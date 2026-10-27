"""All configuration comes from the environment; .env.example documents every variable."""

from __future__ import annotations

from decimal import Decimal
from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

REPO_ROOT = Path(__file__).resolve().parents[4]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", env_ignore_empty=True, extra="ignore"
    )

    env: Literal["dev", "test", "prod"] = "dev"
    public_base_url: str = "http://localhost:8000"
    log_level: str = "INFO"

    # Database: one URL as the owner role; tenant work switches to the app role (see core.db).
    database_url: str = "postgresql+asyncpg://chatfront_owner:owner@127.0.0.1:5432/chatfront"
    db_pool_size: int = 5

    # Secrets used to sign and hash. They must be long random strings in production.
    secret_key: SecretStr = SecretStr("dev-only-secret-key-change-me-0000000000")
    phone_hash_key: SecretStr = SecretStr("dev-only-phone-hash-key-change-me-00000")
    cron_secret: SecretStr = SecretStr("dev-only-cron-secret")

    # Meta platform (WhatsApp Cloud API, Instagram Messaging, Messenger).
    meta_app_secret: SecretStr | None = None
    meta_verify_token: SecretStr | None = None
    meta_graph_version: str = "v23.0"
    meta_graph_base_url: str = "https://graph.facebook.com"
    whatsapp_access_token: SecretStr | None = None

    # Models. "auto" calls a provider when its key is set and the mock otherwise.
    ai_mode: Literal["auto", "mock"] = "auto"
    baseten_api_key: SecretStr | None = None
    baseten_base_url: str = "https://inference.baseten.co/v1"
    spark_model_baseten: str = "meta-models/muse-spark-open"
    fireworks_api_key: SecretStr | None = None
    fireworks_base_url: str = "https://api.fireworks.ai/inference/v1"
    spark_model_fireworks: str = "accounts/fireworks/models/muse-spark-open"
    modal_glimmer_base_url: str | None = None
    modal_glimmer_api_key: SecretStr | None = None
    glimmer_model: str = "meta-models/Muse-Glimmer-30B"
    meta_model_api_key: SecretStr | None = None
    meta_model_api_base_url: str | None = None
    meta_model_api_enabled: bool = False
    meta_spark_model: str = "muse-spark-1.3"
    ai_timeout_seconds: float = 60.0
    usd_to_pkr: Decimal = Decimal("280")
    ai_record_dir: Path | None = None

    # Media storage.
    storage_backend: Literal["local", "supabase"] = "local"
    local_media_dir: Path = REPO_ROOT / ".media"
    supabase_url: str | None = None
    supabase_service_key: SecretStr | None = None
    supabase_bucket: str = "chatfront-media"
    max_upload_bytes: int = 20 * 1024 * 1024
    signed_url_ttl_seconds: int = 3600

    # Behaviour.
    worker_enabled: bool = True
    worker_poll_seconds: float = 1.0
    worker_batch_size: int = 5
    i18n_dir: Path = REPO_ROOT / "i18n"
    web_dist_dir: Path = REPO_ROOT / "web" / "dist"
    public_rate_limit_per_minute: int = 30

    @field_validator("database_url")
    @classmethod
    def _use_asyncpg(cls, value: str) -> str:
        for prefix in ("postgresql://", "postgres://"):
            if value.startswith(prefix):
                return "postgresql+asyncpg://" + value.removeprefix(prefix)
        return value

    def require_production_secrets(self) -> None:
        """Refuse to start in production with any of the development secrets."""
        if self.env != "prod":
            return
        for name in ("secret_key", "phone_hash_key", "cron_secret"):
            secret: SecretStr = getattr(self, name)
            if secret.get_secret_value().startswith("dev-only") or len(secret) < 32:
                raise RuntimeError(f"{name.upper()} must be set to a random value of 32+ chars")


@lru_cache
def get_settings() -> Settings:
    return Settings()
