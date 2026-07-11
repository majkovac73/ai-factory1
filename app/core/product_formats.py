"""
Product format registry — step 91.

Single source of truth for the strict, capability-tied allow-list of
concrete product formats the pipeline can actually produce. Both
TrendResearchAgent (validates a proposed concept against this list before a
task is ever created) and PipelineOrchestrator (branches delivery-asset
generation and Etsy listing type on it, and refuses to create a listing for
anything not in this list) import from here so the two can never drift out
of sync.

category:
  "digital" — Etsy listing type "download", no Printify/POD involvement.
  "pod"     — Etsy listing type "physical", a real Printify product is
              required (PODFulfillmentService.create_product_for_task()).

delivery:
  "single_image" — one delivery asset, produced by PODPipelineService /
                    PODDesignAgent (existing single-image path). Used for
                    every digital single-image format AND pod_apparel_design
                    (the sellable design sent to Printify is still one
                    asset — additional listing photos are separate, see
                    below).
  "pdf"           — a real multi-page PDF, produced by PDFGenerationService,
                    capped at settings.MAX_PDF_PAGES pages.

Every format also gets the existing hero+lifestyle listing-photo images
(ProductImageAgent, unrelated to the delivery asset) — that stage already
runs for every recognized format. pod_apparel_design's "multi-image POD"
requirement is exactly this: one sellable design asset submitted to
Printify, plus the existing multi-image Etsy listing gallery (front view /
lifestyle shot) — not multiple different sellable products.

taxonomy_id (step 93): a real, specific LEAF node id from Etsy's live
seller-taxonomy tree (GET /v3/application/seller-taxonomy/nodes), NOT
guessed and NOT the previous silent default of 1 ("Accessories" — the
top-level, most-generic node in the entire tree, which is what every
listing had been created with since nothing upstream ever set
taxonomy_id at all). Etsy's own listing editor flags category 1 as "too
broad" — confirmed live against production listing 4534427807.

Verified against the real tree fetched 2026-07-08 (results cached in the
step 93 changelog):
  single_print / phone_wallpaper : 2078  "Digital Prints" (Art & Collectibles > Prints > Digital Prints) — true leaf (0 children). Etsy's taxonomy has no separate "phone wallpaper" node.
  coloring_page                  : 339   "Coloring Books" (Books, Movies & Music > ... > Coloring Books) — true leaf.
  greeting_card_design           : 1280  "Just Because Cards" (Paper & Party Supplies > Greeting Cards > Just Because Cards) — true leaf; picked over parent "Greeting Cards" (1261, which itself has 20 occasion-specific children and is not a true leaf) since a customizable, any-occasion card fits no single occasion child.
  sticker_sheet_design           : 1326  "Stickers" (Paper & Party Supplies > Stickers, Labels & Tags > Stickers) — true leaf.
  pdf_planner_or_guide           : 354   "Calendars & Planners" (Paper & Party Supplies > ... > Calendars & Planners) — this node DOES have 4 children, but all 4 are calendar sub-types (Advent/Desk/Pocket/Wall) with no dedicated "Planners" leaf existing anywhere in the tree; 354 itself is the best real match for a multi-page planner PDF.
  pod_apparel_design             : 482   "T-shirts" (Clothing > Gender-Neutral Adult Clothing > ... > T-shirts) — true leaf. This is a static default for the common case (unisex adult tee); Etsy's tree has separate T-shirt leaves per demographic (men's/women's/boys'/girls'/gender-neutral, ids 449/559/11136/11143/482) and per apparel type (hoodie/tee/etc.) that a real Printify blueprint could map to more precisely — flagged as a follow-up, not built here (would need blueprint-title-to-taxonomy matching, a materially larger feature).
"""

# price_band (P0-11): the realistic Etsy price range for each format, in USD.
# The listing-pricing LLM is HINTED with this band and its output is CLAMPED to
# it (midpoint if the LLM returns something out of range / None / 0), so a
# listing can never publish at $0 (Etsy rejects <$0.20) or a wildly wrong price.
# The old single hardcoded "$10-25" band overpriced cheap digital goods (a
# coloring page / wallpaper sells for ~$2-6) and underpriced nothing usefully.
# pod_apparel_design's band is only a conservative FALLBACK floor — real POD
# pricing must be cost-based (P0-4) to guarantee margin; the band keeps it from
# ever going stupidly low if that computation is unavailable.
# delivery_aspect (P1-2): the shape the DELIVERED file must be. A phone
# wallpaper delivered as a 1:1 square is a bad product (phones are 9:16); a
# greeting card is portrait; coloring pages are usually 8.5x11 portrait. Only
# single_print / sticker_sheet / pod design stay square. Values are OpenRouter
# aspect strings; non-square delivery renders at 4K to clear Seedream's pixel
# floor (see PipelineOrchestrator._stage_pod_design).
PRODUCT_FORMATS = {
    # NOTE: master stays 1:1 for now — DeliveryBundleService still center-crops
    # it to portrait print ratios (2:3/3:4/4:5/A). A portrait master (A-5 pt 2)
    # would give crops more real estate but changes many delivery-validation
    # fixtures; tracked as a follow-up.
    "single_print":         {"category": "digital", "delivery": "single_image", "taxonomy_id": 2078, "price_band": (3.50, 8.00),  "delivery_aspect": "1:1"},
    "coloring_page":        {"category": "digital", "delivery": "single_image", "taxonomy_id": 339,  "price_band": (2.00, 4.50),  "delivery_aspect": "3:4"},
    "greeting_card_design": {"category": "digital", "delivery": "single_image", "taxonomy_id": 1280, "price_band": (3.00, 6.00),  "delivery_aspect": "3:4"},
    "phone_wallpaper":      {"category": "digital", "delivery": "single_image", "taxonomy_id": 2078, "price_band": (2.00, 4.00),  "delivery_aspect": "9:16"},
    "sticker_sheet_design": {"category": "digital", "delivery": "single_image", "taxonomy_id": 1326, "price_band": (3.00, 6.00),  "delivery_aspect": "1:1"},
    "pdf_planner_or_guide": {"category": "digital", "delivery": "pdf",          "taxonomy_id": 354,  "price_band": (5.00, 12.00), "delivery_aspect": "3:4"},
    "pod_apparel_design":   {"category": "pod",     "delivery": "single_image", "taxonomy_id": 482,  "price_band": (24.00, 40.00), "delivery_aspect": "1:1"},
}


