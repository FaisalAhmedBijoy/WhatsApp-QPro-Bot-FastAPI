from functools import lru_cache
from pathlib import Path
from typing import Annotated, Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

BASE_DIR = Path(__file__).resolve().parent

# Comma-separated in .env, e.g. AUTHORIZED_NUMBERS=8801711000000,8801811000000
CsvList = Annotated[list[str], NoDecode]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # --- WhatsApp Cloud API ---
    # "dry_run" logs outgoing messages instead of calling Meta (no credentials needed).
    whatsapp_mode: Literal["cloud", "dry_run"] = "dry_run"
    whatsapp_access_token: str = ""
    whatsapp_phone_number_id: str = ""
    whatsapp_app_secret: str = ""  # used to verify X-Hub-Signature-256; empty = skip check (dev only)
    whatsapp_verify_token: str = "change-me"
    graph_api_version: str = "v21.0"
    graph_api_base_url: str = "https://graph.facebook.com"

    # --- Token provider ---
    token_provider: Literal["mock", "queuepro"] = "mock"
    # Empty = call the mock API in-process (no network hop). Set a URL to use a standalone mock.
    mock_token_api_url: str = ""
    queuepro_token_api_url: str = ""
    queuepro_api_key: str = ""
    token_api_timeout_seconds: float = 10.0

    # --- Mock API behaviour (demo / testing) ---
    mock_latency_ms: int = 0
    mock_failure_rate: float = 0.0
    mock_closed_branches: CsvList = Field(default_factory=list)

    # --- Sessions ---
    session_backend: Literal["memory", "redis"] = "memory"
    redis_url: str = "redis://localhost:6379/0"
    session_ttl_seconds: int = 600
    last_branch_ttl_seconds: int = 60 * 60 * 24 * 90

    # --- Access control ---
    # Empty = everyone may request tokens. Otherwise only these WhatsApp numbers (e.g. 8801711000000).
    authorized_numbers: CsvList = Field(default_factory=list)

    # --- Misc ---
    timezone: str = "Asia/Dhaka"
    master_data_dir: Path = BASE_DIR / "masterdata" / "data"
    media_dir: Path = BASE_DIR.parent / "var" / "media"
    enable_simulator: bool = True
    log_level: str = "INFO"

    @field_validator("mock_closed_branches", "authorized_numbers", mode="before")
    @classmethod
    def _split_csv(cls, value: object) -> object:
        if isinstance(value, str):
            return [item.strip() for item in value.split(",") if item.strip()]
        return value


@lru_cache
def get_settings() -> Settings:
    return Settings()
