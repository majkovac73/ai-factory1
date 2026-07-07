"""
Step 72 test: Image validation and quality checks.
Uses synthetic PNG files — no real DALL-E API call.
"""
import sys
import uuid
import shutil
from pathlib import Path
from io import BytesIO

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

print("=" * 60)
print("STEP 72 — IMAGE VALIDATION SERVICE TEST")
print("=" * 60)

from PIL import Image as PILImage
from app.services.image_validation_service import ImageValidationService, ImageValidationError
from app.services.image_file_service import ImageFileService, LISTING_DIR, DELIVERY_DIR

svc = ImageValidationService()
file_svc = ImageFileService()

def make_png(width: int, height: int) -> bytes:
    img = PILImage.new("RGB", (width, height), color=(200, 200, 200))
    buf = BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()

task_id = f"test-{uuid.uuid4().hex[:8]}"

# 1. Valid listing image (1024x1024)
print("\n[1] Validating a valid 1024x1024 listing image...")
path = file_svc.save_bytes(make_png(1024, 1024), task_id, "listing", "valid_listing.png")
result = svc.validate(path, use_case="listing")
assert result["valid"] is True
assert result["width"] == 1024
print(f"  PASSED: {result['width']}x{result['height']}, use_case=listing")

# 2. Too small (800x800) listing image
print("\n[2] Checking too-small image is rejected...")
path2 = file_svc.save_bytes(make_png(800, 800), task_id, "listing", "small.png")
try:
    svc.validate(path2, use_case="listing")
    print("  FAIL — should have raised ImageValidationError")
    sys.exit(1)
except ImageValidationError as e:
    print(f"  Correctly rejected: {e}")

# 3. Wrong aspect ratio for listing (wide landscape 2:1)
print("\n[3] Checking wrong aspect ratio is rejected...")
path3 = file_svc.save_bytes(make_png(2048, 1024), task_id, "listing", "wide.png")
try:
    svc.validate(path3, use_case="listing")
    print("  FAIL — should have raised ImageValidationError")
    sys.exit(1)
except ImageValidationError as e:
    print(f"  Correctly rejected: {e}")

# 4. Valid Pinterest image — native 2:3 ratio (OpenRouter gemini-3.1-flash-image)
# Previously used 1024x1792 (DALL-E 3 approximation = 4:7). Now model returns true 2:3.
print("\n[4] Validating a valid 1000x1500 Pinterest image (true 2:3 ratio)...")
path4 = file_svc.save_bytes(make_png(1000, 1500), task_id, "listing", "valid_pin.png")
result4 = svc.validate(path4, use_case="pinterest")
assert result4["valid"] is True
print(f"  PASSED: {result4['width']}x{result4['height']}, use_case=pinterest")

# 5. validate_with_retry: succeeds on second attempt
print("\n[5] Testing validate_with_retry (succeeds on 2nd attempt)...")
call_count = [0]
good_path = file_svc.save_bytes(make_png(1024, 1024), task_id, "listing", "retry_good.png")

def gen_fn():
    call_count[0] += 1
    if call_count[0] == 1:
        return file_svc.save_bytes(make_png(800, 800), task_id, "listing", f"retry_{call_count[0]}.png")
    return good_path

path5, result5 = svc.validate_with_retry(gen_fn, use_case="listing", max_attempts=3)
assert call_count[0] == 2
assert result5["valid"] is True
print(f"  Succeeded after {call_count[0]} attempts")

# 6. validate_with_retry: fails after all retries
print("\n[6] Testing validate_with_retry exhaustion...")
def always_bad():
    return file_svc.save_bytes(make_png(500, 500), task_id, "listing", f"bad_{uuid.uuid4().hex[:4]}.png")

try:
    svc.validate_with_retry(always_bad, use_case="listing", max_attempts=2)
    print("  FAIL — should have raised ImageValidationError")
    sys.exit(1)
except ImageValidationError as e:
    print(f"  Correctly exhausted after 2 attempts: {e}")

# Cleanup
shutil.rmtree(str(LISTING_DIR / task_id), ignore_errors=True)
shutil.rmtree(str(DELIVERY_DIR / task_id), ignore_errors=True)

print("\n" + "=" * 60)
print("STEP 72 TEST PASSED — image validation works, test double used (no DALL-E API call)")
print("=" * 60)
