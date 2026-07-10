"""
Step 102 / P0-4 + P0-5 + P0-12 test — POD margin math, single variant,
curated t-shirt blueprint selection with the real concept.

  [1] margin math: price = ceil((cost+shipping+0.20+profit)/(1-fee)) whole-dollar.
  [2] single-variant picker prefers a neutral L/M variant, else a middle one.
  [3] variant cost is read from the product readback.
  [4] create_product_for_task: curates catalog to TEE blueprints, passes the
      REAL concept to the selector, creates ONE variant, computes a margin-safe
      price that COVERS cost+fees, and persists cost/price/variant_title.

Usage: python scripts/test_step102_pod_margin.py
"""
import math
import os
import sys
import tempfile
from unittest.mock import MagicMock

os.environ["DATABASE_PATH"] = os.path.join(tempfile.mkdtemp(), "pod.db")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.db.database import Base, engine
from app.models.pod_product import PODProduct  # noqa: F401
from config import settings
from app.services.pod_fulfillment_service import PODFulfillmentService

failures = []


def check(name, cond):
    print(f"[{'PASS' if cond else 'FAIL'}] {name}")
    if not cond:
        failures.append(name)


Base.metadata.create_all(bind=engine)

# [1] margin math covers cost + all fees + profit
cost_cents = 1200  # $12 tee base
price_cents = PODFulfillmentService._pod_price_cents_from_cost(cost_cents)
price = price_cents / 100.0
# must strictly exceed cost + shipping + fee + listing fee (i.e. real profit > 0)
etsy_fee = price * settings.POD_ETSY_FEE_FRACTION
net = price - etsy_fee - 0.20 - settings.POD_SHIPPING_ESTIMATE_USD - (cost_cents / 100.0)
check("1 margin-safe price yields positive profit", net > 0)
check("1 profit ~= target (>= target_profit - $1 rounding)", net >= settings.POD_TARGET_PROFIT_USD - 1.0)
check("1 price is whole dollars", price_cents % 100 == 0)
check("1 a $12 tee never sells at $12", price > 12.0)

# [2] single variant picker
svc = PODFulfillmentService.__new__(PODFulfillmentService)  # no __init__ (avoid real clients)
variants = [
    {"id": 1, "title": "Black / S", "is_enabled": True},
    {"id": 2, "title": "Black / L", "is_enabled": True},
    {"id": 3, "title": "White / L", "is_enabled": True},
]
picked = svc._pick_single_variant(variants)
check("2 prefers Black / L", picked["id"] == 2)
# no preferred -> middle
odd = [{"id": 10, "title": "Red / XXL", "is_enabled": True},
       {"id": 11, "title": "Green / XXL", "is_enabled": True},
       {"id": 12, "title": "Blue / XXL", "is_enabled": True}]
check("2 falls back to a middle variant", svc._pick_single_variant(odd)["id"] == 11)
check("2 size token match not substring ('l' not in 'black')",
      not svc._pick_single_variant([{"id": 9, "title": "Black / S", "is_enabled": True}])["title"].endswith("/ L"))

# [3] variant cost from readback
product = {"variants": [{"id": 2, "cost": 1150}, {"id": 3, "cost": 1200}]}
check("3 reads sold variant cost", PODFulfillmentService._variant_cost_cents(product, 2) == 1150)
check("3 falls back to max cost if id missing", PODFulfillmentService._variant_cost_cents(product, 999) == 1200)

# [4] end-to-end create_product_for_task with fakes
printify = MagicMock()
printify.upload_image.return_value = "img-1"
printify.list_blueprints.return_value = [
    {"id": 100, "title": "Ceramic Mug 11oz"},
    {"id": 6, "title": "Unisex Jersey Short Sleeve Tee"},
    {"id": 77, "title": "Unisex Hoodie"},
    {"id": 384, "title": "Unisex Heavy Cotton T-Shirt"},
]
printify.list_print_providers.return_value = [{"id": 55}]
printify.list_variants.return_value = {"variants": [
    {"id": 1, "title": "Black / S", "is_enabled": True},
    {"id": 2, "title": "Black / L", "is_enabled": True},
]}
printify.create_product.return_value = {"id": "prod-1"}
printify.get_product.return_value = {
    "print_areas": [{"placeholders": [{"images": [{"id": "img-1"}]}]}],
    "variants": [{"id": 2, "cost": 1300}],
}

selector = MagicMock()
seen = {}
def _select(concept, blueprints):
    seen["concept"] = concept
    seen["ids"] = [b["id"] for b in blueprints]
    return {"blueprint_id": 6}
selector.select.side_effect = _select

catalog = MagicMock()
catalog.get_delivery_asset.return_value = MagicMock(local_path=__file__)  # any existing file

svc2 = PODFulfillmentService(printify_client=printify, selector_agent=selector)
svc2._catalog = catalog

pod = svc2.create_product_for_task("task-xyz", concept="Funny Cat Astronaut Tee. A cat in a spacesuit.")

check("4 selector got the REAL concept (not task_id=)", "Funny Cat Astronaut" in seen.get("concept", ""))
check("4 only TEE blueprints offered (no mug/hoodie)", set(seen.get("ids", [])) == {6, 384})
check("4 exactly one variant created", pod.variant_ids == [2])
check("4 variant_title persisted", pod.variant_title == "Black / L")
check("4 cost_cents persisted from readback", pod.cost_cents == 1300)
check("4 margin-safe price persisted and covers cost",
      pod.price_cents and pod.price_cents / 100.0 > 13.0)
# create_product called with a single variant
_, kwargs = printify.create_product.call_args
check("4 create_product got single variant", kwargs.get("variant_ids") == [2])
check("4 Printify title is the real concept, not 'AI Factory Product'",
      "Funny Cat" in kwargs.get("title", ""))

print()
if failures:
    print(f"{len(failures)} test(s) FAILED: {failures}")
    sys.exit(1)
print("All step-102 POD-margin tests passed.")
