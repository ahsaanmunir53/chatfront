"""Products the shop already has, brought in as drafts (features 85, 84 and 2): from a sheet,
its Meta catalog, its Instagram posts or a video of a rack.

Every import ends the same way. What it finds waits as drafts in the console, each with its
photo when there is one, where its price was read, and what is still missing. Nothing is on
sale until the owner adds a draft; then code builds the product from it, checked against the
shop's template like any other. A price read from a picture or a video is shown for the owner
to check before adding.
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass, field
from datetime import timedelta
from enum import StrEnum
from typing import TYPE_CHECKING, Any
from urllib.parse import urlsplit

from sqlalchemy import func, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from chatfront.audit.service import Actor, record
from chatfront.catalog.drafts import ProductDraft, to_input
from chatfront.catalog.models import CatalogImport, CommerceCatalog, ImportDraft, ProductStatus
from chatfront.catalog.service import CatalogItem, create_product, find_products, load_items
from chatfront.catalog.translations import TRANSLATE_JOB
from chatfront.channels.models import Channel, SocialPost
from chatfront.core.clock import utcnow
from chatfront.core.countries import get_pack
from chatfront.core.db import Database
from chatfront.core.errors import AppError, NotFound
from chatfront.core.http import request
from chatfront.core.models import Tenant
from chatfront.jobs.queue import JobContext, enqueue
from chatfront.media import service as media_service
from chatfront.media.models import MediaKind
from chatfront.templates import get_template

if TYPE_CHECKING:
    from chatfront.services import Services

log = logging.getLogger(__name__)

MAX_DRAFTS = 100  # what one import may find
SAME_NAME = 0.9  # how close a name must be to an existing product's to wait for the owner
STUCK_AFTER = timedelta(minutes=20)  # a run still reading after this no longer blocks another
# Photos are fetched only from Meta's own servers, where Graph API answers point.
PHOTO_HOSTS = ("fbcdn.net", "cdninstagram.com", "fbsbx.com")


class Source(StrEnum):
    SHEET = "sheet"
    META_CATALOG = "meta_catalog"
    INSTAGRAM = "instagram"
    VIDEO = "video"


class RunStatus(StrEnum):
    READING = "reading"
    READY = "ready"
    FAILED = "failed"


class DraftStatus(StrEnum):
    WAITING = "waiting"
    ADDED = "added"
    DROPPED = "dropped"


@dataclass(frozen=True)
class Found:
    """One product an import found."""

    ref: str  # the post, catalog item, sheet row or moment in the video it came from
    draft: ProductDraft
    price_from: str | None = None  # sheet, catalog, caption, image, said or tag
    image_media_id: uuid.UUID | None = None
    extra: dict[str, Any] = field(default_factory=dict)


# -- an import's life -----------------------------------------------------------------------


async def start(
    session: AsyncSession,
    tenant_id: uuid.UUID,
    source: Source,
    label: str,
    staff_id: uuid.UUID | None,
) -> CatalogImport:
    """Open an import; refused while another of the same kind is still reading."""
    busy = await session.scalar(
        select(CatalogImport.id).where(
            CatalogImport.source == source,
            CatalogImport.status == RunStatus.READING,
            CatalogImport.created_at > utcnow() - STUCK_AFTER,
        )
    )
    if busy is not None:
        raise AppError("import_running", "This import is still reading", status_code=409)
    run = CatalogImport(
        tenant_id=tenant_id,
        source=source,
        label=label[:160],
        status=RunStatus.READING,
        staff_id=staff_id,
    )
    session.add(run)
    await session.flush()
    return run


async def keep(session: AsyncSession, run: CatalogImport, found: Sequence[Found]) -> int:
    """Save what was found as drafts; something drafted before is not drafted again."""
    kept = 0
    for one in found[:MAX_DRAFTS]:
        stored = await session.scalar(
            insert(ImportDraft)
            .values(
                id=uuid.uuid4(),
                tenant_id=run.tenant_id,
                import_id=run.id,
                source=run.source,
                source_ref=one.ref[:120],
                data=one.draft.model_dump(),
                price_from=one.price_from,
                extra=one.extra,
                image_media_id=one.image_media_id,
                status=DraftStatus.WAITING,
            )
            .on_conflict_do_nothing(index_elements=["tenant_id", "source", "source_ref"])
            .returning(ImportDraft.id)
        )
        kept += stored is not None
    return kept


async def finish(
    session: AsyncSession, run_id: uuid.UUID, *, error: str | None = None
) -> CatalogImport:
    run = await session.get(CatalogImport, run_id)
    if run is None:
        raise NotFound("import")
    run.found = int(
        await session.scalar(
            select(func.count(ImportDraft.id)).where(ImportDraft.import_id == run_id)
        )
        or 0
    )
    run.status = RunStatus.FAILED if error else RunStatus.READY
    run.error = error[:60] if error else None
    run.finished_at = utcnow()
    return run


# How a source reads what it finds: the services, the open import and the job's details in,
# the products out.
Reader = Callable[["Services", CatalogImport, dict[str, Any]], Awaitable[list[Found]]]


async def run_job(ctx: JobContext, read: Reader) -> None:
    """The body of every import job: read, keep the drafts, and say how it went."""
    services: Services = ctx.services
    assert ctx.tenant_id is not None
    run_id = uuid.UUID(ctx.payload["import_id"])
    async with services.db.tenant(ctx.tenant_id) as session:
        run = await session.get(CatalogImport, run_id)
    if run is None or run.status != RunStatus.READING:
        return
    try:
        found = await read(services, run, ctx.payload)
    except AppError as exc:
        log.info("an import failed: %s", exc.code)
        async with services.db.tenant(ctx.tenant_id) as session:
            await finish(session, run_id, error=exc.code)
        return
    async with services.db.tenant(ctx.tenant_id) as session:
        stored = await session.get(CatalogImport, run_id)
        assert stored is not None
        await keep(session, stored, found)
        await finish(session, run_id)


async def queue(
    db: Database,
    tenant_id: uuid.UUID,
    source: Source,
    job: str,
    label: str,
    staff_id: uuid.UUID | None,
    **payload: str,
) -> uuid.UUID:
    """Open an import and hand its reading to the worker."""
    async with db.tenant(tenant_id) as session:
        run = await start(session, tenant_id, source, label, staff_id)
        await enqueue(
            session,
            job,
            {"import_id": str(run.id), **payload},
            tenant_id=tenant_id,
            dedupe_key=f"import:{run.id}",
            max_attempts=2,
        )
        return run.id


# -- photos -----------------------------------------------------------------------------------


def from_meta(url: str | None) -> bool:
    """A link to Meta's own servers over HTTPS: the only photo links fetched."""
    if not url:
        return False
    parts = urlsplit(url)
    host = (parts.hostname or "").lower()
    return (
        parts.scheme == "https"
        and parts.port in (None, 443)
        and parts.username is None
        and parts.password is None
        and any(host == allowed or host.endswith("." + allowed) for allowed in PHOTO_HOSTS)
    )


