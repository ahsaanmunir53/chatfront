"""All configuration comes from the environment; .env.example documents every variable."""

from __future__ import annotations

from decimal import Decimal
from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

REPO_ROOT = Path(__file__).resolve().parents[4]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", env_ignore_empty=True, extra="ignore"
    )

    env: Literal["dev", "test", "prod"] = "dev"
    public_base_url: str = "http://localhost:8000"
    # Where the web app lives, for links sent in chats. Empty means PUBLIC_BASE_URL.
    web_base_url: str | None = None
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
    # Owner alerts outside the 24-hour window go as this approved utility template (one body
    # parameter); without it they wait in the console (feature 118).
    whatsapp_alert_template: str | None = None
    whatsapp_alert_template_lang: str = "en"
    whatsapp_offer_template: str | None = None
    whatsapp_offer_template_lang: str = "en"
    # The owner panel's WhatsApp Flow, once published in WhatsApp Manager (feature 118).
    whatsapp_flow_product_id: str | None = None
    whatsapp_flow_booking_id: str | None = None  # the booking form (feature 18)
    # Instagram API with Instagram Login signs webhooks with its own app secret.
    instagram_app_secret: SecretStr | None = None
    instagram_graph_base_url: str = "https://graph.instagram.com"
    instagram_access_token: SecretStr | None = None
    messenger_page_access_token: SecretStr | None = None
    meta_catalog_token: SecretStr | None = None  # catalog_management; per shop if sealed

    # Google Safe Browsing (feature 40): links in chats are also checked against Google's
    # lists of phishing and malware sites; without a key, code's own checks run alone.
    safe_browsing_api_key: SecretStr | None = None
    safe_browsing_url: str = "https://safebrowsing.googleapis.com/v4/threatMatches:find"

    # Models. "auto" calls a provider when its key is set and the mock otherwise.
    ai_mode: Literal["auto", "mock"] = "auto"
    baseten_api_key: SecretStr | None = None
    baseten_base_url: str = "https://inference.baseten.co/v1"
    spark_model_baseten: str = "meta-models/muse-spark-open-1026"
    fireworks_api_key: SecretStr | None = None
    fireworks_base_url: str = "https://api.fireworks.ai/inference/v1"
    spark_model_fireworks: str = "accounts/fireworks/models/muse-spark-open"
    modal_spark_base_url: str | None = None
    modal_spark_api_key: SecretStr | None = None
    spark_model_modal: str = "muse-spark-open"
    # Optional local OpenAI-compatible Glimmer endpoint, reachable from this API process.
    glimmer_base_url: str | None = None
    glimmer_api_key: SecretStr | None = None
    # When enabled, all AI roles use only GLIMMER_BASE_URL; no remote model failover.
    ai_privacy_mode: bool = False
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

    # Signed provider events for network payments. This verifies callbacks but does not create
    # hosted checkout sessions or transfer funds to sellers.
    marketplace_provider: str | None = None
    marketplace_webhook_secret: SecretStr | None = None
    marketplace_fee_bps: int = Field(default=500, ge=0, le=3000)

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
    # Alerts on staff phones (feature 115): the VAPID key from `python -m chatfront.push`, and
    # an address push services may write to (empty: the public URL).
    vapid_private_key: SecretStr | None = None
    vapid_subject: str | None = None
    # The customer simulator's pace (feature 110): seconds between arrivals and between a
    # simulated customer's messages are a small multiple of this; 0 in tests.
    sim_pace_seconds: float = 2.0
    i18n_dir: Path = REPO_ROOT / "i18n"
    web_dist_dir: Path = REPO_ROOT / "web" / "dist"
    public_rate_limit_per_minute: int = 30
    # Origins allowed to call the API from a browser, comma-separated: the static frontend
    # when it is hosted apart from the API (for example on GitHub Pages).
    cors_origins: str = ""

    @property
    def web_url(self) -> str:
        return (self.web_base_url or self.public_base_url).rstrip("/")

    @property
    def cors_origin_list(self) -> list[str]:
        return [
            origin.strip().rstrip("/") for origin in self.cors_origins.split(",") if origin.strip()
        ]

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
