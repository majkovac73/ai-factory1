"""
Etsy image pipeline integration — step 73 (extended in step 92 with a
publish-state readback fix and a digital-file readback method).

Extends EtsyClient with operations needed once we have real generated
images/files:

  1. upload_listing_image(listing_id, image_path)
       Attaches a local image file to a draft Etsy listing as a listing photo.
       Etsy endpoint: POST /v3/application/shops/{shop_id}/listings/{listing_id}/images

  2. upload_digital_file(listing_id, file_path)
       Uploads the delivery-ready digital file as the file the buyer actually
       receives after purchase (not the listing photo — a separate endpoint).
       Etsy endpoint: POST /v3/application/shops/{shop_id}/listings/{listing_id}/files

  3. publish_listing(listing_id)
       Flips a draft listing to 'active' (live and publicly sellable).
       Etsy endpoint: PATCH /v3/application/shops/{shop_id}/listings/{listing_id}
       Only called when settings.AUTO_PUBLISH_LISTINGS is True.

       Step 92 fix: a 200 OK response from this endpoint does NOT guarantee
       the state actually transitioned. Confirmed live in production
       (task fb66a81a, listing 4534427807): the PATCH returned 200 but the
       listing's own `state` field in that same response body stayed
       "edit" — Etsy accepted the request without erroring but the
       activation didn't take effect, almost certainly a brief
       eventual-consistency lag immediately following the image/file
       uploads that happen in the same call sequence just before this one.
       A manual re-invocation of the identical PATCH moments later DID
       transition it to "active". This method now checks the response
       BODY's `state` field (not just the HTTP status) and retries once
       after a short delay before reporting failure.

  4. get_listing_images(listing_id) / get_listing_files(listing_id)
       Readback verification: confirm images/files are REALLY attached
       rather than trusting the upload response alone.
       Etsy endpoints: GET /v3/application/listings/{listing_id}/images
                        GET /v3/application/shops/{shop_id}/listings/{listing_id}/files
       (Per Etsy's published OpenAPI spec, getAllListingFiles IS
       shop-scoped even though the images equivalent is not — verified
       against the real spec, not assumed, after this project was burned
       by an assumed-wrong endpoint shape once already.)

AUTO_PUBLISH_LISTINGS defaults to False — nothing goes live without Maj
explicitly enabling it in the environment. This is intentional: publishing a
real public listing is a significant action, and this code should not do it
silently the first time it runs.

All methods share the same auth/header pattern as EtsyClient.create_draft_listing.
"""
import asyncio
import mimetypes
import httpx

from app.services.etsy_oauth import get_valid_access_token
from config import settings

ETSY_API_BASE = "https://openapi.etsy.com/v3/application"

PUBLISH_RETRY_DELAY_SECONDS = 2

# Content-type Etsy stores for a file it can't recognise. A file stored with
# this type IS attached (getAllListingFiles counts it) but Etsy's listing
# editor will NOT display/render it — confirmed live against production
# listing 4534427807, whose design.png was uploaded as octet-stream and never
# appeared in the editor, versus a manually-made listing whose file carried a
# real MIME type and displayed correctly. Never send this for a file whose
# real type we can determine.
GENERIC_BINARY_CONTENT_TYPE = "application/octet-stream"


def _guess_content_type(file_path: str, filename: str) -> str:
    """
    Resolve a real MIME type from the file's extension, falling back to the
    generic binary type only when the extension is genuinely unknown. Etsy
    stores exactly what we send as the multipart content-type, and its editor
    only renders files with a recognised type — so sending image/png for a
    .png (rather than octet-stream) is what makes the uploaded file actually
    display for the buyer/seller.
    """
    guess = mimetypes.guess_type(str(file_path))[0] or mimetypes.guess_type(filename)[0]
    return guess or GENERIC_BINARY_CONTENT_TYPE


