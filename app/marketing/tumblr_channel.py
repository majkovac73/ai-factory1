"""
Tumblr marketing channel — posts a product as a Tumblr photo post using the
Neue Post Format (NPF), confirmed against Tumblr's current API docs (July 2026).

Create endpoint:  POST https://api.tumblr.com/v2/blog/{blog-identifier}/posts
Auth:             Authorization: Bearer <OAuth2 access token>
Body:             multipart/form-data with
                    - a "json" part (application/json) = the NPF post body:
                        {"content": [<blocks>], "tags": "a,b,c", "state": "published"}
                    - one part per uploaded image, whose form-field NAME matches
                      the "identifier" set inside that image block's media object.
                  (This identifier↔form-field mapping is the documented Tumblr
                  mechanism, matched by PyTumblr2's working client.)

Same interface + result shape as PinterestChannel: post(listing) -> {"success",
"external_id", "url", "error"}. Accepts a local image file (image_path — used by
the refresh flow, which pulls already-generated assets), a base64 image, or a
remote URL. A PDF delivery asset is handled by extracting its first page to PNG
(reusing content_quality_service._delivery_image_bytes) — Tumblr photo posts
need a real image, not a PDF.
"""
import asyncio
import base64
import json
import logging
from io import BytesIO
from pathlib import Path

import httpx

from app.marketing.base import MarketingChannel
from app.services.tumblr_oauth import get_valid_access_token
from config import settings

logger = logging.getLogger("ai-factory")

TUMBLR_API_BASE = "https://api.tumblr.com/v2"

_EXT_MIME = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".webp": "image/webp",
}


def _blog_identifier() -> str:
    name = (settings.TUMBLR_BLOG_NAME or "").strip()
    if not name:
        raise ValueError("TUMBLR_BLOG_NAME is not set")
    return name if "." in name else f"{name}.tumblr.com"


def _resolve_image(listing: dict):
    """Return (image_bytes, mime) for the post, or (None, None) if none provided.

    Prefers a local file (image_path), then base64, then a remote URL (fetched).
    A .pdf file is converted to a PNG of its first page.
    """
    image_path = listing.get("image_path")
    if image_path:
        p = Path(image_path)
        if not p.exists():
            raise FileNotFoundError(f"image_path does not exist: {p}")
        if p.suffix.lower() == ".pdf":
            from app.services.content_quality_service import _delivery_image_bytes
            return _delivery_image_bytes(p), "image/png"
        return p.read_bytes(), _EXT_MIME.get(p.suffix.lower(), "image/png")

    image_b64 = listing.get("image_base64")
    if image_b64:
        return base64.b64decode(image_b64), listing.get("image_content_type", "image/png")

    image_url = listing.get("image_url")
    if image_url:
        resp = httpx.get(image_url, timeout=60.0, follow_redirects=True)
        resp.raise_for_status()
        mime = resp.headers.get("content-type", "image/png").split(";")[0]
        return resp.content, mime
    return None, None


STORE_IN_BIO_FALLBACK = "🛍️ Link to our Etsy store in bio"
SHOP_LINK_LABEL = "🛍️ Shop this listing"
SHOP_LINK_ANCHOR = "Shop this listing"


def _utf16_len(s: str) -> int:
    """Length of s in UTF-16 code units — the unit NPF formatting ranges use.

    Tumblr's NPF `start`/`end` inline-formatting offsets are counted in UTF-16
    code units (like JavaScript string indices), so a leading emoji shifts the
    anchor's real offset past its visible character count.
    """
    return len(s.encode("utf-16-le")) // 2


