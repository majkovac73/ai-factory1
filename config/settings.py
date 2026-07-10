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

    SECRET_KEY: str = "change_me"

    LOG_LEVEL: str = "info"

    PRINTIFY_API_KEY: str | None = None
    PRINTIFY_SHOP_ID: str | None = None

    ETSY_SHIPPING_PROFILE_ID: str | None = None
    ETSY_SHOP_ORIGIN_COUNTRY: str = "US"  # ISO 3166-1 alpha-2; set in env if shop is not US-based

    ETSY_RECEIPT_POLL_SECONDS: int = 300

    # Persistent storage paths — override in Railway env vars:
    #   DATABASE_PATH=/data/app.db
    #   IMAGE_STORAGE_ROOT=/data/images
    DATABASE_PATH: str | None = None
    IMAGE_STORAGE_ROOT: str | None = None

    DISCORD_WEBHOOK_URL: str | None = None

    MAX_TASKS_PER_DAY: int = 10
    MAX_DAILY_SPEND_USD: float = 5.00
    AUTONOMY_ENABLED: bool = False
    AUTONOMY_SCHEDULE_SECONDS: int = 3600

    AUTO_PUBLISH_LISTINGS: bool = False

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
