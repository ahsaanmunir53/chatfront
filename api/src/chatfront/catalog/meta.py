"""The shop's products in its Meta Commerce catalog (feature 6), so they show in the WhatsApp
catalog and the Instagram and Facebook shop, whose buy links open the shop page's checkout.

One catalog item per variant, known by its catalog id (the product code, or code-n for a
product with options), so a cart sent from WhatsApp or a shop link maps straight back. Every
30 minutes, and when the owner asks, code sends every product on sale (prices, stock, options,
photo) with the Graph API's items_batch, and deletes what is no longer on sale. Meta needs a
photo for every item, so products without one are left out and listed for the owner. A few
minutes later code asks Meta how the batch went and keeps its errors for the console.

    python -m chatfront.catalog.meta --shop gulmohar-lawn --catalog-id <id> \\
        --token-env CATALOG_TOKEN

The token (a system user's, with catalog_management) is read from the environment variable
named, never typed, and stored sealed; without one the platform's META_CATALOG_TOKEN is used.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import uuid
from datetime import timedelta
from decimal import Decimal
from typing import TYPE_CHECKING, Any
from urllib.parse import urlencode

from sqlalchemy import select

from chatfront.audit.service import SYSTEM, record
from chatfront.catalog.models import CommerceCatalog, Variant
from chatfront.catalog.service import CatalogItem, load_items
from chatfront.core.clock import utcnow
from chatfront.core.countries import get_pack
from chatfront.core.db import Database
from chatfront.core.errors import AppError, NotFound
from chatfront.core.http import request
from chatfront.core.models import Tenant
from chatfront.core.secretbox import seal, unseal
from chatfront.core.settings import Settings, get_settings
from chatfront.jobs.queue import JobContext, enqueue

if TYPE_CHECKING:
    from chatfront.services import Services

SYNC_JOB = "catalog.sync"
SYNC_ALL_JOB = "catalog.sync_all"
CHECK_JOB = "catalog.check"
SYNC_EVERY = timedelta(minutes=30)
CHECK_AFTER = timedelta(minutes=2)
CHECK_TRIES = 5
_COLOUR_KEYS = ("colour", "color")
_DONE = frozenset({"finished", "error", "canceled", "cancelled"})


# -- what is sent ---------------------------------------------------------------------------


def price_text(tenant: Tenant, amount: int) -> str:
    """Meta's price format: the amount in major units and the currency code ("4850.00 PKR")."""
    digits = get_pack(tenant.country).minor_digits
    major = (Decimal(amount) / (10**digits)).quantize(Decimal(1).scaleb(-digits))
    return f"{major} {tenant.currency}"


def item_data(
    tenant: Tenant, item: CatalogItem, variant: Variant, *, link: str, image: str
) -> dict[str, Any]:
    product, options = item.product, {k: str(v) for k, v in variant.options.items()}
    extra = [v for k, v in options.items() if k not in (*_COLOUR_KEYS, "size")]
    title = f"{product.name} ({', '.join(extra)})" if extra else product.name
    on_sale = variant.active and (variant.stock is None or variant.stock > 0)
    data: dict[str, Any] = {
        "id": item.catalog_id(variant),
        "title": title[:100],
        "description": (product.description or product.name)[:5000],
        "availability": "in stock" if on_sale else "out of stock",
        "condition": "new",
        "price": price_text(tenant, item.price_of(variant)),
        "link": link,
        "image_link": image,
        "brand": tenant.name[:100],
    }
    if len(item.variants) > 1:
        data["item_group_id"] = product.code
    colour = next((options[k] for k in _COLOUR_KEYS if k in options), None)
    if colour:
        data["color"] = colour
    if size := options.get("size"):
        data["size"] = size
    if variant.stock is not None:
        data["quantity_to_sell_on_facebook"] = max(variant.stock, 0)
    return data


