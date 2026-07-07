"""
Image validation and quality checks — step 72.

Automated QA gate on generated images, following the same
retry → repair → retry pattern used for text schema validation.
Checks are objective and deterministic (no human judgment required):
  - File is a valid, readable image (not corrupted / truncated)
  - Resolution meets the minimum for the given use case
  - Aspect ratio matches the expected ratio for the use case (±5% tolerance)
  - File size is non-trivial (guards against blank/empty files)

Use cases and their constraints:
  'listing'   : Etsy listing photo — minimum 1000x1000 px, square (1:1)
  'delivery'  : Digital download / POD artifact — minimum 1000x1000 px, square (1:1)
  'pinterest' : Pinterest pin — minimum 600x900 px, portrait ~2:3

When validation fails, ImageValidationError is raised with a human-readable
reason so the caller's retry loop knows what went wrong.
"""
from pathlib import Path
from typing import Optional, Tuple

try:
    from PIL import Image as PILImage
    _PILLOW_AVAILABLE = True
except ImportError:
    _PILLOW_AVAILABLE = False


class ImageValidationError(Exception):
    """Raised when a generated image fails an automated QA check."""


# (min_width, min_height, expected_ratio_w, expected_ratio_h, ratio_tolerance)
USE_CASE_RULES = {
    "listing": {
        "min_width": 1000,
        "min_height": 1000,
        "expected_ratio": (1, 1),
        "ratio_tolerance": 0.10,
    },
    "delivery": {
        "min_width": 1000,
        "min_height": 1000,
        "expected_ratio": (1, 1),
        "ratio_tolerance": 0.10,
    },
    "pinterest": {
        "min_width": 600,
        "min_height": 900,
        "expected_ratio": (2, 3),  # OpenRouter gemini-3.1-flash-image natively supports 2:3; replaces old DALL-E 4:7 workaround
        "ratio_tolerance": 0.10,
    },
}
MIN_FILE_BYTES = 1_000  # below this, the file is almost certainly blank/empty


class ImageValidationService:
    """
    Validates a local image file against automated quality checks for its
    intended use case. Raises ImageValidationError on any failure; the
    caller should treat this as a signal to regenerate.
    """

    def validate(self, path: Path, use_case: str = "listing") -> dict:
        """
        Validate a saved image file.

        Args:
            path: Filesystem path to the image file.
            use_case: 'listing', 'delivery', or 'pinterest'.

        Returns:
            Dict with validation details (width, height, file_size, etc.).

        Raises:
            ImageValidationError: If any check fails.
        """
        if not path.exists():
            raise ImageValidationError(f"Image file not found: {path}")

        file_size = path.stat().st_size
        if file_size < MIN_FILE_BYTES:
            raise ImageValidationError(
                f"Image file too small ({file_size} bytes) — likely blank or corrupted."
            )

        if not _PILLOW_AVAILABLE:
            return {
                "path": str(path),
                "use_case": use_case,
                "file_size": file_size,
                "note": "Pillow not available — pixel-level checks skipped",
            }

        try:
            with PILImage.open(path) as img:
                img.verify()
        except Exception as e:
            raise ImageValidationError(f"Image file is corrupted or unreadable: {e}")

        with PILImage.open(path) as img:
            width, height = img.size

        rules = USE_CASE_RULES.get(use_case, USE_CASE_RULES["listing"])

        if width < rules["min_width"] or height < rules["min_height"]:
            raise ImageValidationError(
                f"Resolution too low for '{use_case}': got {width}x{height}, "
                f"need at least {rules['min_width']}x{rules['min_height']}."
            )

        ew, eh = rules["expected_ratio"]
        expected_ratio = ew / eh
        actual_ratio = width / height
        tolerance = rules["ratio_tolerance"]
        if abs(actual_ratio - expected_ratio) > tolerance * expected_ratio:
            raise ImageValidationError(
                f"Aspect ratio mismatch for '{use_case}': image is {width}x{height} "
                f"(ratio {actual_ratio:.3f}), expected ~{expected_ratio:.3f} "
                f"({ew}:{eh}) within ±{int(tolerance*100)}%."
            )

        return {
            "path": str(path),
            "use_case": use_case,
            "width": width,
            "height": height,
            "file_size": file_size,
            "valid": True,
        }

    def validate_with_retry(
        self,
        generate_fn,
        use_case: str = "listing",
        max_attempts: int = 3,
    ) -> Tuple[Path, dict]:
        """
        Retry pattern: call generate_fn() up to max_attempts times,
        validating the result each time. Returns (path, validation_result)
        on first success; raises the last ImageValidationError on exhaustion.

        Args:
            generate_fn: Zero-arg callable that returns a Path to a new image.
            use_case: Validation use-case name.
            max_attempts: Maximum generation attempts before giving up.

        Returns:
            (Path, validation_result_dict)

        Raises:
            ImageValidationError: If all attempts fail validation.
        """
        last_error: Optional[ImageValidationError] = None
        for attempt in range(1, max_attempts + 1):
            path = generate_fn()
            try:
                result = self.validate(path, use_case)
                return path, result
            except ImageValidationError as e:
                last_error = e
                if path.exists():
                    path.unlink(missing_ok=True)
        raise ImageValidationError(
            f"Image validation failed after {max_attempts} attempts. "
            f"Last error: {last_error}"
        )
