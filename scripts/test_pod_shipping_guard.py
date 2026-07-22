"""
POD end-to-end: shipping-profile resolution + buyer-variant routing.

- POD is proposable when POD_APPAREL_ENABLED (shipping profile auto-resolved).
- Orchestrator fast-fails a POD task BEFORE generation only if the shipping
  profile can't be resolved/created.
- EtsyShippingService finds a usable profile via the CORRECT Etsy fields
  (shipping_profile_id / profile_type) and prefers worldwide-shipping profiles.
- Fulfillment routes to the Printify variant matching the buyer's chosen
  size/color (was: always the first variant).

Usage: python scripts/test_pod_shipping_guard.py
"""
import os, sys, tempfile
from unittest.mock import patch, MagicMock
os.environ["DATABASE_PATH"] = os.path.join(tempfile.mkdtemp(), "pod.db")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config import settings

failures = []
def check(name, cond):
    print(f"[{'PASS' if cond else 'FAIL'}] {name}")
    if not cond:
        failures.append(name)

from app.agents.trend_research_agent import TrendResearchAgent
base = dict(WALL_ART_SET_ENABLED=False, SEAMLESS_PATTERN_ENABLED=False, PHONE_WALLPAPER_ENABLED=False)
def formats(**over):
    with patch.multiple(settings, **{**base, **over}):
        return TrendResearchAgent._proposable_formats()
check("POD off -> excluded", "pod_apparel_design" not in formats(POD_APPAREL_ENABLED=False))
check("POD on -> proposable (shipping auto-resolves)", "pod_apparel_design" in formats(POD_APPAREL_ENABLED=True))

# ── orchestrator fast-fail keyed on get_or_create ────────────────────────────
from app.db.database import Base, engine, SessionLocal
from app.models.task import Task
from app.services.pipeline_orchestrator import PipelineOrchestrator
Base.metadata.create_all(bind=engine)
db = SessionLocal()
db.add(Task(id="pod-noship", prompt="tee", type="pod_apparel_design", status="DONE", output_data={"title": "Tee"}))
db.commit(); db.close()

from app.services.etsy_shipping_service import EtsyShippingService as _ESS
async def _none(self): return None
orch = PipelineOrchestrator()
with patch.object(_ESS, "get_or_create", _none), \
     patch("app.services.task_service.TaskService.record_pipeline_block", MagicMock()), \
     patch.object(orch, "_alert", MagicMock()):
    rep = orch.run_post_completion("pod-noship")
check("POD blocked before generation when profile unresolvable", rep.get("blocked") is True)
check("blocked with the SHIPPING reason (guard fired, not later gen)",
      "shipping profile" in str(rep.get("blocked_reason", "")).lower())
check("no delivery/listing generation ran (failed fast)",
      "listing_images" not in rep.get("stages", {}) and "delivery_asset" not in rep.get("stages", {}))

# ── EtsyShippingService: correct field detection + worldwide preference ───────
from app.services.etsy_shipping_service import EtsyShippingService
svc = EtsyShippingService()
domestic = {"shipping_profile_id": 1, "title": "test", "profile_type": "manual", "is_deleted": False,
            "shipping_profile_destinations": [{"destination_region": "none", "destination_country_iso": ""}]}
worldwide = {"shipping_profile_id": 2, "title": "POD Standard Shipping", "profile_type": "manual", "is_deleted": False,
             "shipping_profile_destinations": [{"destination_region": "eu"}, {"destination_region": "non_eu"}]}
check("domestic-only profile NOT considered broadly-shipping", EtsyShippingService._ships_beyond_domestic(domestic) is False)
check("worldwide profile IS broadly-shipping", EtsyShippingService._ships_beyond_domestic(worldwide) is True)

import asyncio
async def run_fetch(profiles):
    with patch.object(EtsyShippingService, "_list_profiles", side_effect=lambda: _list(profiles)):
        async def _list(p): return p
        return await EtsyShippingService()._fetch_existing()
# simpler: patch _list_profiles to an async returning our list
async def _fetch(profiles):
    async def fake_list(self=None): return profiles
    with patch.object(EtsyShippingService, "_list_profiles", fake_list):
        return await EtsyShippingService()._fetch_existing()