def batch(
    tenant: Tenant, items: list[CatalogItem], before: list[str], *, web_url: str, api_url: str
) -> tuple[list[dict[str, Any]], list[str], list[str]]:
    """The requests for one send, the item ids that will be in the catalog after it, and the
    products left out for want of a photo."""
    requests, sent, left_out = [], [], []
    for item in items:
        code = item.product.code
        if item.product.image_media_id is None:
            left_out.append(item.product.name)
            continue
        link = f"{web_url}/s/{tenant.slug}#p-{code}"
        image = f"{api_url}/api/shops/{tenant.slug}/products/{code}/photo"
        for variant in item.variants:
            if not variant.active:
                continue
            data = item_data(tenant, item, variant, link=link, image=image)
            requests.append({"method": "UPDATE", "data": data})
            sent.append(data["id"])
    gone = [{"method": "DELETE", "data": {"id": old}} for old in before if old not in set(sent)]
    return requests + gone, sent, left_out


# -- talking to Meta ------------------------------------------------------------------------


def _graph(settings: Settings) -> str:
    return f"{settings.meta_graph_base_url.rstrip('/')}/{settings.meta_graph_version}"


def _token(services: Services, link: CommerceCatalog) -> str:
    secret = services.settings.secret_key.get_secret_value()
    if link.access_token_sealed:
        return unseal(link.access_token_sealed, secret)
    fallback = services.settings.meta_catalog_token
    if fallback is None:
        raise AppError("catalog_token_missing", "No catalog token is set")
    return fallback.get_secret_value()


async def _catalog(services: Services, tenant_id: uuid.UUID) -> tuple[Tenant, CommerceCatalog]:
    async with services.db.tenant(tenant_id) as session:
        tenant = await session.get(Tenant, tenant_id)
        link = await session.scalar(select(CommerceCatalog))
    if tenant is None or link is None:
        raise AppError("catalog_not_connected", "No Meta catalog is connected to this shop")
    return tenant, link


async def _save(services: Services, tenant_id: uuid.UUID, **values: Any) -> None:
    async with services.db.tenant(tenant_id) as session:
        link = await session.scalar(select(CommerceCatalog))
        assert link is not None
        for name, value in values.items():
            setattr(link, name, value)


async def push(services: Services, tenant_id: uuid.UUID) -> int:
    """Send the shop's products to its catalog; returns how many items were sent."""
    tenant, link = await _catalog(services, tenant_id)
    async with services.db.tenant(tenant_id) as session:
        items = await load_items(session)
    settings = services.settings
    requests, sent, left_out = batch(
        tenant,
        items,
        list(link.synced_ids),
        web_url=settings.web_url,
        api_url=settings.public_base_url.rstrip("/"),
    )
    response = await request(
        services.http,
        "POST",
        f"{_graph(settings)}/{link.catalog_id}/items_batch",
        service="catalog",
        content=urlencode(
            {"item_type": "PRODUCT_ITEM", "requests": json.dumps(requests, ensure_ascii=False)}
        ).encode(),
        headers={
            "Authorization": f"Bearer {_token(services, link)}",
            "Content-Type": "application/x-www-form-urlencoded",
        },
        retries=0,
    )
    answer = response.json()
    errors = [
        {"id": row.get("retailer_id"), "message": str(error.get("message", ""))[:300]}
        for row in answer.get("validation_status") or []
        for error in row.get("errors") or []
    ]
    handle = next(iter(answer.get("handles") or []), None)
    await _save(
        services,
        tenant_id,
        synced_ids=sent,
        status="sent" if handle else "error",
        handle=handle,
        errors=errors,
        left_out=left_out,
        synced_at=utcnow(),
    )
    if handle:
        async with services.db.tenant(tenant_id) as session:
            await enqueue(
                session,
                CHECK_JOB,
                {"handle": handle, "try": 1},
                tenant_id=tenant_id,
                run_at=utcnow() + CHECK_AFTER,
            )
    return len(sent)


