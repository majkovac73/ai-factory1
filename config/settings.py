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
    POD_TARGET_PROFIT_USD: float = 6.00
    POD_SHIPPING_ESTIMATE_USD: float = 5.00
    POD_ETSY_FEE_FRACTION: float = 0.10  # ~6.5% transaction + ~3% payment

    ETSY_SHIPPING_PROFILE_ID: str | None = None
    ETSY_SHOP_ORIGIN_COUNTRY: str = "US"  # ISO 3166-1 alpha-2; set in env if shop is not US-based

    # STEP 103 C-2: Etsy Creativity Standards require POD listings to DECLARE
    # their production partner (Printify). Maj adds Printify in Shop Manager
    # (Settings → Production partners), then GET /shops/{id}/production-partners
    # for the id and sets this env; POD listings then send production_partner_ids.
    # Unset = not sent (safe, but POD listings are then non-compliant).
    ETSY_PRODUCTION_PARTNER_ID: str | None = None
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
    BACKUP_KEEP_LOCAL: int = 7
    BACKUP_S3_BUCKET: str | None = None
    BACKUP_S3_ENDPOINT_URL: str | None = None   # e.g. https://<acct>.r2.cloudflarestorage.com
    BACKUP_S3_ACCESS_KEY_ID: str | None = None
    BACKUP_S3_SECRET_ACCESS_KEY: str | None = None
    BACKUP_S3_REGION: str = "auto"

    MAX_TASKS_PER_DAY: int = 10
    MAX_DAILY_SPEND_USD: float = 5.00

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

    # Hard cap on pages for a multi-page PDF product (step 91). Each page is a
    # real, billable image-generation call — this must never be unbounded.
    MAX_PDF_PAGES: int = 6

    # Content-quality gate (step 96): a VISION-capable model inspects the
    # actual generated delivery asset for legibility/coherence/correctness —
    # image-generation models garble text (e.g. "2 þutter"), which no
    # structural check catches. gpt-4o-mini is vision-capable and cheap; a
    # stronger model (openai/gpt-4o, a Claude vision model) can be swapped in
    # here if QA accuracy needs improving.
    CONTENT_QA_MODEL: str = "openai/gpt-4o-mini"
    CONTENT_QA_MAX_ATTEMPTS: int = 2  # regenerate-and-recheck attempts before blocking

    # Product-viability critic (step 102): an independent LLM judgment step that
    # scores a schema-valid concept 1-10 on whether a real stranger would
    # actually buy THIS specific item, and rejects (fails) anything scoring
    # below this threshold. Pass/fail is derived from the score in code (not the
    # model's own inconsistent internal bar) so it's tunable without a prompt
    # change. 6 = "a real niche buyer would plausibly purchase this"; raise to be
    # stricter, lower to be more permissive. Calibrated against manual spot-checks
    # so genuinely sellable products pass and generic/low-effort ones fail.
    VIABILITY_CRITIC_MIN_SCORE: int = 6

    # STEP 103 C-1: extra trademark/brand terms to block beyond the built-in
    # list, as a JSON array env, e.g. TRADEMARK_BLOCKLIST_EXTRA='["acme","foo"]'.
    # Screened against concept name/description, tags, and trend queries.
    TRADEMARK_BLOCKLIST_EXTRA: list = []

    # Real trend research (Google Trends via pytrends). Seed keywords the
    # TrendDataService pulls live search-interest data for; empty = use the
    # service's built-in SEED_KEYWORDS default. Lets the anchor list be tuned
    # without a redeploy-requiring code change.
    TREND_SEED_KEYWORDS: list = []

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
