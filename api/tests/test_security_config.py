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


@pytest.mark.asyncio
async def test_rate_limit_error_includes_retry_after() -> None:
    from chatfront.core.errors import RateLimited

    app = create_app(Settings(env="test", worker_enabled=False))

    @app.get("/security-rate-limit-test")
    async def rate_limit_test() -> None:
        raise RateLimited()

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/security-rate-limit-test")
    assert response.status_code == 429
    assert response.headers["retry-after"] == "60"


def test_owner_session_claims_include_version_and_reject_oversized_tokens() -> None:
    import uuid
    from datetime import timedelta

    from chatfront.core.clock import utcnow
    from chatfront.owner.sessions import MAX_TOKEN_LENGTH, NotSignedIn, issue, verify

    staff_id, tenant_id = uuid.uuid4(), uuid.uuid4()
    token = issue(staff_id, tenant_id, "test-key", timedelta(minutes=5), session_version=7)
    parsed = verify(token, "test-key")
    assert parsed.staff_id == staff_id
    assert parsed.tenant_id == tenant_id
    assert parsed.session_version == 7
    assert parsed.expires_at > utcnow()

    with pytest.raises(NotSignedIn):
        verify("x" * (MAX_TOKEN_LENGTH + 1), "test-key")
    with pytest.raises(NotSignedIn):
        verify(token, "different-key")


def test_recent_sign_in_is_required_for_sensitive_changes() -> None:
    import uuid
    from datetime import timedelta

    from chatfront.core.clock import utcnow
    from chatfront.owner.sessions import (
        FreshLinkRequired,
        OwnerSession,
        require_recent,
    )

    now = utcnow()
    recent = OwnerSession(
        uuid.uuid4(), uuid.uuid4(), now + timedelta(days=1), 3, now - timedelta(minutes=5)
    )
    stale = OwnerSession(
        recent.staff_id,
        recent.tenant_id,
        recent.expires_at,
        recent.session_version,
        now - timedelta(minutes=16),
    )
    missing_issue_time = OwnerSession(
        recent.staff_id, recent.tenant_id, recent.expires_at, recent.session_version, None
    )
    require_recent(recent, now=now)
    with pytest.raises(FreshLinkRequired, match="fresh sign-in link"):
        require_recent(stale, now=now)
    with pytest.raises(FreshLinkRequired):
        require_recent(missing_issue_time, now=now)


def test_session_verification_preserves_issue_time_and_version() -> None:
    import uuid

    from chatfront.owner.sessions import issue, verify

    staff_id, tenant_id = uuid.uuid4(), uuid.uuid4()
    token = issue(staff_id, tenant_id, "test-key", session_version=9)
    session = verify(token, "test-key")
    assert session.session_version == 9
    assert session.issued_at is not None
    assert session.staff_id == staff_id
    assert session.tenant_id == tenant_id


def test_owner_route_permissions_are_explicit() -> None:
    from fastapi.routing import APIRoute

    from chatfront.main import create_app
    from chatfront.core.settings import Settings

    app = create_app(Settings(env="test", worker_enabled=False))
    violations: list[str] = []

    def dependency_tree(dependant: object) -> list[object]:
        children = list(getattr(dependant, "dependencies", ()))
        result = [dependant]
        for child in children:
            result.extend(dependency_tree(child))
        return result

    for route in app.routes:
        if not isinstance(route, APIRoute):
            continue
        path = route.path
        if path.startswith("/api/owner/") and path != "/api/owner/demo":
            deps = dependency_tree(route.dependant)
            names = {getattr(dep.call, "__name__", "") for dep in deps}
            has_right = any(name == "check" for name in names)
            has_active_staff = any(name in {"active_staff", "recent_staff"} for name in names)
            if not (has_right or has_active_staff):
                violations.append(f"{','.join(sorted(route.methods or []))} {path}")
        if path.startswith("/api/platform/"):
            deps = dependency_tree(route.dependant)
            names = {getattr(dep.call, "__name__", "") for dep in deps}
            if "reviewer" not in names:
                violations.append(f"platform reviewer missing: {path}")

    assert violations == [], "Unprotected routes: " + "; ".join(violations)
