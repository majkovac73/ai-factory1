"""
Step 76 test: Image catalog and asset management.
Uses SQLite (the real DB) for catalog queries. No DALL-E API call.
"""
import sys
import uuid
import shutil
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

print("=" * 60)
print("STEP 76 — IMAGE CATALOG SERVICE TEST")
print("=" * 60)

# Ensure DB tables are created (runs migrations in a controlled way)
from app.db.database import Base, engine
from app.models.image_asset import ImageAsset as _IA  # noqa: ensure model is registered
Base.metadata.create_all(bind=engine)
print("  DB tables created (or already exist)")

from app.services.image_catalog_service import ImageCatalogService
from app.services.image_file_service import ImageFileService, LISTING_DIR, DELIVERY_DIR

cat = ImageCatalogService()
file_svc = ImageFileService()

task_id = f"test-{uuid.uuid4().hex[:8]}"
task_id2 = f"test-{uuid.uuid4().hex[:8]}"
dummy_bytes = b"\x89PNG\r\n\x1a\n" + b"\x00" * 50

# 1. Register a listing asset
print(f"\n[1] Registering a listing asset for task {task_id}...")
hero_path = file_svc.save_bytes(dummy_bytes, task_id, "listing", "hero.png")
record1 = cat.register(
    task_id=task_id,
    local_path=str(hero_path),
    variant="listing",
    use_case="listing",
    agent="ProductImageAgent",
    provider="fake",
    model="fake-model",
)
assert record1.id is not None
assert record1.task_id == task_id
assert record1.variant == "listing"
print(f"  Registered ID: {record1.id}")

# 2. Register a delivery asset
print("\n[2] Registering a delivery asset...")
design_path = file_svc.save_bytes(dummy_bytes, task_id, "delivery", "design.png")
record2 = cat.register(
    task_id=task_id,
    local_path=str(design_path),
    variant="delivery",
    use_case="delivery",
    agent="PODDesignAgent",
    provider="fake",
    model="fake-model",
)
assert record2.variant == "delivery"
print(f"  Registered ID: {record2.id}")

# 3. Register a Pinterest asset for a different task
print("\n[3] Registering a Pinterest asset...")
pin_path = file_svc.save_bytes(dummy_bytes, task_id2, "listing", "pin.png")
record3 = cat.register(
    task_id=task_id2,
    local_path=str(pin_path),
    variant="listing",
    use_case="pinterest",
    agent="SocialImageAgent",
)
print(f"  Registered ID: {record3.id}")

# 4. get_by_task returns correct records
print("\n[4] get_by_task returns all assets for the task...")
assets = cat.get_by_task(task_id)
assert len(assets) == 2, f"Expected 2 assets, got {len(assets)}"
variants = {a.variant for a in assets}
assert variants == {"listing", "delivery"}
print(f"  Found {len(assets)} assets: variants={sorted(variants)}")

# 5. get_delivery_asset returns the delivery variant
print("\n[5] get_delivery_asset returns delivery variant...")
delivery = cat.get_delivery_asset(task_id)
assert delivery is not None
assert delivery.variant == "delivery"
assert delivery.local_path == str(design_path)
print(f"  Delivery asset: {Path(delivery.local_path).name}")

# 6. get_listing_assets returns only listing variants
print("\n[6] get_listing_assets returns only listing variants...")
listing_assets = cat.get_listing_assets(task_id)
assert len(listing_assets) == 1
assert listing_assets[0].use_case == "listing"
print(f"  Listing assets: {[Path(a.local_path).name for a in listing_assets]}")

# 7. attach_listing updates the record
print("\n[7] attach_listing wires an Etsy listing ID...")
ok = cat.attach_listing(str(hero_path), "etsy_listing_99999")
assert ok is True
refreshed = cat.get_listing_assets(task_id)
assert refreshed[0].listing_id == "etsy_listing_99999"
print(f"  listing_id set to: {refreshed[0].listing_id}")

# 8. get_by_listing returns record by listing_id
print("\n[8] get_by_listing returns assets by Etsy listing ID...")
by_listing = cat.get_by_listing("etsy_listing_99999")
assert len(by_listing) >= 1
print(f"  Found {len(by_listing)} asset(s) for listing_id=etsy_listing_99999")

# 9. Idempotent re-registration (same path → update, not insert)
print("\n[9] Re-registering same path is idempotent...")
count_before = len(cat.list_all())
cat.register(
    task_id=task_id,
    local_path=str(hero_path),
    variant="listing",
    use_case="listing",
    agent="ProductImageAgent",
    provider="dalle3",
    model="dall-e-3",
)
count_after = len(cat.list_all())
assert count_after == count_before, "Re-registration should not insert a new row"
updated = [a for a in cat.get_by_task(task_id) if "hero" in a.local_path]
assert updated[0].provider == "dalle3"
print(f"  Row count unchanged ({count_before}); provider updated to 'dalle3'")

# Cleanup
shutil.rmtree(str(LISTING_DIR / task_id), ignore_errors=True)
shutil.rmtree(str(DELIVERY_DIR / task_id), ignore_errors=True)
shutil.rmtree(str(LISTING_DIR / task_id2), ignore_errors=True)

print("\n" + "=" * 60)
print("STEP 76 TEST PASSED — image catalog works (real SQLite DB), test double used (no DALL-E API call)")
print("=" * 60)
