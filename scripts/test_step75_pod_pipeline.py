"""
Step 75 test: POD product pipeline integration.
Uses test-double image provider — no real DALL-E API call.
"""
import sys
import uuid
import shutil
import base64
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

print("=" * 60)
print("STEP 75 — POD PIPELINE SERVICE TEST")
print("=" * 60)

from app.core.providers.image_base import BaseImageProvider, ImageGenerationResult
from app.services.image_file_service import DELIVERY_DIR
from app.services.pod_pipeline_service import PODPipelineService

class FakeImageProvider(BaseImageProvider):
    async def generate_image(self, prompt, size="1024x1024", model=None, **kw):
        fake_png = b"\x89PNG\r\n\x1a\n" + b"\x00" * 100
        return ImageGenerationResult(
            b64_data=base64.b64encode(fake_png).decode(),
            provider="fake",
            model="fake-model",
        )

fake = FakeImageProvider()
svc = PODPipelineService(image_provider=fake)

# 1. digital_download product type
print("\n[1] Building product record for digital_download...")
task_id = f"test-{uuid.uuid4().hex[:8]}"
record = svc.build_product_record(
    task_id=task_id,
    product_name="Gratitude Journal Page Pack",
    visual_brief="Soft botanical watercolour, cream background, script headings",
    product_type="digital_download",
)
assert record["ready_for_pod"] is True
assert record["product_type"] == "digital_download"
assert record["design_path"] is not None
design_path = Path(record["design_path"])
assert design_path.exists()
assert design_path.parent == DELIVERY_DIR / task_id
print(f"  design_path: {design_path}")
print(f"  ready_for_pod: {record['ready_for_pod']}")

# 2. pod product type
print("\n[2] Building product record for pod...")
task_id2 = f"test-{uuid.uuid4().hex[:8]}"
fake2 = FakeImageProvider()
svc2 = PODPipelineService(image_provider=fake2)
record2 = svc2.build_product_record(
    task_id=task_id2,
    product_name="Succulent Pattern Tote Bag",
    visual_brief="Flat vector illustration, teal and coral, repeating pattern",
    product_type="pod",
)
assert record2["ready_for_pod"] is True
print(f"  design_path: {record2['design_path']}")

# 3. unsupported type returns ready_for_pod=False
print("\n[3] Unsupported product_type returns ready_for_pod=False...")
task_id3 = f"test-{uuid.uuid4().hex[:8]}"
record3 = svc.build_product_record(
    task_id=task_id3,
    product_name="Physical Widget",
    visual_brief="N/A",
    product_type="physical_other",
)
assert record3["ready_for_pod"] is False
assert record3["design_path"] is None
print(f"  ready_for_pod={record3['ready_for_pod']}, design_path={record3['design_path']}")

# 4. design file is in delivery dir (confirming step 81 can find it)
print("\n[4] Confirming design is in data/images/delivery/ (step 81 location)...")
assert str(DELIVERY_DIR) in str(design_path)
print(f"  Path confirms delivery variant: {design_path.relative_to(DELIVERY_DIR.parent.parent)}")

# Cleanup
shutil.rmtree(str(DELIVERY_DIR / task_id), ignore_errors=True)
shutil.rmtree(str(DELIVERY_DIR / task_id2), ignore_errors=True)

print("\n" + "=" * 60)
print("STEP 75 TEST PASSED — POD pipeline works, test double used (no DALL-E API call, no POD service API call)")
print("=" * 60)