check("fetch skips domestic-only, ignores wrong-field 'profile_id'",
      asyncio.run(_fetch([domestic])) is None)
check("fetch finds the worldwide profile by shipping_profile_id",
      asyncio.run(_fetch([domestic, worldwide])) == "2")

# ── variant routing: buyer's size/color -> correct Printify variant ──────────
from app.services.pod_variant_mapper import PodVariantMapper
vmap = [
    {"size": "S", "color": "Black", "variant_id": 101, "sku": "pf-101"},
    {"size": "M", "color": "Navy", "variant_id": 202, "sku": "pf-202"},
    {"size": "XL", "color": "Navy", "variant_id": 303, "sku": "pf-303"},
]
check("resolve exact (XL, Navy) -> 303", PodVariantMapper.resolve_variant_id(vmap, "XL", "Navy") == 303)
check("resolve is case-insensitive (xl, navy) -> 303", PodVariantMapper.resolve_variant_id(vmap, "xl", "navy") == 303)
check("resolve size-only (M) -> 202", PodVariantMapper.resolve_variant_id(vmap, "M", None) == 202)
check("resolve color-only (Black) -> 101", PodVariantMapper.resolve_variant_id(vmap, None, "Black") == 101)
check("resolve unknown -> first (never drop order)", PodVariantMapper.resolve_variant_id(vmap, "5XL", "Pink") == 101)
check("resolve empty map -> None", PodVariantMapper.resolve_variant_id([], "M", "Navy") is None)

txn = {"variations": [
    {"property_id": 100, "formatted_name": "Size", "formatted_value": "XL"},
    {"property_id": 200, "formatted_name": "Color", "formatted_value": "Navy"},
]}
s, c = PodVariantMapper.parse_buyer_variations(txn)
check("parse buyer variations -> (XL, Navy)", s == "XL" and c == "Navy")
check("end-to-end: buyer XL/Navy -> variant 303",
      PodVariantMapper.resolve_variant_id(vmap, *PodVariantMapper.parse_buyer_variations(txn)) == 303)

# ── real Printify shipping cost baked into the FREE-shipping price ────────────
from app.services.printify_client import PrintifyClient
from app.services.pod_fulfillment_service import PODFulfillmentService

pc = PrintifyClient.__new__(PrintifyClient)
SHIP = {"profiles": [
    {"variant_ids": [10, 11], "first_item": {"cost": 429, "currency": "USD"}, "countries": ["US"]},
    {"variant_ids": [10, 11], "first_item": {"cost": 1359, "currency": "USD"}, "countries": ["DE"]},
    {"variant_ids": [10, 11], "first_item": {"cost": 1000, "currency": "USD"}, "countries": ["REST_OF_THE_WORLD"]},
    {"variant_ids": [99], "first_item": {"cost": 9999, "currency": "USD"}, "countries": ["XX"]},  # other variant
]}
pc._get = lambda path, params=None: SHIP
check("shipping cost = MAX first_item across destinations for the variant (1359)",
      pc.get_shipping_cost_cents(5, 39, variant_id=10) == 1359)
check("shipping cost excludes profiles not covering the variant",
      pc.get_shipping_cost_cents(5, 39, variant_id=10) != 9999)
pc_err = PrintifyClient.__new__(PrintifyClient)
def _boom(*a, **k): raise RuntimeError("api down")
pc_err._get = _boom
check("shipping cost None on API error (caller falls back)", pc_err.get_shipping_cost_cents(5, 39, 10) is None)

with patch.object(settings, "USD_TO_BASE_RATE", 1.0), patch.object(settings, "POD_SHIPPING_ESTIMATE_USD", 5.0), \
     patch.object(settings, "POD_TARGET_PROFIT_USD", 6.0), patch.object(settings, "POD_ETSY_FEE_FRACTION", 0.12):
    est = PODFulfillmentService._pod_price_cents_from_cost(1200)             # flat $5 estimate
    real = PODFulfillmentService._pod_price_cents_from_cost(1200, 1359)      # real $13.59 EU shipping
