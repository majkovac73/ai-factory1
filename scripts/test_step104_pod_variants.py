"""
Step 104 test — 7-2 Printify variant-array -> Etsy variations mapping.

Usage: python scripts/test_step104_pod_variants.py
"""
import os
import sys
import tempfile

os.environ["DATABASE_PATH"] = os.path.join(tempfile.mkdtemp(), "podv.db")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.services.pod_variant_mapper import (
    PodVariantMapper, SIZE_PROPERTY_ID, COLOR_PROPERTY_ID,
)

failures = []


def check(name, cond):
    print(f"[{'PASS' if cond else 'FAIL'}] {name}")
    if not cond:
        failures.append(name)


# a realistic-ish Printify variant array: options-dict AND title-only styles
def mk(vid, size, color, options=True, enabled=True, cost=1200):
    v = {"id": vid, "title": f"{color} / {size}", "is_enabled": enabled, "cost": cost}
    if options:
        v["options"] = {"size": size, "color": color}
    return v


variants = []
vid = 1
for color in ["Black", "White", "Navy", "Red"]:      # Red is not a default color
    for size in ["XS", "S", "M", "L", "XL", "2XL", "3XL"]:
        variants.append(mk(vid, size, color, options=(vid % 2 == 0)))  # mix option styles
        vid += 1

# ── parse_option handles both option-dict and title-only ──
s, c = PodVariantMapper.parse_option(variants[0])   # title-only (odd id)
check("7-2 parse_option from title works", s == "xs" and c == "black")
s2, c2 = PodVariantMapper.parse_option(variants[1])  # options dict (even id)
check("7-2 parse_option from options dict works", s2 == "s" and c2 == "black")

# ── selection: default sizes x default colors, capped, no Red ──
sel = PodVariantMapper.select_variants(variants)
sizes = {PodVariantMapper.parse_option(v)[0] for v in sel}
colors = {PodVariantMapper.parse_option(v)[1] for v in sel}
check("7-2 selection restricts to default sizes", sizes <= {"s", "m", "l", "xl", "2xl"})
check("7-2 selection excludes non-default color (red)", "red" not in colors)
check("7-2 selection includes multiple sizes (real size run)", len(sizes) >= 4)
check("7-2 selection includes multiple colors", len(colors) >= 2)
check("7-2 selection is capped", len(sel) <= 30)

# ── fallback when preferred colors absent: any-color rather than empty ──
only_red = [mk(900 + i, sz, "Crimson") for i, sz in enumerate(["S", "M", "L"])]
sel_fb = PodVariantMapper.select_variants(only_red)
check("7-2 falls back to any-color instead of empty", len(sel_fb) == 3)

# ── build Etsy inventory payload ──
inv = PodVariantMapper.build_etsy_inventory(sel, price_cents_fn=lambda v: 2500)
check("7-2 inventory has one product per variant", len(inv["products"]) == len(sel))
p0 = inv["products"][0]
check("7-2 product has a sku", p0["sku"].startswith("pf-"))
prop_ids = {pv["property_id"] for pv in p0["property_values"]}
check("7-2 product carries Size + Color properties", SIZE_PROPERTY_ID in prop_ids and COLOR_PROPERTY_ID in prop_ids)
check("7-2 offering price is in dollars", p0["offerings"][0]["price"] == 25.0)
check("7-2 offering enabled with quantity", p0["offerings"][0]["is_enabled"] and p0["offerings"][0]["quantity"] > 0)
check("7-2 price varies on both properties", set(inv["price_on_property"]) == {SIZE_PROPERTY_ID, COLOR_PROPERTY_ID})

# per-variant pricing from cost actually differs
inv2 = PodVariantMapper.build_etsy_inventory(
    sel, price_cents_fn=lambda v: 2000 + (PodVariantMapper.parse_option(v)[0] == "2xl") * 500)
prices = {pp["offerings"][0]["price"] for pp in inv2["products"]}
check("7-2 supports per-variant pricing (2XL upcharge)", 25.0 in prices and 20.0 in prices)

# ── persisted resolution map ──
vmap = PodVariantMapper.variant_map(sel)
check("7-2 variant_map resolves (size,color)->printify variant_id", all(m["variant_id"] for m in vmap))
sample = vmap[0]
check("7-2 variant_map entry has size/color/sku", sample["size"] and sample["color"] and sample["sku"])

# ── EtsyClient exposes the inventory PUT ──
from app.services.etsy_client import EtsyClient
check("7-2 EtsyClient.update_listing_inventory exists", callable(getattr(EtsyClient, "update_listing_inventory", None)))

# ── pipeline stage skips a SINGLE-variant POD product (variations only make
#    sense for a multi-variant product; format-agnostic, not enable-flag coupled) ──
from app.services.pipeline_orchestrator import PipelineOrchestrator
rep = {"stages": {}}
PipelineOrchestrator()._stage_pod_variations(type("P", (), {"printify_product_id": "x", "variant_ids": [123], "price_cents": 0})(), "L1", rep)
check("7-2 pod_variations skips a single-variant product",
      rep["stages"].get("pod_variations", {}).get("skipped") == "single variant")

print()
if failures:
    print(f"{len(failures)} test(s) FAILED: {failures}")
    sys.exit(1)
print("All step-104 (7-2) tests passed.")
