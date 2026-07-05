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

    OPENAI_API_KEY: str | None = None
    ANTHROPIC_API_KEY: str | None = None
    DEFAULT_MODEL: str = "openai/gpt-4o-mini"

    ETSY_API_KEY: str | None = None
    ETSY_REDIRECT_URI: str = "http://localhost:8000/etsy/oauth/callback"
    ETSY_SHOP_ID: str | None = None

    PINTEREST_APP_ID: str | None = None
    PINTEREST_APP_SECRET: str | None = None
    PINTEREST_REDIRECT_URI: str = "http://localhost:8000/pinterest/oauth/callback"
    PINTEREST_BOARD_ID: str | None = None

    SECRET_KEY: str = "change_me"

    LOG_LEVEL: str = "info"

    class Config:
        env_file = ".env"
        extra = "ignore"


settings = Settings()
