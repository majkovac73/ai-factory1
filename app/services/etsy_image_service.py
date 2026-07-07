"""
Etsy image pipeline integration — step 73.

Extends EtsyClient with three new operations that are needed once we have
real generated images:

  1. upload_listing_image(listing_id, image_path)
       Attaches a local image file to a draft Etsy listing as a listing photo.
       Etsy endpoint: POST /v3/application/shops/{shop_id}/listings/{listing_id}/images

  2. upload_digital_file(listing_id, file_path)
       Uploads the delivery-ready digital file as the file the buyer actually
       receives after purchase (not the listing photo — a separate endpoint).
       Etsy endpoint: POST /v3/application/shops/{shop_id}/listings/{listing_id}/files

  3. publish_listing(listing_id)
       Flips a draft listing to 'active' (live and publicly sellable).
       Etsy endpoint: PATCH /v3/application/listings/{listing_id}
       Only called when settings.AUTO_PUBLISH_LISTINGS is True.

AUTO_PUBLISH_LISTINGS defaults to False — nothing goes live without Maj
explicitly enabling it in the environment. This is intentional: publishing a
real public listing is a significant action, and this code should not do it
silently the first time it runs.

All three methods share the same auth/header pattern as EtsyClient.create_draft_listing.
"""
import httpx

from app.services.etsy_oauth import get_valid_access_token
from config import settings

ETSY_API_BASE = "https://openapi.etsy.com/v3/application"


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

        files = {"file": (filename, file_bytes, "application/octet-stream")}
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

    async def publish_listing(self, listing_id: str) -> dict:
        """
        Activate a draft listing (make it live and publicly sellable).

        Only called when settings.AUTO_PUBLISH_LISTINGS is True. This setting
        defaults to False — do not enable it until you have reviewed and approved
        the listing content, as publishing creates a real public Etsy listing.

        Args:
            listing_id: Etsy listing ID.

        Returns:
            Updated Etsy listing dict.
        """
        if not settings.AUTO_PUBLISH_LISTINGS:
            return {
                "published": False,
                "reason": "AUTO_PUBLISH_LISTINGS is False — listing left in DRAFT state",
                "listing_id": listing_id,
            }

        access_token = await get_valid_access_token()
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.patch(
                f"{ETSY_API_BASE}/listings/{listing_id}",
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
