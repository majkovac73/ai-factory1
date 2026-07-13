import os

from dotenv import load_dotenv
from pydantic_settings import BaseSettings

load_dotenv()


class Settings(BaseSettings):
    APP_NAME: str = "AI Factory"
    ENV: str = "development"
    DEBUG: bool = True

    HOST: str = "127.0.0.1"
    PORT: int = 8000

    DATABASE_URL: str = "sqlite:///./app.db"

    OPENAI_API_KEY: str | None = None  # no longer used for images; kept in case a future step needs it directly
    ANTHROPIC_API_KEY: str | None = None
    DEFAULT_MODEL: str = "openai/gpt-4o-mini"
    # B-5: the concept and SEO calls are the two highest-leverage LLM outputs in
    # the system (what to build, how it's found) yet cost well under $0.05/day on
    # mini. Point these at a stronger model (e.g. anthropic/claude-sonnet-5 or
    # openai/gpt-4o via OpenRouter) for cents more per day. Default = DEFAULT_MODEL.
    CONCEPT_MODEL: str | None = None
    SEO_MODEL: str | None = None

    OPENROUTER_API_KEY: str | None = None

    IMAGE_PROVIDER: str = "openrouter"
    OPENROUTER_IMAGE_MODEL: str = "bytedance-seed/seedream-4.5"
    DEFAULT_IMAGE_SIZE: str = "1024x1024"  # fallback; actual per-request sizing uses aspect_ratio + resolution params

    ETSY_API_KEY: str | None = None
    ETSY_SHARED_SECRET: str | None = None
    ETSY_REDIRECT_URI: str = "http://localhost:8000/etsy/oauth/callback"
    ETSY_SHOP_ID: str | None = None

    PINTEREST_APP_ID: str | None = None
    PINTEREST_APP_SECRET: str | None = None
    PINTEREST_REDIRECT_URI: str = "http://localhost:8000/pinterest/oauth/callback"
    PINTEREST_BOARD_ID: str | None = None
    # A-9: optional per-format Pinterest board routing (product_format ->
    # board_id) as a JSON env, e.g. '{"single_print":"123","pdf_planner_or_guide":"456"}'.
    # Falls back to PINTEREST_BOARD_ID when a format isn't mapped.
    PINTEREST_BOARD_MAP: dict = {}

    # Tumblr (OAuth 2.0). Maj registered the app and set the consumer key/secret
    # in Railway. The redirect_uri MUST match one registered on the Tumblr app —
    # defaulted to the live Railway domain so the one-time prod authorization
    # works without an extra env var; overridable for local dev.
    TUMBLR_CONSUMER_KEY: str | None = None
    TUMBLR_CONSUMER_SECRET: str | None = None
    TUMBLR_BLOG_NAME: str | None = None
    TUMBLR_REDIRECT_URI: str = "https://kind-liberation-production.up.railway.app/tumblr/oauth/callback"
    # Whether to send redirect_uri in the OAuth authorize/token requests. Tumblr
    # makes it optional when exactly one callback URL is registered, and sending
    # it produced a persistent redirect_uri_mismatch — so default OFF (omit) and
    # rely on the app's registered default callback. Set True only if multiple
    # callback URLs are registered on the Tumblr app.
    TUMBLR_SEND_REDIRECT_URI: bool = False

    # API access control (step 102 / P0-3). When set, every money-spending or
    # shop-mutating request (POST/PUT/PATCH/DELETE) plus the sensitive /logs
    # reads must carry header `X-Factory-Key: <FACTORY_API_KEY>`. Read-only
    # dashboards/health/OAuth-callbacks stay open so the /ui frontend and
    # Railway healthcheck keep working without a key. When UNSET, enforcement
    # is OFF (nothing breaks on deploy) — set this in Railway env to turn
    # protection on. There is no default: a hardcoded key would be worse than
    # none.
    FACTORY_API_KEY: str | None = None

    LOG_LEVEL: str = "info"

    PRINTIFY_API_KEY: str | None = None
    PRINTIFY_SHOP_ID: str | None = None

    # P0-4: POD margin math. The Etsy price for a print-on-demand item is
    # computed from Printify's real per-variant production cost so a sale can
    # never lose money: price = ceil((max_variant_cost + shipping_est + $0.20
    # listing fee + target_profit) / (1 - etsy_fee_fraction)). Rounding UP to a
    # whole dollar protects margin further.
    # 4-1: Etsy fee estimate per sale for honest P&L (transaction + payment
    # processing). ~6.5% transaction + ~3% payment + $0.25 flat.
    ETSY_TRANSACTION_FEE_PCT: float = 0.065
    ETSY_PAYMENT_FEE_PCT: float = 0.03
    ETSY_PAYMENT_FEE_FLAT: float = 0.25
    # 105 4-1: $0.20 auto-renew per active listing every ~4 months (amortized).
    ETSY_LISTING_RENEWAL_FEE: float = 0.20
    ETSY_LISTING_RENEWAL_MONTHS: float = 4

    POD_TARGET_PROFIT_USD: float = 6.00
    POD_SHIPPING_ESTIMATE_USD: float = 5.00
    # ~6.5% transaction + ~3% payment; bumped to 0.12 (D-6) to leave headroom
    # for Etsy Offsite Ads fees (12-15% when a sale is attributed).
    POD_ETSY_FEE_FRACTION: float = 0.12

    ETSY_SHIPPING_PROFILE_ID: str | None = None
    ETSY_SHOP_ORIGIN_COUNTRY: str = "US"  # ISO 3166-1 alpha-2; set in env if shop is not US-based

    # STEP 103 C-2: Etsy Creativity Standards require POD listings to DECLARE
    # their production partner (Printify). Maj adds Printify in Shop Manager
    # (Settings → Production partners), then GET /shops/{id}/production-partners
    # for the id and sets this env; POD listings then send production_partner_ids.
    # Unset = not sent (safe, but POD listings are then non-compliant).
    ETSY_PRODUCTION_PARTNER_ID: str | None = None
    # B-7: optional per-format Etsy shop section routing (product_format ->
    # shop_section_id) as a JSON env. Populate after running
    # scripts/create_shop_sections.py. Sent as shop_section_id on listing creation.
    SHOP_SECTION_MAP: dict = {}
    # Honest AI-assisted-design disclosure appended to every listing description
    # (Etsy requires accurate "how it's made" info). Mirror it in the shop About.
    SHOP_AI_DISCLOSURE: str = "Original design created using AI-assisted design tools and refined for print."

    ETSY_RECEIPT_POLL_SECONDS: int = 300

    # P0-7: a fulfillment (Printify order) submit that fails is retried on each
    # subsequent receipt poll — the poll checkpoint is held back so the receipt
    # is re-fetched — up to this many attempts before giving up LOUDLY (a
    # persistent alert asking for manual intervention) and letting the checkpoint
    # advance past it. Idempotency ((receipt_id, transaction_id) unique) makes
    # retries safe. A paying customer must never silently get nothing.
    FULFILLMENT_MAX_RETRY_ATTEMPTS: int = 5

    # Persistent storage paths — override in Railway env vars:
    #   DATABASE_PATH=/data/app.db
    #   IMAGE_STORAGE_ROOT=/data/images
    DATABASE_PATH: str | None = None
    IMAGE_STORAGE_ROOT: str | None = None

    DISCORD_WEBHOOK_URL: str | None = None

    # STEP 103 C-3: automatic backups. A daily tick zips a consistent copy of the
    # SQLite DB (OAuth tokens, PODProduct↔listing mappings, the whole
    # analytics/revenue ledger the learning loop depends on) + the runtime state
    # JSONs. If an S3-compatible bucket is configured (Cloudflare R2 / Backblaze
    # B2 etc.) it's uploaded off-box; otherwise the last BACKUP_KEEP_LOCAL zips
    # are kept on the volume and a weekly alert warns that offsite is unconfigured.
    BACKUP_ENABLED: bool = True
    BACKUP_INTERVAL_HOURS: int = 24
    # Each backup zips the whole DB (~34MB). On a 500MB Railway volume, keeping 7
    # would eat ~240MB, so keep only 2 locally and rely on BACKUP_S3_* for history.
    BACKUP_KEEP_LOCAL: int = 2

    # Automatic image pruning (STEP 103 disk hygiene). Generated listing mockups
    # are transient (uploaded to Etsy then never read again); delivery files are
    # hosted by Etsy after publish. Without pruning, data/images grows unbounded
    # and fills the volume (A-5's multi-ratio bundle accelerates this). A daily
    # tick deletes listing mockups older than LISTING hours and delivery files
    # older than DELIVERY days. Scenes cache is preserved.
    IMAGE_CLEANUP_ENABLED: bool = True
    IMAGE_CLEANUP_LISTING_MAX_AGE_HOURS: int = 6
    IMAGE_CLEANUP_DELIVERY_MAX_AGE_DAYS: int = 3

    # C-5: prune dead inventory — active listings older than this many days with
    # ZERO sales and views at/below the threshold are auto-renew fee burn and
    # drag perceived shop quality. POST /admin/prune-listings reports candidates
    # (dry-run) or, with apply=true, deactivates them.
    LISTING_PRUNE_MIN_AGE_DAYS: int = 100
    LISTING_PRUNE_MAX_VIEWS: int = 10
    BACKUP_S3_BUCKET: str | None = None
    BACKUP_S3_ENDPOINT_URL: str | None = None   # e.g. https://<acct>.r2.cloudflarestorage.com
    BACKUP_S3_ACCESS_KEY_ID: str | None = None
    BACKUP_S3_SECRET_ACCESS_KEY: str | None = None
    BACKUP_S3_REGION: str = "auto"

    MAX_TASKS_PER_DAY: int = 10
    MAX_DAILY_SPEND_USD: float = 5.00
    # 5-2: hard circuit-breaker ceiling = MAX_DAILY_SPEND_USD * this multiplier.
    # can_spend() is advisory (racy under concurrency); past this ceiling a
    # provider raises SpendCapExceeded and refuses further paid calls.
    SPEND_CIRCUIT_BREAKER_MULT: float = 1.5
    # 7-3: max in-season occasion seed phrases folded into the trend pull per
    # cycle (each is a separate pytrends fetch; keep modest to avoid 429).
    SEASONAL_SEED_MAX: int = 4
    # 7-4: zero-view listing SEO refresh thresholds.
    SEO_REFRESH_MIN_AGE_DAYS: int = 21
    SEO_REFRESH_MAX_VIEWS: int = 5
    SEO_REFRESH_MAX_PER_RUN: int = 5
    SEO_REFRESH_ENABLED: bool = True
    # 3-4: attach a deterministic ken-burns listing video on publish. Off by
    # default (encoding adds CPU/time per publish); flip on when ready.
    LISTING_VIDEO_ENABLED: bool = False
    # 7-1: allow the wall_art_set_3 format (3 coordinated prints, ~3x image cost).
    # Off until validated.
    WALL_ART_SET_ENABLED: bool = False
    # 7-1: max color-histogram distance (0-1) between set pieces for them to
    # count as sharing a palette. Above this, the set is flagged inconsistent.
    WALL_ART_SET_PALETTE_TOL: float = 0.42

    # P0-13: honest per-unit costs for the daily-spend ledger. Every image goes
    # through OpenRouterImageProvider.generate_image (flat-rate Seedream), so
    # counting each at IMAGE_COST_USD is accurate — this replaces the old flat
    # $0.20/task guess that under-counted PDF pages, mockups, remakes, pins, etc.
    # Vision-QA calls are cheap but counted so the ledger isn't fiction. ALL
    # image spend is recorded (not just autonomy) so the cap protects the wallet
    # globally.
    IMAGE_COST_USD: float = 0.04
    VISION_QA_COST_USD: float = 0.002
    AUTONOMY_ENABLED: bool = False
    AUTONOMY_SCHEDULE_SECONDS: int = 3600
    # Friendly Railway knob for how often the autonomy loop runs (and thus how
    # often a product is created/posted): interval in MINUTES. When set (>0) it
    # overrides AUTONOMY_SCHEDULE_SECONDS. e.g. 60 = one product/hour, 180 = one
    # every 3h, 1440 = one/day. Floor of 1 minute enforced. Leave unset to use
    # AUTONOMY_SCHEDULE_SECONDS. (MAX_TASKS_PER_DAY still caps the daily total.)
    AUTONOMY_INTERVAL_MINUTES: int | None = None

    AUTO_PUBLISH_LISTINGS: bool = False

    # A-1: when a product actually SELLS, spawn up to this many follow-up
    # "variant" concept tasks per day seeded from the winner (a product with a
    # real stranger's money behind it is worth many fresh guesses). Respects
    # AUTONOMY_ENABLED and the daily task/spend caps. 0 disables.
    WINNER_VARIANTS_PER_DAY: int = 2

    # B-1(b): a POD t-shirt listing where the buyer can't pick a size converts
    # near zero, yet each POD task costs full pipeline money. Paused by default
    # (the concept generator won't propose pod_apparel_design) until real Etsy
    # variations are built (B-1(a)). Existing POD listings/fulfillment still work;
    # this only stops NEW POD products. Set True in Railway to re-enable.
    POD_APPAREL_ENABLED: bool = False

    # P0-9: crash-resume bounds. On startup, DONE tasks whose post-completion
    # pipeline never recorded an outcome (crashed mid-pipeline) are re-run — but
    # ONLY those updated within the last N hours and at most this many, so a
    # first deploy can't mass-re-run (and re-spend on) the whole task history.
    PIPELINE_RESUME_WINDOW_HOURS: int = 6
    PIPELINE_RESUME_MAX: int = 5

    # Recurring marketing-refresh automation (re-promotes EXISTING published
    # products using their ALREADY-GENERATED assets — no new image generation).
    # Same safety philosophy as AUTONOMY_ENABLED: OFF by default, Maj enables
    # explicitly. The only spend is one optional cheap caption-rewrite LLM call
    # per post (see MarketingRefreshService).
    MARKETING_REFRESH_ENABLED: bool = False           # kill switch, off by default
    MARKETING_REFRESH_SCHEDULE_SECONDS: int = 21600   # every 6 hours
    MARKETING_REFRESH_MIN_INTERVAL_DAYS: int = 7      # don't re-promote same product+channel more often
    MARKETING_REFRESH_MAX_POSTS_PER_CYCLE: int = 3    # hard cap per cycle

    # Hard cap on pages for a multi-page PDF product. With A-6 code-rendering,
    # interior pages are ~free and always legible, so a competitive 20-30 page
    # planner is viable (was 6 when every page was a billable image call).
    MAX_PDF_PAGES: int = 30

    # A-6: render planner/guide INTERIOR pages deterministically (Pillow) instead
    # of image-generating them — legible grids/lines/checkboxes at ~$0, no
    # garbled-text QA failures. Page 1 stays an image-generated decorative cover.
    # Set False to fall back to the all-image-generated path (6-page era).
    PLANNER_RENDER_INTERIOR: bool = True

    # Content-quality gate (step 96): a VISION-capable model inspects the
    # actual generated delivery asset for legibility/coherence/correctness —
    # image-generation models garble text (e.g. "2 þutter"), which no
    # structural check catches. gpt-4o-mini is vision-capable and cheap; a
    # stronger model (openai/gpt-4o, a Claude vision model) can be swapped in
    # here if QA accuracy needs improving.
    CONTENT_QA_MODEL: str = "openai/gpt-4o-mini"
    CONTENT_QA_MAX_ATTEMPTS: int = 2  # regenerate-and-recheck attempts before blocking
    # 1-5: max fraction of a coloring page that may be colored/grey-shaded before
    # it's rejected as "pre-colored" and regenerated (clean line art is ~0%).
    COLORING_PAGE_MAX_COLOR_FRACTION: float = 0.03

    # Product-viability critic (step 102): an independent LLM judgment step that
    # scores a schema-valid concept 1-10 on whether a real stranger would
    # actually buy THIS specific item, and rejects (fails) anything scoring
    # below this threshold. Pass/fail is derived from the score in code (not the
    # model's own inconsistent internal bar) so it's tunable without a prompt
    # change. 6 = "a real niche buyer would plausibly purchase this"; raise to be
    # stricter, lower to be more permissive. Calibrated against manual spot-checks
    # so genuinely sellable products pass and generic/low-effort ones fail.
    VIABILITY_CRITIC_MIN_SCORE: int = 6

    # STEP 105 1-1: the composite 0-100 product-quality gate (ProductScoreService).
    # PRODUCT_MIN_SCORE is the hard bar; PRODUCT_SCORE_ENFORCE=false runs it in
    # SHADOW MODE (compute + record concept_scored events while the old 6/10
    # critic still decides). Flip to true after ~5 days of event data confirm the
    # distribution is sane. A 95 bar means expect 0-3 products/day, by design.
    # 106 1-1: the old 95 bar was mathematically unreachable (needed dual 10/10
    # judges), so the factory built nothing. The rule is now floors-based: total
    # >= PRODUCT_MIN_SCORE AND harsher judge >= PRODUCT_JUDGE_FLOOR AND
    # deterministic >= PRODUCT_DET_FLOOR AND no axis at rock bottom. Reachable
    # (B=36 + dual 9s = 90) yet strict (a weak axis can't be blended away).
    PRODUCT_MIN_SCORE: int = 90
    PRODUCT_JUDGE_FLOOR: int = 9      # both judges in the "distinctive/compelling" band
    PRODUCT_DET_FLOOR: int = 30       # of 40 — evidence must be strong
    PRODUCT_SCORE_ENFORCE: bool = False
    # 106 1-2: cycle-wide budget of fully-scored concept attempts (each ~2 judge
    # LLM calls). The persistent search tries every opportunity + one fresh
    # research pass until this many scored attempts, then stops for the hour.
    CONCEPT_SEARCH_MAX_ATTEMPTS_PER_CYCLE: int = 15
    # 106 1-8: flat per-call text-LLM cost estimate for the spend ledger (images
    # were metered, text never was). Strong models (sonnet/gpt-4o/opus) cost more.
    TEXT_LLM_COST_USD: float = 0.002
    TEXT_LLM_COST_USD_STRONG: float = 0.01
    # 1-2: listings scoring <= this in an audit report are deactivated by the
    # shop-cleanup script / monthly dry-run tick (the critic's "erodes trust" band).
    SHOP_CLEANUP_MAX_SCORE: int = 3
    # 3-2: formats where the listing PREVIEW is basically the product get a tiled
    # watermark baked into the mockup's design layer (delivery files stay clean).
    WATERMARK_FORMATS: list = ["coloring_page", "phone_wallpaper"]
    WATERMARK_TEXT: str | None = None   # falls back to SHOP_NAME, then a default
    WATERMARK_ALPHA: int = 55           # 0-255 opacity of the tiled text
    SHOP_NAME: str | None = None

    # STEP 103 C-1: extra trademark/brand terms to block beyond the built-in
    # list, as a JSON array env, e.g. TRADEMARK_BLOCKLIST_EXTRA='["acme","foo"]'.
    # Screened against concept name/description, tags, and trend queries.
    TRADEMARK_BLOCKLIST_EXTRA: list = []

    # Real trend research (Google Trends via pytrends). Seed keywords the
    # TrendDataService pulls live search-interest data for; empty = use the
    # service's built-in SEED_KEYWORDS default. Lets the anchor list be tuned
    # without a redeploy-requiring code change.
    TREND_SEED_KEYWORDS: list = []
    # D-1: serve Google Trends data from a local cache within this many hours
    # (trends don't change hourly; re-fetching every cycle from one IP risks a
    # 429 ban that halts all autonomy). 0 disables caching.
    TREND_CACHE_HOURS: int = 12

    # Per-page QA for multi-page PDF planners/guides (step 100l). PDF pages are
    # text-and-layout heavy, so their content review is STRICTER than a generic
    # asset review (rejects photographs/decorative imagery on a functional page,
    # garbled/misspelled text, and stray meta-text). Uses its own model knob so a
    # stronger reader can be swapped in without changing the single-image gate;
    # defaults to the same cheap vision model.
    PDF_QA_MODEL: str = "openai/gpt-4o-mini"

    # Marketing/deliverable consistency remakes (step 100b): when the
    # consistency vision check finds a marketing image depicting a DIFFERENT
    # design than the delivery asset, regenerate ONLY the mismatched image(s)
    # with the vision model's own issue text as corrective feedback, then
    # re-check — up to this many total remake attempts PER TASK before falling
    # back to the hard BLOCKED_NO_PRODUCT behavior. Capped at 2 so a stubborn
    # mismatch can't spiral into unbounded regeneration cost (see cost note in
    # CHANGELOG_STEP100b): worst case ≈ 2 × (remake images + re-check).
    MARKETING_CONSISTENCY_MAX_REMAKES: int = 2

    # Digital single-image listing previews (step 100h): the listing photos
    # composite the real delivered design into a realistic scene at a PERSPECTIVE
    # ANGLE, so a buyer sees exactly what they get but a screenshot isn't a usable
    # flat copy (the clean, straight file is delivered only after purchase).
    # When True, listing/ad mockups composite the real design into a photorealistic
    # GENERATED scene (framed print on a wall, print in a desk flat-lay). When
    # False (or if generation fails), a deterministic PIL studio/desk background is
    # used. Either way the composited design is the REAL delivered file.
    MOCKUP_USE_GENERATED_SCENES: bool = True

    class Config:
        env_file = ".env"
        extra = "ignore"


settings = Settings()
