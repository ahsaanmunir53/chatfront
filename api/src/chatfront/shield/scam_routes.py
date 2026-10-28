"""Scams in the console: a call recording or voice note checked (feature 39), the shop's
reports of scammers' numbers, accounts and links, and, for Chatfront's own staff, their review
and the STIX report for NCCIA and CERT (feature 47)."""

from __future__ import annotations

import json
import uuid
from datetime import datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, File, UploadFile
from fastapi.responses import Response
from pydantic import BaseModel, Field
from sqlalchemy import select

from chatfront.audit.service import Actor, ActorType, record
from chatfront.core.clock import utcnow
from chatfront.core.errors import AppError, NotFound
from chatfront.core.models import Tenant
from chatfront.media import service as media_service
from chatfront.media.models import MediaKind
from chatfront.owner.rights import Can
from chatfront.owner.routes import ServicesDep, needs
from chatfront.owner.sessions import OwnerSession
from chatfront.shield import guardian, stix
from chatfront.shield.models import ScamKind, ScamReport, ScamStatus
from chatfront.shield.platform_routes import reviewer

router = APIRouter()
TrustDep = Annotated[OwnerSession, needs(Can.TRUST)]
ReviewerDep = Annotated[OwnerSession, Depends(reviewer)]
SHOWN = 50
LISTENED = (MediaKind.AUDIO, MediaKind.VIDEO)


class CheckOut(BaseModel):
    verdict: str
    reading: dict[str, Any]


class ReportIn(BaseModel):
    kind: ScamKind
    value: str = Field(min_length=3, max_length=300)
    posing_as: str = Field(default="other", max_length=20)


class ReportOut(BaseModel):
    id: uuid.UUID
    kind: str
    value: str
    posing_as: str
    source: str
    status: str
    created_at: datetime


class ToReview(ReportOut):
    shop: str
    evidence: dict[str, Any]


class DecisionIn(BaseModel):
    confirm: bool
    note: str = Field(default="", max_length=300)


def _out(report: ScamReport) -> ReportOut:
    return ReportOut(
        id=report.id,
        kind=report.kind,
        value=report.value,
        posing_as=report.posing_as,
        source=report.source,
        status=report.status,
        created_at=report.created_at,
    )


@router.post("/api/owner/guardian")
async def check_call(
    session: TrustDep, services: ServicesDep, file: Annotated[UploadFile, File()]
) -> CheckOut:
    """A call recording or a voice note, heard for a scam."""
    data = await file.read(services.settings.max_upload_bytes + 1)
    async with services.db.tenant(session.tenant_id) as db:
        media = await media_service.store(
            db,
            services.storage,
            session.tenant_id,
            data,
            source="guardian",
            max_bytes=services.settings.max_upload_bytes,
        )
        if media.kind not in LISTENED:
            raise AppError("not_a_recording", "Send a recording or a voice note", status_code=415)
    reading = await guardian.listen(services.ai, services.storage, session.tenant_id, media)
    return CheckOut(verdict=guardian.judge(reading), reading=reading.model_dump(mode="json"))


@router.get("/api/owner/scams")
async def scams(session: TrustDep, services: ServicesDep) -> list[ReportOut]:
    async with services.db.tenant(session.tenant_id) as db:
        found = await db.scalars(
            select(ScamReport).order_by(ScamReport.created_at.desc()).limit(SHOWN)
        )
        return [_out(r) for r in found]


@router.post("/api/owner/scams")
async def report_scam(body: ReportIn, session: TrustDep, services: ServicesDep) -> ReportOut:
    async with services.db.tenant(session.tenant_id) as db:
        found = await guardian.report(
            db,
            session.tenant_id,
            body.kind,
            body.value,
            body.posing_as,
            "console",
            Actor(ActorType.STAFF, str(session.staff_id)),
            staff_id=session.staff_id,
        )
        return _out(found)


@router.get("/api/platform/scams", dependencies=[Depends(reviewer)])
async def scams_to_review(services: ServicesDep) -> list[ToReview]:
    async with services.db.system() as system:
        rows = (
            await system.execute(
                select(ScamReport, Tenant.name)
                .join(Tenant, Tenant.id == ScamReport.tenant_id)
                .where(ScamReport.status == ScamStatus.PENDING)
                .order_by(ScamReport.created_at)
                .limit(SHOWN)
            )
        ).all()
    return [
        ToReview(**_out(report).model_dump(), shop=shop, evidence=report.evidence)
        for report, shop in rows
    ]


@router.post("/api/platform/scams/{report_id}")
async def decide_scam(
    report_id: uuid.UUID, body: DecisionIn, session: ReviewerDep, services: ServicesDep
) -> ReportOut:
    async with services.db.system() as system:
        report = await system.get(ScamReport, report_id)
        if report is None:
            raise NotFound("report")
        if report.status != ScamStatus.PENDING:
            raise AppError("decided_before", "This report was reviewed already", status_code=409)
        report.status = ScamStatus.CONFIRMED if body.confirm else ScamStatus.DISMISSED
        report.review_note, report.reviewed_at = body.note or None, utcnow()
        await record(
            system,
            session.tenant_id,
            Actor(ActorType.STAFF, str(session.staff_id)),
            "scam.confirmed" if body.confirm else "scam.dismissed",
            entity=("scam_report", str(report.id)),
            data={"kind": report.kind},
        )
        return _out(report)


@router.get("/api/platform/stix", dependencies=[Depends(reviewer)])
async def stix_report(services: ServicesDep) -> Response:
    """The confirmed scams as a STIX 2.1 bundle, to send to NCCIA and CERT."""
    async with services.db.system() as system:
        confirmed = list(
            await system.scalars(
                select(ScamReport)
                .where(ScamReport.status == ScamStatus.CONFIRMED)
                .order_by(ScamReport.reviewed_at)
            )
        )
    now = utcnow()
    body = json.dumps(stix.bundle(confirmed, now), indent=2)
    name = f"chatfront-scams-{now:%Y-%m-%d}.json"
    return Response(
        body,
        media_type="application/stix+json;version=2.1",
        headers={"Content-Disposition": f'attachment; filename="{name}"'},
    )
