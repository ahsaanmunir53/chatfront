"""Bringing in what the shop already has, in the console (features 85, 84 and 2): a sheet, the
Meta catalog, Instagram posts or a video become drafts, and the owner adds or drops each."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated, Any

from fastapi import APIRouter, File, Form, UploadFile
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from chatfront.audit.service import Actor, ActorType
from chatfront.catalog import imports, instagram_import, meta_import, sheet_import, video_import
from chatfront.catalog.drafts import ProductDraft, allowed_options, to_minor
from chatfront.catalog.imports import DraftStatus, RunStatus, Source
from chatfront.catalog.models import (
    CatalogImport,
    CommerceCatalog,
    ImportDraft,
    ProductStatus,
)
from chatfront.catalog.service import CatalogItem, load_items
from chatfront.channels.language import staff_language
from chatfront.core.clock import utcnow
from chatfront.core.countries import get_pack
from chatfront.core.errors import AppError, NotFound
from chatfront.core.models import Staff, Tenant
from chatfront.media import service as media_service
from chatfront.media.models import Media, MediaKind
from chatfront.owner.rights import Can
from chatfront.owner.routes import ServicesDep, needs
from chatfront.owner.sessions import OwnerSession
from chatfront.services import Services
from chatfront.stock import sheets
from chatfront.templates import get_template

router = APIRouter(prefix="/api/owner/imports")
CatalogDep = Annotated[OwnerSession, needs(Can.CATALOG)]
RUNS_SHOWN = 6
DRAFTS_SHOWN = 120


class VariantOut(BaseModel):
    options: dict[str, str]
    price: int | None
    stock: int | None


class DraftOut(BaseModel):
    id: uuid.UUID
    import_id: uuid.UUID
    source: str
    name: str
    price: int | None  # minor units, as the product would get it
    price_from: str | None
    description: str
    category: str | None
    variants: list[VariantOut]
    photo: str | None
    ready: bool  # can be added as it is
    in_shop: str | None  # an existing product with nearly the same name
    link: str | None  # the Instagram post


class RunOut(BaseModel):
    id: uuid.UUID
    source: str
    label: str
    status: str
    found: int
    waiting: int
    error: str | None
    created_at: datetime


class ImportsOut(BaseModel):
    currency: str
    runs: list[RunOut]
    drafts: list[DraftOut]
    instagram: bool  # an Instagram account is connected
    meta_catalog: bool  # a Meta catalog is connected


class AddIn(BaseModel):
    name: str | None = Field(default=None, max_length=160)
    price: float | None = Field(default=None, gt=0, le=100_000_000)


def _actor(session: OwnerSession) -> Actor:
    return Actor(ActorType.STAFF, str(session.staff_id))


async def _tenant(db: AsyncSession, session: OwnerSession) -> Tenant:
    tenant = await db.get(Tenant, session.tenant_id)
    if tenant is None:
        raise NotFound("shop")
    return tenant


async def _draft_out(
    services: Services,
    tenant: Tenant,
    row: ImportDraft,
    photo: Media | None,
    items: list[CatalogItem],
) -> DraftOut:
    draft = ProductDraft.model_validate(row.data)
    url = None
    if photo is not None:
        ttl = services.settings.signed_url_ttl_seconds
        url = await media_service.signed_url(services.storage, photo, ttl, blurred=False)
    template, digits = get_template(tenant.template), get_pack(tenant.country).minor_digits
    variants = [
        VariantOut(
            options=allowed_options(template, v.options),
            price=to_minor(v.price, digits) if v.price is not None else None,
            stock=v.stock,
        )
        for v in draft.variants
    ]
    return DraftOut(
        id=row.id,
        import_id=row.import_id,
        source=row.source,
        name=draft.name,
        price=to_minor(draft.price, digits) if draft.price is not None else None,
        price_from=row.price_from,
        description=draft.description,
        category=draft.category,
        variants=[v for v in variants if v.options or v.stock is not None],
        photo=url,
        ready=imports.ready(tenant, row),
        in_shop=imports.same_as(items, draft.name),
        link=row.extra.get("permalink"),
    )


def _run_out(run: CatalogImport, waiting: int) -> RunOut:
    stuck = run.status == RunStatus.READING and run.created_at < utcnow() - imports.STUCK_AFTER
    return RunOut(
        id=run.id,
        source=run.source,
        label=run.label,
        status=RunStatus.FAILED if stuck else run.status,
        found=run.found,
        waiting=waiting,
        error="timed_out" if stuck else run.error,
        created_at=run.created_at,
    )


@router.get("")
async def list_imports(session: CatalogDep, services: ServicesDep) -> ImportsOut:
    async with services.db.tenant(session.tenant_id) as db:
        tenant = await _tenant(db, session)
        runs = list(
            await db.scalars(
                select(CatalogImport).order_by(CatalogImport.created_at.desc()).limit(RUNS_SHOWN)
            )
        )
        rows = list(
            await db.scalars(
                select(ImportDraft)
                .where(ImportDraft.status == DraftStatus.WAITING)
                .order_by(ImportDraft.created_at, ImportDraft.source_ref)
                .limit(DRAFTS_SHOWN)
            )
        )
        photos = {
            m.id: m
            for m in await db.scalars(
                select(Media).where(
                    Media.id.in_([r.image_media_id for r in rows if r.image_media_id])
                )
            )
        }
        items = await load_items(db, statuses=(ProductStatus.ACTIVE, ProductStatus.ARCHIVED))
        catalog = await db.scalar(select(CommerceCatalog.id))
    counts: dict[uuid.UUID, int] = {}
    for row in rows:
        counts[row.import_id] = counts.get(row.import_id, 0) + 1
    drafts = [
        await _draft_out(
            services,
            tenant,
            row,
            photos.get(row.image_media_id) if row.image_media_id else None,
            items,
        )
        for row in rows
    ]
    return ImportsOut(
        currency=tenant.currency,
        runs=[_run_out(run, counts.get(run.id, 0)) for run in runs],
        drafts=drafts,
        instagram=await instagram_import.connected(services, session.tenant_id),
        meta_catalog=catalog is not None,
    )


@router.post("/sheet")
async def import_sheet(
    session: CatalogDep, services: ServicesDep, file: Annotated[UploadFile, File()]
) -> RunOut:
    """Read a product list now; its products wait as drafts."""
    rows = sheets.read_rows(await file.read(sheets.MAX_BYTES + 1))
    async with services.db.tenant(session.tenant_id) as db:
        tenant = await _tenant(db, session)
        staff = await db.get(Staff, session.staff_id)
    lang = staff_language(staff, tenant) if staff else tenant.default_lang
    template = get_template(tenant.template)
    columns = await sheet_import.map_columns(services.ai, tenant.id, rows, template, lang)
    async with services.db.tenant(session.tenant_id) as db:
        run = await imports.start(
            db, tenant.id, Source.SHEET, file.filename or "sheet", session.staff_id
        )
        await imports.keep(db, run, sheet_import.to_found(rows, columns, run.id))
        run = await imports.finish(db, run.id)
        return _run_out(run, run.found)


async def _queue(
    services: Services, session: OwnerSession, source: Source, job: str, label: str, **payload: str
) -> dict[str, Any]:
    run_id = await imports.queue(
        services.db, session.tenant_id, source, job, label, session.staff_id, **payload
    )
    return {"queued": True, "import_id": str(run_id)}


@router.post("/meta-catalog")
async def import_meta_catalog(session: CatalogDep, services: ServicesDep) -> dict[str, Any]:
    async with services.db.tenant(session.tenant_id) as db:
        if await db.scalar(select(CommerceCatalog.id)) is None:
            raise AppError("catalog_not_connected", "No Meta catalog is connected to this shop")
    return await _queue(
        services, session, Source.META_CATALOG, meta_import.IMPORT_JOB, "Meta catalog"
    )


@router.post("/instagram")
async def import_instagram(session: CatalogDep, services: ServicesDep) -> dict[str, Any]:
    if not await instagram_import.connected(services, session.tenant_id):
        raise AppError("instagram_not_connected", "No Instagram account is connected")
    label = f"Instagram, last {instagram_import.POSTS} posts"
    return await _queue(services, session, Source.INSTAGRAM, instagram_import.IMPORT_JOB, label)


@router.post("/video")
async def import_video(
    session: CatalogDep,
    services: ServicesDep,
    file: Annotated[UploadFile, File()],
    words: Annotated[str, Form(max_length=500)] = "",
) -> dict[str, Any]:
    data = await file.read(services.settings.max_upload_bytes + 1)
    async with services.db.tenant(session.tenant_id) as db:
        media = await media_service.store(
            db,
            services.storage,
            session.tenant_id,
            data,
            source="console",
            max_bytes=services.settings.max_upload_bytes,
        )
    if media.kind != MediaKind.VIDEO:
        raise AppError("not_a_video", "Send a video (MP4)", status_code=415)
    return await _queue(
        services,
        session,
        Source.VIDEO,
        video_import.IMPORT_JOB,
        file.filename or "video",
        media_id=str(media.id),
        words=words,
    )


async def _row(db: AsyncSession, draft_id: uuid.UUID) -> ImportDraft:
    row = await db.get(ImportDraft, draft_id, with_for_update=True)
    if row is None:
        raise NotFound("draft")
    return row


@router.post("/drafts/{draft_id}/add")
async def add_draft(
    draft_id: uuid.UUID, body: AddIn, session: CatalogDep, services: ServicesDep
) -> dict[str, str]:
    async with services.db.tenant(session.tenant_id) as db:
        tenant = await _tenant(db, session)
        row = await _row(db, draft_id)
        item = await imports.add(db, tenant, row, _actor(session), name=body.name, price=body.price)
        return {"code": item.product.code, "name": item.product.name}


@router.post("/drafts/{draft_id}/drop")
async def drop_draft(
    draft_id: uuid.UUID, session: CatalogDep, services: ServicesDep
) -> dict[str, bool]:
    async with services.db.tenant(session.tenant_id) as db:
        await imports.drop(db, await _row(db, draft_id), _actor(session))
    return {"dropped": True}


@router.post("/{import_id}/add-ready")
async def add_ready(
    import_id: uuid.UUID, session: CatalogDep, services: ServicesDep
) -> dict[str, int]:
    async with services.db.tenant(session.tenant_id) as db:
        tenant = await _tenant(db, session)
        if await db.get(CatalogImport, import_id) is None:
            raise NotFound("import")
        added = await imports.add_ready(db, tenant, import_id, _actor(session))
    return {"added": added}


@router.post("/{import_id}/drop-rest")
async def drop_rest(
    import_id: uuid.UUID, session: CatalogDep, services: ServicesDep
) -> dict[str, int]:
    async with services.db.tenant(session.tenant_id) as db:
        run = await db.get(CatalogImport, import_id)
        if run is None:
            raise NotFound("import")
        dropped = await imports.drop_waiting(db, run, _actor(session))
    return {"dropped": dropped}
