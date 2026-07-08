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

    class Config:
        env_file = ".env"
        extra = "ignore"


settings = Settings()
