from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=("../.env", ".env"), env_file_encoding="utf-8", extra="ignore"
    )

    assistant_env: str = "development"
    assistant_timezone: str = "America/New_York"
    assistant_user_name: str = "Dhruv"
    assistant_provider: Literal["auto", "mock", "ollama", "openai", "anthropic"] = "auto"
    assistant_auto_provider_preference: Literal["openai", "anthropic"] = "openai"
    assistant_database_path: Path = Path("data/friday.db")
    assistant_reminder_poll_seconds: float = 1.0
    assistant_reminder_retry_seconds: float = 90.0
    assistant_reminder_max_attempts: int = 10
    assistant_job_timeout_seconds: float = 120.0
    assistant_job_max_age_seconds: float = 300.0
    assistant_job_concurrency: int = 4
    assistant_confirmation_timeout_seconds: float = 300.0
    assistant_discovery_port: int = 8334
    assistant_public_port: int = 8000
    assistant_public_scheme: Literal["ws", "wss"] = "wss"
    assistant_simulator_token: str = ""
    assistant_simulator_device_id: str = "friday-p3a"
    assistant_admin_token: str = ""

    openai_api_key: str = ""
    anthropic_api_key: str = ""
    ollama_base_url: str = "http://127.0.0.1:11434"
    ollama_model: str = "qwen3.6:latest"
    openai_text_model: str = "gpt-5.4-mini"
    anthropic_text_model: str = "claude-sonnet-5"
    anthropic_max_tokens: int = Field(default=1024, ge=256, le=128000)
    openai_realtime_model: str = "gpt-realtime-2.1"

    stt_provider: Literal["none", "faster-whisper"] = "faster-whisper"
    stt_model: str = "base.en"
    stt_device: str = "cpu"
    stt_compute_type: str = "int8"
    stt_language: str = "en"
    stt_max_seconds: int = 15


@lru_cache
def get_settings() -> Settings:
    return Settings()
