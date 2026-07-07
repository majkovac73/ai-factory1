"""
Step 68 test: Image storage and file service.
Uses synthetic bytes (test double) — no real DALL-E API call.
"""
import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

print("=" * 60)
print("STEP 68 — IMAGE STORAGE AND FILE SERVICE TEST")
print("=" * 60)

from app.services.image_file_service import ImageFileService, LISTING_DIR, DELIVERY_DIR

svc = ImageFileService()
task_id = f"test-{uuid.uuid4().hex[:8]}"
dummy_bytes = b"\x89PNG\r\n\x1a\n" + b"\x00" * 100  # minimal fake PNG header

# 1. Save a listing variant
print("\n[1] Saving listing variant...")
listing_path = svc.save_bytes(dummy_bytes, task_id, "listing", "hero.png")
assert listing_path.exists(), "Listing file not saved"
assert listing_path.parent == LISTING_DIR / task_id
assert listing_path.name == "hero.png"
print(f"  Saved: {listing_path}")

# 2. Save a delivery variant
print("\n[2] Saving delivery variant...")
delivery_path = svc.save_bytes(dummy_bytes, task_id, "delivery", "product.png")
assert delivery_path.exists(), "Delivery file not saved"
assert delivery_path.parent == DELIVERY_DIR / task_id
assert delivery_path.name == "product.png"
print(f"  Saved: {delivery_path}")

# 3. list_assets returns correct files
print("\n[3] Listing assets...")
listing_assets = svc.list_assets(task_id, "listing")
delivery_assets = svc.list_assets(task_id, "delivery")
assert len(listing_assets) == 1
assert len(delivery_assets) == 1
print(f"  Listing assets  : {[p.name for p in listing_assets]}")
print(f"  Delivery assets : {[p.name for p in delivery_assets]}")

# 4. listing_dir / delivery_dir helpers
print("\n[4] Directory helpers...")
assert svc.listing_dir(task_id) == LISTING_DIR / task_id
assert svc.delivery_dir(task_id) == DELIVERY_DIR / task_id
print("  listing_dir and delivery_dir return correct paths")

# 5. save_from_b64
print("\n[5] save_from_b64...")
import base64
b64 = base64.b64encode(dummy_bytes).decode()
b64_path = svc.save_from_b64(b64, task_id, "delivery", "b64_product.png")
assert b64_path.exists()
assert b64_path.read_bytes() == dummy_bytes
print(f"  Saved b64 file: {b64_path.name}")

# Cleanup
import shutil
shutil.rmtree(str(LISTING_DIR / task_id), ignore_errors=True)
shutil.rmtree(str(DELIVERY_DIR / task_id), ignore_errors=True)
print("\n  Cleaned up test directories")

print("\n" + "=" * 60)
print("STEP 68 TEST PASSED — image storage confirmed, test double used (no DALL-E API call)")
print("=" * 60)