async def check(services: Services, tenant_id: uuid.UUID, handle: str) -> str:
    """Ask Meta how a batch went; keep its status and errors. Returns the status."""
    _, link = await _catalog(services, tenant_id)
    if link.handle != handle:
        return "superseded"  # a newer send has taken its place
    response = await request(
        services.http,
        "GET",
        f"{_graph(services.settings)}/{link.catalog_id}/check_batch_request_status",
        service="catalog",
        params={
            "handle": handle,
            "load_ids_of_invalid_requests": "true",
            "fields": "handle,status,errors,errors_total_count,ids_of_invalid_requests",
        },
        headers={"Authorization": f"Bearer {_token(services, link)}"},
    )
    found: dict[str, Any] = next(iter(response.json().get("data") or []), {})
    status = str(found.get("status", "unknown")).lower()
    errors = [
        {"id": error.get("id"), "message": str(error.get("message", ""))[:300]}
        for error in found.get("errors") or []
    ]
    await _save(services, tenant_id, status=status, errors=errors or list(link.errors))
    return status


# -- jobs -----------------------------------------------------------------------------------


async def sync_job(ctx: JobContext) -> None:
    assert ctx.tenant_id is not None
    try:
        await push(ctx.services, ctx.tenant_id)
    except AppError as exc:
        await _save(ctx.services, ctx.tenant_id, status="error", errors=[{"message": exc.message}])


async def sync_all_job(ctx: JobContext) -> None:
    """Every connected shop, every 30 minutes; a shop with nothing new costs one call."""
    services: Services = ctx.services
    async with services.db.system() as session:
        tenants = list(await session.scalars(select(CommerceCatalog.tenant_id)))
    bucket = int(utcnow().timestamp() // SYNC_EVERY.total_seconds())
    for tenant_id in tenants:
        async with services.db.tenant(tenant_id) as session:
            await enqueue(
                session, SYNC_JOB, tenant_id=tenant_id, dedupe_key=f"catalog:{tenant_id}:{bucket}"
            )


async def check_job(ctx: JobContext) -> None:
    assert ctx.tenant_id is not None
    handle, tries = str(ctx.payload["handle"]), int(ctx.payload.get("try", 1))
    try:
        status = await check(ctx.services, ctx.tenant_id, handle)
    except AppError:
        status = "unknown"
    if status not in _DONE and status != "superseded" and tries < CHECK_TRIES:
        async with ctx.services.db.tenant(ctx.tenant_id) as session:
            await enqueue(
                session,
                CHECK_JOB,
                {"handle": handle, "try": tries + 1},
                tenant_id=ctx.tenant_id,
                run_at=utcnow() + CHECK_AFTER,
            )


# -- connecting a catalog -------------------------------------------------------------------


async def connect(
    db: Database, settings: Settings, *, shop: str, catalog_id: str, token: str | None
) -> CommerceCatalog:
    """Connect or change the shop's catalog; the next send starts from nothing."""
    sealed = seal(token, settings.secret_key.get_secret_value()) if token else None
    async with db.system() as session:
        tenant = await session.scalar(select(Tenant).where(Tenant.slug == shop))
        if tenant is None:
            raise NotFound("Shop")
    async with db.tenant(tenant.id) as session:
        link = await session.scalar(select(CommerceCatalog))
        if link is None:
            link = CommerceCatalog(tenant_id=tenant.id, catalog_id=catalog_id)
            session.add(link)
        link.catalog_id, link.synced_ids, link.status = catalog_id, [], "never"
        if sealed:
            link.access_token_sealed = sealed
        await session.flush()
        await record(
            session,
            tenant.id,
            SYSTEM,
            "catalog.connected",
            entity=("commerce_catalog", str(link.id)),
            data={"catalog_id": catalog_id},
        )
    return link


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=(__doc__ or "").split("\n")[0])
    parser.add_argument("--shop", required=True, help="the shop's slug, e.g. gulmohar-lawn")
    parser.add_argument("--catalog-id", required=True, help="from Commerce Manager")
    parser.add_argument("--token-env", help="the environment variable holding the token")
    args = parser.parse_args(argv)
    token = os.environ.get(args.token_env) if args.token_env else None
    if args.token_env and not token:
        parser.error(f"{args.token_env} is not set")
    settings = get_settings()

    async def run() -> None:
        db = Database(settings.database_url, pool_size=1)
        try:
            await connect(db, settings, shop=args.shop, catalog_id=args.catalog_id, token=token)
        finally:
            await db.dispose()

    asyncio.run(run())
    print(f"Connected catalog {args.catalog_id} to {args.shop}.")


if __name__ == "__main__":
    main()
