from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=("../.env", ".env"), env_file_encoding="utf-8", extra="ignore"
    )

    assistant_env: str = "development"
    assistant_timezone: str = "America/New_York"
    assistant_user_name: str = "Dhruv"
    assistant_provider: Literal["auto", "mock", "openai", "anthropic"] = "auto"
    assistant_database_path: Path = Path("data/friday.db")

    openai_api_key: str = ""
    anthropic_api_key: str = ""
    openai_text_model: str = "gpt-5.4-mini"
    anthropic_text_model: str = "claude-sonnet-4-5"
    openai_realtime_model: str = "gpt-realtime-2.1"


@lru_cache
def get_settings() -> Settings:
    return Settings()