def _build_caption_blocks(listing: dict) -> list:
    """Build the NPF text blocks for a post.

    Title and description become plain text blocks. The shop line becomes a
    text block whose label is a real hyperlink via NPF `link` formatting — so
    buyers get an actual clickable link in the post, not a raw URL string. When
    no listing link can be resolved, falls back to a "store in bio" pointer so
    the post is never a dead end.
    """
    title = (listing.get("title") or "").strip()
    description = (listing.get("description") or "").strip()
    link = (listing.get("listing_url") or listing.get("product_url") or "").strip()

    blocks = []
    if title:
        blocks.append({"type": "text", "text": title})
    if description:
        blocks.append({"type": "text", "text": description[:450]})

    if link:
        # Turn only the human-readable anchor into a hyperlink; NPF formatting
        # ranges are UTF-16 code-unit offsets, so compute them accordingly.
        idx = SHOP_LINK_LABEL.index(SHOP_LINK_ANCHOR)
        start = _utf16_len(SHOP_LINK_LABEL[:idx])
        end = start + _utf16_len(SHOP_LINK_ANCHOR)
        blocks.append({
            "type": "text",
            "text": SHOP_LINK_LABEL,
            "formatting": [{"start": start, "end": end, "type": "link", "url": link}],
        })
    else:
        blocks.append({"type": "text", "text": STORE_IN_BIO_FALLBACK})
    return blocks


def _build_tags(listing: dict) -> str:
    keywords = listing.get("keywords") or []
    if isinstance(keywords, str):
        keywords = [k.strip() for k in keywords.split(",")]
    # Tumblr expects a comma-separated string; cap to a sane number.
    clean = [str(k).strip() for k in keywords if str(k).strip()][:20]
    return ",".join(clean)


class TumblrChannel(MarketingChannel):
    """Posts a product to Tumblr as an NPF photo post."""

    name = "tumblr"

    def post(self, listing: dict) -> dict:
        return asyncio.run(self._post_async(listing))

    async def _post_async(self, listing: dict) -> dict:
        try:
            access_token = await get_valid_access_token()
            blog = _blog_identifier()
        except Exception as e:
            return {"success": False, "external_id": None, "url": None, "error": str(e)}

        try:
            image_bytes, mime = _resolve_image(listing)
        except Exception as e:
            return {"success": False, "external_id": None, "url": None, "error": f"image resolution failed: {e}"}

        caption_blocks = _build_caption_blocks(listing)
        tags = _build_tags(listing)

        content = []
        files = None
        if image_bytes:
            identifier = "file0"
            image_block = {"type": "image", "media": [{"type": mime, "identifier": identifier}]}
            title = (listing.get("title") or "").strip()
            if title:
                image_block["alt_text"] = title[:200]
            content.append(image_block)
            # Determine dimensions where possible (optional but nice for NPF).
            try:
                from PIL import Image as PILImage
                with PILImage.open(BytesIO(image_bytes)) as im:
                    w, h = im.size
                image_block["media"][0]["width"] = w
                image_block["media"][0]["height"] = h
            except Exception:
                pass
            files = {identifier: (f"{identifier}.{mime.split('/')[-1]}", image_bytes, mime)}

        content.extend(caption_blocks)

        if not content:
            return {"success": False, "external_id": None, "url": None, "error": "nothing to post (no image and no caption)"}

        body = {"content": content, "state": "published"}
        if tags:
            body["tags"] = tags

        # multipart/form-data: a "json" part holds the post body, plus one part
        # per image whose field name == the media identifier.
        form = {"json": (None, json.dumps(body), "application/json")}
        if files:
            form.update(files)

        try:
            async with httpx.AsyncClient(timeout=60.0) as client:
                response = await client.post(
                    f"{TUMBLR_API_BASE}/blog/{blog}/posts",
                    headers={"Authorization": f"Bearer {access_token}"},
                    files=form,
                )
                response.raise_for_status()
                data = response.json()
        except httpx.HTTPStatusError as e:
            body_text = e.response.text[:300] if e.response is not None else ""
            return {"success": False, "external_id": None, "url": None, "error": f"Tumblr API error {e.response.status_code}: {body_text}"}
        except Exception as e:
            return {"success": False, "external_id": None, "url": None, "error": str(e)}

        resp = data.get("response", {}) if isinstance(data, dict) else {}
        post_id = resp.get("id_string") or (str(resp.get("id")) if resp.get("id") is not None else None)
        url = f"https://{blog}/post/{post_id}" if post_id else None

        return {"success": True, "external_id": post_id, "url": url, "error": None}
