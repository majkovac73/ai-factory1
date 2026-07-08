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

PRODUCT_FORMATS = {
    "single_print":         {"category": "digital", "delivery": "single_image", "taxonomy_id": 2078},
    "coloring_page":        {"category": "digital", "delivery": "single_image", "taxonomy_id": 339},
    "greeting_card_design": {"category": "digital", "delivery": "single_image", "taxonomy_id": 1280},
    "phone_wallpaper":      {"category": "digital", "delivery": "single_image", "taxonomy_id": 2078},
    "sticker_sheet_design": {"category": "digital", "delivery": "single_image", "taxonomy_id": 1326},
    "pdf_planner_or_guide": {"category": "digital", "delivery": "pdf",          "taxonomy_id": 354},
    "pod_apparel_design":   {"category": "pod",     "delivery": "single_image", "taxonomy_id": 482},
}
