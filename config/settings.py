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

    SECRET_KEY: str = "change_me"

    LOG_LEVEL: str = "info"

    class Config:
        env_file = ".env"
        extra = "ignore"


settings = Settings()
