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
"""

PRODUCT_FORMATS = {
    "single_print":         {"category": "digital", "delivery": "single_image"},
    "coloring_page":        {"category": "digital", "delivery": "single_image"},
    "greeting_card_design": {"category": "digital", "delivery": "single_image"},
    "phone_wallpaper":      {"category": "digital", "delivery": "single_image"},
    "sticker_sheet_design": {"category": "digital", "delivery": "single_image"},
    "pdf_planner_or_guide": {"category": "digital", "delivery": "pdf"},
    "pod_apparel_design":   {"category": "pod",     "delivery": "single_image"},
}
