"""
Image file service — local filesystem storage for generated image assets.

Storage layout (all paths relative to project root):
  data/images/listing/{task_id}/{filename}   — listing/preview variant
      Shown publicly on Etsy and Pinterest (scene-composited; watermarked for
      WATERMARK_FORMATS such as coloring pages / wallpapers — see MockupService).
  data/images/delivery/{task_id}/{filename}  — delivery/print-ready variant
      The actual file a paying customer receives (digital download), or
      that a future POD service (step 81) will submit for printing.

Local-first by design: this is a single-operator project with no cloud
storage layer yet. If cloud storage is added later, only this service
needs to change — callers store/retrieve paths, not bytes.
"""
import base64
import uuid
from pathlib import Path
from typing import Optional

import httpx

import os as _os

PROJECT_ROOT = Path(__file__).resolve().parents[2]
# IMAGE_STORAGE_ROOT overrides the local default (Railway: /data/images).
_image_root = _os.getenv("IMAGE_STORAGE_ROOT")
IMAGES_DIR = Path(_image_root) if _image_root else PROJECT_ROOT / "data" / "images"
LISTING_DIR = IMAGES_DIR / "listing"
DELIVERY_DIR = IMAGES_DIR / "delivery"


def _ensure_dirs(task_id: str):
    (LISTING_DIR / task_id).mkdir(parents=True, exist_ok=True)
    (DELIVERY_DIR / task_id).mkdir(parents=True, exist_ok=True)


class ImageFileService:
    """
    Saves generated image assets to the local filesystem under data/images/.
    Maintains two distinct variants per asset:
      - listing  : preview shown on Etsy/Pinterest
      - delivery : high-quality file delivered to the customer or POD service
    """

    def save_from_url(
        self,
        url: str,
        task_id: str,
        variant: str,
        filename: Optional[str] = None,
    ) -> Path:
        """
        Download an image from a URL and save it locally.

        Args:
            url: Remote image URL (e.g. from DALL-E 3 response).
            task_id: Task this image belongs to (used as subdirectory).
            variant: 'listing' or 'delivery'.
            filename: Override filename; auto-generated if not provided.

        Returns:
            Absolute Path to the saved file.
        """
        _ensure_dirs(task_id)
        base_dir = LISTING_DIR if variant == "listing" else DELIVERY_DIR
        fname = filename or f"{uuid.uuid4().hex}.png"
        dest = base_dir / task_id / fname

        response = httpx.get(url, timeout=60.0, follow_redirects=True)
        response.raise_for_status()
        dest.write_bytes(response.content)
        return dest

    def save_from_b64(
        self,
        b64_data: str,
        task_id: str,
        variant: str,
        filename: Optional[str] = None,
    ) -> Path:
        """
        Decode a base64-encoded image and save it locally.

        Args:
            b64_data: Base64-encoded PNG/JPEG bytes (no data URI prefix needed).
            task_id: Task this image belongs to.
            variant: 'listing' or 'delivery'.
            filename: Override filename; auto-generated if not provided.

        Returns:
            Absolute Path to the saved file.
        """
        _ensure_dirs(task_id)
        base_dir = LISTING_DIR if variant == "listing" else DELIVERY_DIR
        fname = filename or f"{uuid.uuid4().hex}.png"
        dest = base_dir / task_id / fname

        raw = base64.b64decode(b64_data)
        dest.write_bytes(raw)
        return dest

    def save_from_result(
        self,
        result,
        task_id: str,
        variant: str,
        filename: Optional[str] = None,
    ) -> Path:
        """
        Save an ImageGenerationResult (from BaseImageProvider) to disk.
        Prefers URL download; falls back to b64 if only b64 is available.

        Args:
            result: ImageGenerationResult instance.
            task_id: Task this image belongs to.
            variant: 'listing' or 'delivery'.
            filename: Override filename.

        Returns:
            Absolute Path to the saved file.
        """
        if result.url:
            return self.save_from_url(result.url, task_id, variant, filename)
        if result.b64_data:
            return self.save_from_b64(result.b64_data, task_id, variant, filename)
        raise ValueError("ImageGenerationResult has neither url nor b64_data")

    def save_bytes(
        self,
        data: bytes,
        task_id: str,
        variant: str,
        filename: Optional[str] = None,
    ) -> Path:
        """Save raw bytes (e.g. from a test double or local render) to disk."""
        _ensure_dirs(task_id)
        base_dir = LISTING_DIR if variant == "listing" else DELIVERY_DIR
        fname = filename or f"{uuid.uuid4().hex}.png"
        dest = base_dir / task_id / fname
        dest.write_bytes(data)
        return dest

    def listing_dir(self, task_id: str) -> Path:
        return LISTING_DIR / task_id

    def delivery_dir(self, task_id: str) -> Path:
        return DELIVERY_DIR / task_id

    def list_assets(self, task_id: str, variant: str) -> list:
        """Return a sorted list of Paths for all saved assets of the given variant."""
        base_dir = LISTING_DIR if variant == "listing" else DELIVERY_DIR
        d = base_dir / task_id
        if not d.exists():
            return []
        return sorted(d.iterdir())