async def keep_photo(services: Services, tenant_id: uuid.UUID, url: str | None) -> uuid.UUID | None:
    """Fetch a photo from Meta and store our own copy; None when it cannot be had."""
    if not from_meta(url):
        return None
    assert url is not None
    try:
        response = await request(services.http, "GET", url, service="photo")
        async with services.db.tenant(tenant_id) as session:
            media = await media_service.store(
                session,
                services.storage,
                tenant_id,
                response.content,
                source="import",
                max_bytes=services.settings.max_upload_bytes,
            )
    except AppError as exc:
        log.info("could not keep an imported photo: %s", exc.code)
        return None
    return media.id if media.kind == MediaKind.IMAGE else None


# -- the owner's answer -----------------------------------------------------------------------


def edited(row: ImportDraft, name: str | None, price: float | None) -> ProductDraft:
    draft = ProductDraft.model_validate(row.data)
    changes: dict[str, Any] = {}
    if name is not None and name.strip():
        changes["name"] = " ".join(name.split())[:160]
    if price is not None:
        changes["price"] = price
    return draft.model_copy(update=changes)


async def add(
    session: AsyncSession,
    tenant: Tenant,
    row: ImportDraft,
    actor: Actor,
    *,
    name: str | None = None,
    price: float | None = None,
) -> CatalogItem:
    """Put a draft on sale, as the owner corrected it."""
    if row.status != DraftStatus.WAITING:
        raise AppError("draft_done", "This draft was added or dropped already", status_code=409)
    draft = edited(row, name, price)
    product = to_input(
        draft,
        get_template(tenant.template),
        get_pack(tenant.country).minor_digits,
        source=row.source,
        image_media_id=row.image_media_id,
    )
    if product is None:
        raise AppError("draft_incomplete", "A product needs a name and a price")
    item = await create_product(session, tenant.id, product, actor)
    row.status, row.product_id, row.data = DraftStatus.ADDED, item.product.id, draft.model_dump()
    await enqueue(
        session,
        TRANSLATE_JOB,
        {"product_id": str(item.product.id)},
        tenant_id=tenant.id,
        dedupe_key=f"translate:{item.product.id}",
    )
    await _after_add(session, row, item)
    await record(
        session,
        tenant.id,
        actor,
        "import.added",
        entity=("product", str(item.product.id)),
        data={"source": row.source, "ref": row.source_ref, "price_from": row.price_from},
    )
    return item


