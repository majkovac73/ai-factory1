"""
PodVariantMapper (STEP 104 7-2) — map a Printify blueprint's variant array to an
Etsy listing-inventory payload so buyers can actually pick a size/color.

A POD tee nets ~$6/sale vs ~$3-5 for a digital, but it stays off
(POD_APPAREL_ENABLED=false) until a buyer can choose their size — a single-
variant apparel listing barely converts. This module is the missing bridge:
  - select_variants(): pick a sane, capped subset of the (often 100+) Printify
    variants — a few neutral colors x a standard size run — instead of all or
    just one.
  - build_etsy_inventory(): turn those into the Etsy PUT /listings/{id}/inventory
    body (products with Size/Color property_values + per-variant offering price),
    priced per variant from its real Printify cost.
  - variant_map(): the Etsy(size,color) -> Printify variant_id table to persist,
    so an order's chosen variation resolves to the right Printify variant at
    fulfillment.

Pure and deterministic — fully unit-testable without any live API.
"""
import logging

logger = logging.getLogger("ai-factory")

# Etsy standard variation property IDs (documented): 100 = Size, 200 = Primary color.
SIZE_PROPERTY_ID = 100
COLOR_PROPERTY_ID = 200

# Conservative defaults — a clean size run and a few neutral colors keep the
# listing manageable and on-brand. Overridable via settings.
DEFAULT_SIZES = ["S", "M", "L", "XL", "2XL"]
DEFAULT_COLORS = ["black", "white", "navy"]
MAX_VARIANTS = 30  # Etsy allows up to ~70/property but keep the grid sane


class PodVariantMapper:
    @staticmethod
    def parse_option(variant: dict) -> tuple:
        """Extract (size, color) for a Printify variant from its `options` dict
        or, failing that, its slash-delimited title ("Black / M"). Returns
        (size|None, color|None), both normalized lowercase."""
        opts = variant.get("options") or {}
        size = color = None
        if isinstance(opts, dict):
            for k, v in opts.items():
                kl = str(k).lower()
                if "size" in kl:
                    size = str(v).strip().lower()
                elif "color" in kl or "colour" in kl:
                    color = str(v).strip().lower()
        # fall back to the title tokens
        if size is None or color is None:
            tokens = [t.strip().lower() for part in str(variant.get("title", "")).split("/")
                      for t in part.split()]
            known_sizes = {s.lower() for s in DEFAULT_SIZES} | {"xs", "xxl", "3xl", "4xl"}
            for t in tokens:
                if size is None and t in known_sizes:
                    size = t
                elif color is None and t not in known_sizes:
                    color = t
        return size, color

    @classmethod
    def select_variants(cls, all_variants: list, sizes: list = None, colors: list = None,
                        max_variants: int = MAX_VARIANTS) -> list:
        """Pick the enabled variants whose size AND color are in the desired sets,
        capped. Falls back to any-color if the preferred colors aren't offered so
        the listing is never left with zero variants."""
        sizes = [s.lower() for s in (sizes or DEFAULT_SIZES)]
        colors = [c.lower() for c in (colors or DEFAULT_COLORS)]
        enabled = [v for v in all_variants if v.get("is_enabled", True)] or list(all_variants)

        def pick(color_filter):
            out = []
            for size in sizes:              # keep a natural S->XL ordering
                for v in enabled:
                    s, c = cls.parse_option(v)
                    if s != size:
                        continue
                    if color_filter is not None and c not in color_filter:
                        continue
                    out.append(v)
            return out

        chosen = pick(colors) or pick(None)
        # de-dup by variant id, preserve order, cap
        seen, result = set(), []
        for v in chosen:
            vid = v.get("id")
            if vid in seen:
                continue
            seen.add(vid)
            result.append(v)
            if len(result) >= max_variants:
                break
        return result

    @classmethod
    def build_etsy_inventory(cls, variants: list, price_cents_fn) -> dict:
        """Build the Etsy inventory payload from selected Printify variants.
        price_cents_fn(variant) -> int cents is the margin-safe Etsy price for
        that variant. Returns {"products": [...], "price_on_property": [...],
        "quantity_on_property": [...]} ready for PUT /listings/{id}/inventory."""
        products, props_used = [], set()
        for v in variants:
            size, color = cls.parse_option(v)
            property_values = []
            if size:
                property_values.append({
                    "property_id": SIZE_PROPERTY_ID, "property_name": "Size",
                    "values": [size.upper()],
                })
                props_used.add(SIZE_PROPERTY_ID)
            if color:
                property_values.append({
                    "property_id": COLOR_PROPERTY_ID, "property_name": "Color",
                    "values": [color.capitalize()],
                })
                props_used.add(COLOR_PROPERTY_ID)
            if not property_values:
                continue
            price = round((price_cents_fn(v) or 0) / 100.0, 2)
            products.append({
                "sku": f"pf-{v.get('id')}",
                "property_values": property_values,
                "offerings": [{"price": price, "quantity": 999, "is_enabled": True}],
            })
        return {
            "products": products,
            # price varies by variant, so price is "on" both properties.
            "price_on_property": sorted(props_used),
            "quantity_on_property": [],
            "sku_on_property": sorted(props_used),
        }

    @classmethod
    def variant_map(cls, variants: list) -> list:
        """The persisted lookup: [{size, color, variant_id, sku}] so an Etsy
        order's chosen (size,color) resolves to the Printify variant to order."""
        out = []
        for v in variants:
            size, color = cls.parse_option(v)
            out.append({"size": (size or "").upper(), "color": (color or "").capitalize(),
                        "variant_id": v.get("id"), "sku": f"pf-{v.get('id')}"})
        return out

    @staticmethod
    def resolve_variant_id(variant_map: list, size: str = None, color: str = None):
        """Given a persisted variant_map and the buyer's chosen (size, color),
        return the matching Printify variant_id. Ensures a POD order ships the
        EXACT variant the buyer selected. Falls back progressively (both -> size
        only -> color only -> first) so an order is never dropped, and returns
        None only if the map is empty."""
        if not variant_map:
            return None
        s = (size or "").strip().upper()
        c = (color or "").strip().lower()

        def norm_c(x):
            return (x or "").strip().lower()

        # exact (size AND color)
        if s and c:
            for e in variant_map:
                if (e.get("size") or "").upper() == s and norm_c(e.get("color")) == c:
                    return e.get("variant_id")
        # size only
        if s:
            for e in variant_map:
                if (e.get("size") or "").upper() == s:
                    return e.get("variant_id")
        # color only
        if c:
            for e in variant_map:
                if norm_c(e.get("color")) == c:
                    return e.get("variant_id")
        # last resort: first mapped variant (never drop a paid order)
        return variant_map[0].get("variant_id")

    @staticmethod
    def parse_buyer_variations(transaction: dict) -> tuple:
        """Extract (size, color) from an Etsy transaction's `variations` array
        (each: {property_id, formatted_name, formatted_value}). Size property is
        100, color 200; also matches on the formatted_name as a fallback."""
        size = color = None
        for var in (transaction or {}).get("variations", []) or []:
            pid = var.get("property_id")
            name = str(var.get("formatted_name", "")).lower()
            val = var.get("formatted_value") or ""
            if pid == SIZE_PROPERTY_ID or "size" in name:
                size = val
            elif pid == COLOR_PROPERTY_ID or "color" in name or "colour" in name:
                color = val
        return size, color