def delivery_aspect_for(product_format: str) -> str:
    """Return the delivered-file aspect string for a format (default '1:1')."""
    return (PRODUCT_FORMATS.get(product_format) or {}).get("delivery_aspect", "1:1")


def aspect_to_ratio(aspect: str):
    """Parse an aspect string like '9:16' into an (w, h) int tuple; default (1,1)."""
    try:
        w, h = aspect.split(":")
        return int(w), int(h)
    except Exception:
        return (1, 1)

# Fallback band for any format missing one (defensive — every format above has
# an explicit band).
DEFAULT_PRICE_BAND = (3.00, 10.00)


def price_band_for(product_format: str):
    """Return (low, high) USD price band for a format, or the default."""
    spec = PRODUCT_FORMATS.get(product_format) or {}
    band = spec.get("price_band") or DEFAULT_PRICE_BAND
    return float(band[0]), float(band[1])


def clamp_price(price, product_format: str) -> float:
    """Clamp an LLM-proposed price into the format's band; return the band
    midpoint if it's missing/non-numeric/out of range. NEVER returns 0/None."""
    lo, hi = price_band_for(product_format)
    if not isinstance(price, (int, float)) or isinstance(price, bool) or price < lo or price > hi:
        return round((lo + hi) / 2, 2)
    return round(float(price), 2)


# A-4: Etsy materials per format (the old code always sent an empty list, wasting
# a small search/relevance surface). Physical POD carries real materials.
_MATERIALS = {
    "single_print":         ["digital download", "printable wall art", "high resolution"],
    "coloring_page":        ["digital download", "printable coloring page", "instant download"],
    "greeting_card_design": ["digital download", "printable greeting card"],
    "phone_wallpaper":      ["digital download", "phone wallpaper", "instant download"],
    "sticker_sheet_design": ["digital download", "printable stickers"],
    "pdf_planner_or_guide": ["digital download", "printable planner", "PDF"],
    "pod_apparel_design":   ["cotton", "unisex t-shirt", "DTG print"],
}


def materials_for(product_format: str) -> list:
    return list(_MATERIALS.get(product_format, ["digital download"]))


def description_blocks(product_format: str, page_count: int = None) -> str:
    """A-4: deterministic, buyer-question-answering description sections appended
    to the LLM's creative hook — "WHAT YOU GET", "HOW IT WORKS", usage terms.
    These can't be hallucinated away and answer exactly what a buyer needs to
    decide, which a free-form blurb doesn't."""
    spec = PRODUCT_FORMATS.get(product_format) or {}
    is_pod = spec.get("category") == "pod"
    is_pdf = spec.get("delivery") == "pdf"

    what = {
        "single_print": "• 1 high-resolution digital print file, ready to print at home, at a local shop, or online.",
        "coloring_page": "• 1 printable coloring page (high-resolution PNG) — print as many copies as you like for personal use.",
        "greeting_card_design": "• 1 printable greeting card design (high-resolution file).",
        "phone_wallpaper": "• 1 high-resolution phone wallpaper sized for modern smartphones.",
        "sticker_sheet_design": "• 1 printable sticker sheet (high-resolution PNG) — print onto sticker paper and cut out.",
        "pdf_planner_or_guide": f"• 1 printable PDF{f' with {page_count} pages' if page_count else ''} — print at home or use on a tablet.",
        "pod_apparel_design": "• 1 made-to-order apparel item, printed just for you (see the size/variant note above).",
    }.get(product_format, "• 1 digital file.")

    if is_pod:
        how = (
            "HOW IT WORKS\n"
            "• Made to order and printed just for you, then shipped by our production partner.\n"
            "• See the listing photos for the exact product and print placement."
        )
    else:
        how = (
            "HOW IT WORKS\n"
            "• Instant digital download — no physical item is shipped.\n"
            "• Files are available immediately after purchase in your Etsy account (Purchases → Downloads).\n"
            + ("• Print at home, at a print shop, or online.\n" if not is_pdf else "• Open/print the PDF at home or use it on a tablet.\n")
        ).rstrip()

    terms = (
        "TERMS\n"
        "• For personal use only. Please do not resell, redistribute, or claim the design as your own."
    )
    return f"WHAT YOU GET\n{what}\n\n{how}\n\n{terms}"
