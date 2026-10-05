from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, model_validator
from zoneinfo import ZoneInfo
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=("../.env", ".env"), env_file_encoding="utf-8", extra="ignore",
        hide_input_in_errors=True,
    )

    assistant_env: Literal["development", "production", "test"] = "development"
    assistant_timezone: str = "America/New_York"
    assistant_user_name: str = "Dhruv"
    assistant_provider: Literal["auto", "mock", "ollama", "openai", "anthropic"] = "auto"
    assistant_auto_provider_preference: Literal["openai", "anthropic"] = "openai"
    assistant_memory_enabled: bool = False
    assistant_database_path: Path = Path("data/friday.db")
    assistant_reminder_poll_seconds: float = 1.0
    assistant_reminder_retry_seconds: float = 90.0
    assistant_reminder_max_attempts: int = 10
    assistant_job_timeout_seconds: float = Field(default=120.0, gt=0, le=600)
    assistant_job_max_age_seconds: float = Field(default=300.0, ge=10, le=86400)
    assistant_job_concurrency: int = Field(default=4, ge=1, le=16)
    assistant_confirmation_timeout_seconds: float = 300.0
    assistant_discovery_port: int = 8334
    assistant_public_host: str = ""
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

    music_provider: Literal["none", "synthetic", "spotify"] = "none"

    music_account_access_approved: bool = False
    music_credentials_path: Path = Path("data/disabled-spotify.json")
    music_timed_display_enabled: bool = False
    lyrics_metadata_sharing_approved: bool = False
    lyrics_client_identifier: str = ""
    music_poll_seconds: float = Field(default=3.0, ge=3, le=30)
    music_artwork_enabled: bool = False

    calendar_provider: Literal["none", "sample", "google"] = "none"
    calendar_model_context: bool = False
    calendar_google_id: str = Field(default="primary", min_length=1, max_length=256)
    calendar_google_credentials_path: Path = Path("data/google-calendar/credentials.json")

    weather_provider: Literal["none", "open-meteo"] = "none"
    weather_latitude: float | None = Field(default=None, ge=-90, le=90)
    weather_longitude: float | None = Field(default=None, ge=-180, le=180)

    tts_provider: Literal["none", "openai", "macos"] = "none"
    tts_model: str = "tts-1"
    tts_voice: str = "onyx"
    tts_local_voice: str = Field(default="Daniel (English (UK))", min_length=1, max_length=100)
    tts_local_rate: int = Field(default=180, ge=100, le=250)
    tts_local_gain: float = Field(default=0.25, gt=0, le=1)

    stt_provider: Literal["none", "faster-whisper"] = "faster-whisper"
    stt_model: str = "base.en"
    stt_device: str = "cpu"
    stt_compute_type: str = "int8"
    stt_language: str = "en"
    stt_max_seconds: int = Field(default=15, ge=1, le=60)

    @model_validator(mode="after")
    def validate_runtime(self):
        ZoneInfo(self.assistant_timezone)
        if self.music_artwork_enabled and self.music_provider != 'spotify':
            raise ValueError('Artwork requires explicit Spotify account opt in')
        if self.music_provider == 'spotify' and not self.music_account_access_approved:
            raise ValueError('Spotify requires explicit account-access opt in')
        if self.music_timed_display_enabled and (self.music_provider != 'spotify' or not self.lyrics_metadata_sharing_approved
            or not self.lyrics_client_identifier or len(self.lyrics_client_identifier)>200 or not self.lyrics_client_identifier.isascii()
            or any(ord(c)<32 or ord(c)==127 for c in self.lyrics_client_identifier)):
            raise ValueError('Timed lyrics require Spotify and approved metadata sharing with a truthful client identifier')
        if self.weather_provider != "none" and (self.weather_latitude is None or self.weather_longitude is None):
            raise ValueError("Opt-in weather requires explicit location coordinates")
        if self.tts_provider == "openai" and not self.openai_api_key:
            raise ValueError("Opt-in speech provider requires a locally configured key")
        if self.assistant_env == "production":
            import re
            tokens = (self.assistant_admin_token, self.assistant_simulator_token)
            if any(re.fullmatch(r"[0-9a-fA-F]{64}", token) is None for token in tokens):
                raise ValueError("Production requires valid admin and simulator credentials")
            if tokens[0].lower() == tokens[1].lower():
                raise ValueError("Admin and simulator credentials must differ")
            if self.assistant_public_scheme != "wss":
                raise ValueError("Production requires WSS")
            if self.assistant_provider in {"auto", "mock"}:
                raise ValueError("Production requires an explicit real provider")
            if self.assistant_provider == "openai" and not self.openai_api_key:
                raise ValueError("Selected OpenAI provider requires a locally configured key")
            if self.assistant_provider == "anthropic" and not self.anthropic_api_key:
                raise ValueError("Selected Anthropic provider requires a locally configured key")
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()
