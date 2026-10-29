"""Focused regression tests for production security configuration and response headers."""

from __future__ import annotations

import httpx
import pytest
from pydantic import SecretStr, ValidationError

from chatfront.core.settings import Settings
from chatfront.main import create_app


def production_settings(**overrides: object) -> Settings:
    values: dict[str, object] = {
        "env": "prod",
        "secret_key": SecretStr("a" * 48),
        "phone_hash_key": SecretStr("b" * 48),
        "cron_secret": SecretStr("c" * 48),
        "worker_enabled": False,
    }
    values.update(overrides)
    return Settings(**values)


@pytest.mark.parametrize(
    "origins",
    [
        "*",
        "https://good.example/path",
        "https://good.example?query=1",
        "http://insecure.example",
        "https://user:pass@good.example",
    ],
)
def test_production_cors_rejects_unsafe_origins(origins: str) -> None:
    with pytest.raises(ValidationError):
        production_settings(cors_origins=origins)


def test_production_cors_accepts_explicit_https_origins() -> None:
    settings = production_settings(cors_origins="https://shop.example, https://admin.example")
    assert settings.cors_origin_list == ["https://shop.example", "https://admin.example"]


@pytest.mark.parametrize("field", ["secret_key", "phone_hash_key", "cron_secret"])
def test_production_requires_independent_long_secrets(field: str) -> None:
    settings = production_settings(**{field: SecretStr("short")})
    with pytest.raises(RuntimeError, match="32 bytes"):
        settings.require_production_secrets()


def test_production_rejects_development_secret_defaults() -> None:
    settings = production_settings(secret_key=SecretStr("dev-only-" + "x" * 48))
    with pytest.raises(RuntimeError, match="SECRET_KEY"):
        settings.require_production_secrets()


@pytest.mark.asyncio
async def test_security_headers_are_set_on_api_responses() -> None:
    app = create_app(Settings(env="test", worker_enabled=False))
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/security-test-route-that-does-not-exist")
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["x-frame-options"] == "DENY"
    assert response.headers["referrer-policy"] == "no-referrer"
    assert "frame-ancestors 'none'" in response.headers["content-security-policy"]
    assert "strict-transport-security" not in response.headers
