"""Environment configuration; credentials never appear in API responses."""

from functools import lru_cache
from pathlib import Path

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=Path(__file__).resolve().parents[2] / ".env",
        extra="ignore",
    )
    database_url: SecretStr
    redis_url: str = "redis://127.0.0.1:36379/0"
    session_secret: SecretStr
    gemini_api_key: SecretStr = SecretStr("")
    gemini_model: str = "gemini-3.5-flash-lite"
    model_calls_enabled: bool = False
    model_rpm_limit: int = 6
    model_concurrency_limit: int = 1
    model_daily_call_limit: int = 30
    owner_email: str = "owner@example.test"
    owner_password: SecretStr
    cookie_secure: bool = False


@lru_cache
def get_settings() -> Settings:
    return Settings()