check("real shipping price COVERS worse shipping (real > estimate)", real > est)
check("real shipping price includes the ~$8.59 extra shipping",
      abs((real - est) - round((13.59 - 5.0) / (1 - 0.12)) * 100) < 200)

# ── mugs + posters: formats, size-only variants, format-aware proposal ───────
from app.core.product_formats import PRODUCT_FORMATS
check("pod_mug is a POD format (taxonomy 1062)",
      PRODUCT_FORMATS.get("pod_mug", {}).get("category") == "pod" and PRODUCT_FORMATS["pod_mug"]["taxonomy_id"] == 1062)
check("pod_poster is a POD format (taxonomy 121)",
      PRODUCT_FORMATS.get("pod_poster", {}).get("category") == "pod" and PRODUCT_FORMATS["pod_poster"]["taxonomy_id"] == 121)

# proposable only when their flag is on
def formats2(**over):
    with patch.multiple(settings, **{**base, "POD_APPAREL_ENABLED": False, "POD_MUG_ENABLED": False, "POD_POSTER_ENABLED": False, **over}):
        return TrendResearchAgent._proposable_formats()
check("mug/poster excluded when flags off",
      "pod_mug" not in formats2() and "pod_poster" not in formats2())
check("pod_mug proposable when POD_MUG_ENABLED", "pod_mug" in formats2(POD_MUG_ENABLED=True))
check("pod_poster proposable when POD_POSTER_ENABLED", "pod_poster" in formats2(POD_POSTER_ENABLED=True))

# size-only variant selection (mugs/posters): distinct sizes, no color axis
poster_variants = [
    {"id": 1, "is_enabled": True, "options": {"size": "11″ x 9″"}},
    {"id": 2, "is_enabled": True, "options": {"size": "18″ x 12″"}},
    {"id": 3, "is_enabled": True, "options": {"size": "24″ x 18″"}},
    {"id": 4, "is_enabled": True, "options": {"size": "24″ x 18″"}},  # dup size
]
sel = PodVariantMapper.select_size_variants(poster_variants, max_variants=8)
check("size-only selection dedups sizes (3 distinct)", len(sel) == 3)
inv = PodVariantMapper.build_etsy_inventory(sel, lambda v: 2000)
check("poster inventory has Size property, no Color",
      all(any(pv["property_name"] == "Size" for pv in p["property_values"]) and
          not any(pv["property_name"] == "Color" for pv in p["property_values"]) for p in inv["products"]))
check("poster size display preserved (dimension, not uppercased weirdly)",
      inv["products"][0]["property_values"][0]["values"][0] == "11″ x 9″")

# _disp_size: apparel upper, dimensions kept
check("_disp_size apparel 'm' -> 'M'", PodVariantMapper._disp_size("m") == "M")
check("_disp_size '11oz' kept", PodVariantMapper._disp_size("11oz") == "11oz")

# fulfillment routing works for size-only (poster): buyer picks a size
pmap = PodVariantMapper.variant_map(sel)
txn_p = {"variations": [{"property_id": 100, "formatted_name": "Size", "formatted_value": "18″ x 12″"}]}
bs, bc = PodVariantMapper.parse_buyer_variations(txn_p)
check("poster buyer size resolves to its variant",
      PodVariantMapper.resolve_variant_id(pmap, bs, bc) == 2)

# format config drives blueprint markers/strategy
from app.services.pod_fulfillment_service import _POD_FORMAT_CONFIG
check("mug config: 'mug' marker, size strategy",
      "mug" in _POD_FORMAT_CONFIG["pod_mug"]["markers"] and _POD_FORMAT_CONFIG["pod_mug"]["variant_strategy"] == "size")
check("poster config: 'poster' marker, prefers vertical",
      "poster" in _POD_FORMAT_CONFIG["pod_poster"]["markers"] and "vertical" in _POD_FORMAT_CONFIG["pod_poster"].get("prefer", ()))

print()
if failures:
    print(f"{len(failures)} test(s) FAILED: {failures}")
    sys.exit(1)
print("All POD end-to-end tests passed.")