class EtsyImageService:
    """
    Handles the image-and-file attachment phase of the Etsy listing pipeline.
    Called after a draft listing has been created by EtsyClient.
    """

    def _api_key_header(self) -> str:
        return f"{settings.ETSY_API_KEY}:{settings.ETSY_SHARED_SECRET}"

    async def upload_listing_image(
        self, listing_id: str, image_path: str
    ) -> dict:
        """
        Upload a local image file as a listing photo on an existing Etsy listing.

        Args:
            listing_id: Etsy listing ID (string or int).
            image_path: Absolute path to the image file on disk.

        Returns:
            Etsy API response dict for the uploaded image.
        """
        access_token = await get_valid_access_token()
        with open(image_path, "rb") as f:
            image_bytes = f.read()

        filename = str(image_path).split("\\")[-1].split("/")[-1]
        files = {"image": (filename, image_bytes, "image/png")}

        async with httpx.AsyncClient(timeout=60.0) as client:
            response = await client.post(
                f"{ETSY_API_BASE}/shops/{settings.ETSY_SHOP_ID}/listings/{listing_id}/images",
                headers={
                    "Authorization": f"Bearer {access_token}",
                    "x-api-key": self._api_key_header(),
                },
                files=files,
            )
            if response.status_code >= 400:
                raise RuntimeError(
                    f"Etsy image upload error {response.status_code}: {response.text}"
                )
            return response.json()

    async def upload_digital_file(
        self, listing_id: str, file_path: str, display_name: str = None
    ) -> dict:
        """
        Upload the delivery-ready file as the digital download file for a listing.

        This is a SEPARATE Etsy endpoint from the image upload endpoint above —
        digital-file uploads go to /listings/{id}/files, not /listings/{id}/images.

        Args:
            listing_id: Etsy listing ID.
            file_path: Absolute path to the delivery-ready file (PNG/PDF).
            display_name: Optional filename shown to the buyer on download.

        Returns:
            Etsy API response dict for the uploaded file.
        """
        access_token = await get_valid_access_token()
        filename = display_name or (str(file_path).split("\\")[-1].split("/")[-1])

        with open(file_path, "rb") as f:
            file_bytes = f.read()

        # Send the file's REAL MIME type (image/png, application/pdf, ...), not
        # a hardcoded application/octet-stream. Etsy stores what we send and its
        # editor only displays files with a recognised type — sending
        # octet-stream is what caused the "file attached but invisible in the
        # editor" bug on listing 4534427807.
        content_type = _guess_content_type(file_path, filename)
        files = {"file": (filename, file_bytes, content_type)}
        data = {"name": filename, "rank": 1}

        async with httpx.AsyncClient(timeout=60.0) as client:
            response = await client.post(
                f"{ETSY_API_BASE}/shops/{settings.ETSY_SHOP_ID}/listings/{listing_id}/files",
                headers={
                    "Authorization": f"Bearer {access_token}",
                    "x-api-key": self._api_key_header(),
                },
                files=files,
                data=data,
            )
            if response.status_code >= 400:
                raise RuntimeError(
                    f"Etsy digital file upload error {response.status_code}: {response.text}"
                )
            return response.json()

    async def delete_listing_file(self, listing_id: str, listing_file_id: str) -> bool:
        """
        Delete a single digital file from a listing. Etsy endpoint:
        DELETE /v3/application/shops/{shop_id}/listings/{listing_id}/files/{listing_file_id}

        CAUTION (per Etsy's own docs): deleting the FINAL file of a digital
        listing converts it back into a physical listing. Callers replacing a
        file must upload the replacement FIRST, then delete the old one, so the
        file count never passes through zero.
        """
        access_token = await get_valid_access_token()
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.delete(
                f"{ETSY_API_BASE}/shops/{settings.ETSY_SHOP_ID}/listings/{listing_id}/files/{listing_file_id}",
                headers={
                    "Authorization": f"Bearer {access_token}",
                    "x-api-key": self._api_key_header(),
                },
            )
            if response.status_code >= 400:
                raise RuntimeError(
                    f"Etsy delete listing file error {response.status_code}: {response.text}"
                )
            return True

    async def _patch_listing_state_active(self, listing_id: str) -> dict:
        access_token = await get_valid_access_token()
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.patch(
                f"{ETSY_API_BASE}/shops/{settings.ETSY_SHOP_ID}/listings/{listing_id}",
                headers={
                    "Authorization": f"Bearer {access_token}",
                    "x-api-key": self._api_key_header(),
                },
                json={"state": "active"},
            )
            if response.status_code >= 400:
                raise RuntimeError(
                    f"Etsy publish listing error {response.status_code}: {response.text}"
                )
            return response.json()

    async def publish_listing(self, listing_id: str) -> dict:
        """
        Activate a draft listing (make it live and publicly sellable).

        Only called when settings.AUTO_PUBLISH_LISTINGS is True. This setting
        defaults to False — do not enable it until you have reviewed and approved
        the listing content, as publishing creates a real public Etsy listing.

        A 200 OK response does NOT guarantee the state actually transitioned
        to "active" — confirmed live in production, where the PATCH returned
        200 but the listing stayed in "edit" state (see module docstring).
        This checks the response body's real `state` field and retries once
        after a short delay to absorb the observed propagation lag, rather
        than trusting the HTTP status code alone.

        Args:
            listing_id: Etsy listing ID.

        Returns:
            The Etsy listing dict, with an added "published" bool reflecting
            whether `state` is actually "active" (not just whether the call
            didn't error).
        """
        if not settings.AUTO_PUBLISH_LISTINGS:
            return {
                "published": False,
                "reason": "AUTO_PUBLISH_LISTINGS is False — listing left in DRAFT state",
                "listing_id": listing_id,
            }

        result = await self._patch_listing_state_active(listing_id)
        if result.get("state") == "active":
            return {**result, "published": True}

        await asyncio.sleep(PUBLISH_RETRY_DELAY_SECONDS)
        result = await self._patch_listing_state_active(listing_id)
        return {**result, "published": result.get("state") == "active"}

    async def get_listing_images(self, listing_id: str) -> list:
        """
        Readback verification (step 91): confirm images are really attached
        to a listing rather than trusting upload_listing_image()'s response
        alone. Etsy endpoint: GET /v3/application/listings/{listing_id}/images
        """
        access_token = await get_valid_access_token()
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.get(
                f"{ETSY_API_BASE}/listings/{listing_id}/images",
                headers={
                    "Authorization": f"Bearer {access_token}",
                    "x-api-key": self._api_key_header(),
                },
            )
            if response.status_code >= 400:
                raise RuntimeError(
                    f"Etsy get listing images error {response.status_code}: {response.text}"
                )
            data = response.json()
            return data.get("results", [])

    async def get_listing_files(self, listing_id: str) -> list:
        """
        Readback verification (step 92): confirm the digital download file
        is really attached rather than trusting upload_digital_file()'s
        response alone. Per Etsy's published OpenAPI spec, getAllListingFiles
        is shop-scoped (unlike the images equivalent) — verified against the
        real spec rather than assumed.
        Etsy endpoint: GET /v3/application/shops/{shop_id}/listings/{listing_id}/files
        """
        access_token = await get_valid_access_token()
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.get(
                f"{ETSY_API_BASE}/shops/{settings.ETSY_SHOP_ID}/listings/{listing_id}/files",
                headers={
                    "Authorization": f"Bearer {access_token}",
                    "x-api-key": self._api_key_header(),
                },
            )
            if response.status_code >= 400:
                raise RuntimeError(
                    f"Etsy get listing files error {response.status_code}: {response.text}"
                )
            data = response.json()
            return data.get("results", [])

    async def attach_images_and_publish(
        self,
        listing_id: str,
        listing_image_paths: list,
        digital_file_path: str = None,
    ) -> dict:
        """
        Orchestrate the full image-attachment sequence:
          1. Upload each listing image (hero, lifestyle, etc.)
          2. Upload the digital delivery file if product_type is digital_download
          3. Publish the listing if AUTO_PUBLISH_LISTINGS is True

        Args:
            listing_id: Etsy listing ID.
            listing_image_paths: List of local paths to listing images.
            digital_file_path: Path to the delivery-ready file, or None.

        Returns:
            Summary dict with upload results and publish status.
        """
        uploaded_images = []
        for img_path in listing_image_paths:
            try:
                r = await self.upload_listing_image(listing_id, str(img_path))
                uploaded_images.append({"path": str(img_path), "result": r})
            except Exception as e:
                uploaded_images.append({"path": str(img_path), "error": str(e)})

        digital_upload = None
        if digital_file_path:
            try:
                digital_upload = await self.upload_digital_file(listing_id, str(digital_file_path))
            except Exception as e:
                digital_upload = {"error": str(e)}

        publish_result = await self.publish_listing(listing_id)

        return {
            "listing_id": listing_id,
            "uploaded_images": uploaded_images,
            "digital_upload": digital_upload,
            "publish_result": publish_result,
        }
