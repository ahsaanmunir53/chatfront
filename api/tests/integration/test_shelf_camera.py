from __future__ import annotations

from typing import Any

import httpx
import pytest
from sqlalchemy import func, select

from chatfront.core.models import Tenant
from chatfront.media.models import Media
from chatfront.services import Services
from chatfront.stock.count import ShelfReading
from tests.integration.chat import photo, sign_in


async def test_ephemeral_shelf_frame_is_not_stored(
    app: tuple[httpx.AsyncClient, Services], monkeypatch: pytest.MonkeyPatch
) -> None:
    client, services = app

    async def read_shelf(*args: Any, **kwargs: Any) -> ShelfReading:
        del args, kwargs
        return ShelfReading(lines=[], note="ephemeral test")

    monkeypatch.setattr("chatfront.stock.routes.count.read_shelf", read_shelf)
    slug, auth = await sign_in(client)
    async with services.db.system() as session:
        tenant = await session.scalar(select(Tenant).where(Tenant.slug == slug))
    assert tenant is not None

    async with services.db.tenant(tenant.id) as session:
        before = await session.scalar(select(func.count(Media.id)))
    response = await client.post(
        "/api/owner/stock/count/photo?ephemeral=true",
        headers=auth,
        files={"file": ("frame.png", photo(), "image/png")},
    )
    assert response.status_code == 200, response.text
    assert response.json()["rows"] == []

    async with services.db.tenant(tenant.id) as session:
        after = await session.scalar(select(func.count(Media.id)))
    assert after == before