async def _after_add(session: AsyncSession, row: ImportDraft, item: CatalogItem) -> None:
    """An Instagram post now shows this product, so comments on it are answered (feature 77);
    a Meta catalog item gives way to the product's own at the next send (feature 6)."""
    if row.source == Source.INSTAGRAM:
        await session.execute(
            insert(SocialPost)
            .values(
                id=uuid.uuid4(),
                tenant_id=row.tenant_id,
                channel=Channel.INSTAGRAM,
                media_id=row.source_ref,
                permalink=row.extra.get("permalink"),
                caption=row.extra.get("caption"),
                product_id=item.product.id,
                linked_by="import",
            )
            .on_conflict_do_update(
                index_elements=["tenant_id", "channel", "media_id"],
                set_={"product_id": item.product.id, "linked_by": "import"},
            )
        )
    elif row.source == Source.META_CATALOG:
        link = await session.scalar(select(CommerceCatalog).with_for_update())
        old = [str(i) for i in row.extra.get("retailer_ids", [])]
        if link is not None and old:
            link.synced_ids = list(dict.fromkeys([*link.synced_ids, *old]))


async def drop(session: AsyncSession, row: ImportDraft, actor: Actor) -> None:
    if row.status != DraftStatus.WAITING:
        raise AppError("draft_done", "This draft was added or dropped already", status_code=409)
    row.status = DraftStatus.DROPPED
    await record(
        session,
        row.tenant_id,
        actor,
        "import.dropped",
        entity=("import_draft", str(row.id)),
        data={"source": row.source, "ref": row.source_ref},
    )


async def add_ready(session: AsyncSession, tenant: Tenant, run_id: uuid.UUID, actor: Actor) -> int:
    """Every waiting draft of an import with its name and a price the owner wrote, each checked
    as one; one that looks like a product already in the shop waits for the owner."""
    rows = list(
        await session.scalars(
            select(ImportDraft)
            .where(ImportDraft.import_id == run_id, ImportDraft.status == DraftStatus.WAITING)
            .order_by(ImportDraft.created_at, ImportDraft.source_ref)
        )
    )
    items = await load_items(session, statuses=(ProductStatus.ACTIVE, ProductStatus.ARCHIVED))
    added = 0
    for row in rows:
        name = ProductDraft.model_validate(row.data).name
        if ready(tenant, row) and same_as(items, name) is None:
            await add(session, tenant, row, actor)
            added += 1
    return added


def same_as(items: Sequence[CatalogItem], name: str) -> str | None:
    """An existing product with nearly this name, which the owner should compare first."""
    found = find_products(items, name, threshold=SAME_NAME) if name.strip() else []
    return found[0].item.product.name if found else None


# Prices the owner wrote: in a sheet, their catalog or a caption. One read from a photo or a
# video waits for the owner to check it.
TRUSTED = frozenset({"sheet", "catalog", "caption"})


def ready(tenant: Tenant, row: ImportDraft) -> bool:
    """Complete enough to become a product, with a price the owner wrote."""
    product = to_input(
        ProductDraft.model_validate(row.data),
        get_template(tenant.template),
        get_pack(tenant.country).minor_digits,
        source=row.source,
        image_media_id=row.image_media_id,
    )
    return product is not None and row.price_from in TRUSTED


async def drop_waiting(session: AsyncSession, run: CatalogImport, actor: Actor) -> int:
    """Drop what the owner did not add from an import (a video's rack, say)."""
    result = await session.execute(
        update(ImportDraft)
        .where(ImportDraft.import_id == run.id, ImportDraft.status == DraftStatus.WAITING)
        .values(status=DraftStatus.DROPPED)
    )
    dropped = int(getattr(result, "rowcount", 0) or 0)
    await record(
        session,
        run.tenant_id,
        actor,
        "import.dropped_rest",
        entity=("catalog_import", str(run.id)),
        data={"dropped": dropped},
    )
    return dropped
