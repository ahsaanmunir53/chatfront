"""Decisions waiting for the owner (feature 104): when an action's autonomy level is "ask",
code stops, stores what it would do, and the owner answers with Approve or Reject on WhatsApp
or in the console. Each kind of decision says who may answer it, how it reads to the owner,
and what happens after.
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING, Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from chatfront.audit.service import Actor, ActorType, record
from chatfront.channels.language import staff_language
from chatfront.channels.notify import tell_owner
from chatfront.channels.types import Button, Outbound, OutButtons, OutText
from chatfront.core.clock import utcnow
from chatfront.core.i18n import t
from chatfront.core.models import Staff, Tenant
from chatfront.core.provisioning import owner_of
from chatfront.jobs.queue import JobContext, enqueue
from chatfront.owner.rights import Can, staff_rights
from chatfront.shield.models import Approval, ApprovalStatus

if TYPE_CHECKING:
    from chatfront.channels.conversation import Turn
    from chatfront.services import Services

log = logging.getLogger(__name__)

APPROVAL_JOB = "shield.approval"
APPROVE, REJECT = "ok", "no"


@dataclass(frozen=True)
class ApprovalKind:
    right: Can  # who may answer
    # The question as the owner reads it, in their language.
    describe: Callable[[Tenant, Approval, str], str]
    # What happens once answered: (services, tenant, approval, approved).
    carry_out: Callable[[Services, Tenant, Approval, bool], Awaitable[None]]


class Answered(StrEnum):
    APPROVED = "approved"
    REJECTED = "rejected"
    BEFORE = "before"
    NOT_ALLOWED = "not_allowed"


async def ask(
    session: AsyncSession, tenant_id: uuid.UUID, kind: str, payload: dict[str, Any]
) -> Approval:
    """Store the question and have the owner told."""
    approval = Approval(tenant_id=tenant_id, kind=kind, payload=payload)
    session.add(approval)
    await session.flush()
    await enqueue(
        session,
        APPROVAL_JOB,
        {"approval_id": str(approval.id)},
        tenant_id=tenant_id,
        dedupe_key=f"approval:{approval.id}",
    )
    return approval


async def ask_once(
    session: AsyncSession,
    tenant_id: uuid.UUID,
    kind: str,
    payload: dict[str, Any],
    same: tuple[str, ...],
) -> Approval:
    """Ask, unless the same question (these payload keys) is already waiting."""
    query = select(Approval).where(Approval.kind == kind, Approval.status == ApprovalStatus.PENDING)
    for key in same:
        query = query.where(Approval.payload[key].astext == str(payload[key]))
    waiting = await session.scalar(query.limit(1))
    return waiting if waiting is not None else await ask(session, tenant_id, kind, payload)


def button_id(approval: Approval, answer: str) -> str:
    return f"ap:{approval.id}:{answer}"


def parse_button(value: str | None) -> tuple[uuid.UUID, bool] | None:
    parts = (value or "").split(":")
    if len(parts) != 3 or parts[0] != "ap" or parts[2] not in (APPROVE, REJECT):
        return None
    try:
        return uuid.UUID(parts[1]), parts[2] == APPROVE
    except ValueError:
        return None


def card(
    tenant: Tenant, approval: Approval, kinds: dict[str, ApprovalKind], lang: str
) -> OutButtons:
    return OutButtons(
        kinds[approval.kind].describe(tenant, approval, lang),
        (
            Button(button_id(approval, APPROVE), t("payment.approve", lang)),
            Button(button_id(approval, REJECT), t("payment.reject", lang)),
        ),
    )


async def tell_job(ctx: JobContext) -> None:
    """Send the owner the question. Safe to run twice: a told approval is skipped."""
    services: Services = ctx.services
    assert ctx.tenant_id is not None
    owner = await owner_of(services.db, ctx.tenant_id)
    async with services.db.tenant(ctx.tenant_id) as session:
        approval = await session.get(Approval, uuid.UUID(ctx.payload["approval_id"]))
        tenant = await session.get(Tenant, ctx.tenant_id)
        if approval is None or tenant is None or approval.owner_told_at is not None:
            return
        if approval.status != ApprovalStatus.PENDING:
            return
        lang = staff_language(owner, tenant)
        message = card(tenant, approval, services.registries.approvals, lang)
        approval.owner_told_at = utcnow()
    await tell_owner(services, ctx.tenant_id, owner, message, lang)


async def pending(session: AsyncSession) -> list[Approval]:
    return list(
        await session.scalars(
            select(Approval)
            .where(Approval.status == ApprovalStatus.PENDING)
            .order_by(Approval.created_at.desc())
            .limit(50)
        )
    )


async def answer(
    services: Services,
    tenant_id: uuid.UUID,
    approval_id: uuid.UUID,
    staff_id: uuid.UUID,
    *,
    approve: bool,
) -> Answered:
    """The owner's (or an allowed staff member's) answer, once; what follows is the kind's."""
    kinds = services.registries.approvals
    allowed = await staff_rights(services.db, tenant_id, staff_id)
    async with services.db.tenant(tenant_id) as session:
        approval = await session.get(Approval, approval_id)
        tenant = await session.get(Tenant, tenant_id)
        if approval is None or tenant is None or approval.status != ApprovalStatus.PENDING:
            return Answered.BEFORE
        if kinds[approval.kind].right not in allowed:
            return Answered.NOT_ALLOWED
        approval.status = ApprovalStatus.APPROVED if approve else ApprovalStatus.REJECTED
        approval.decided_by, approval.decided_at = staff_id, utcnow()
        await record(
            session,
            tenant_id,
            Actor(ActorType.STAFF, str(staff_id)),
            "approval.approved" if approve else "approval.rejected",
            entity=("approval", str(approval.id)),
            data={"kind": approval.kind},
        )
    await kinds[approval.kind].carry_out(services, tenant, approval, approve)
    return Answered.APPROVED if approve else Answered.REJECTED


async def approval_button(services: Services, turn: Turn) -> list[Outbound] | None:
    """Approve or Reject tapped in the owner's own chat."""
    parsed = parse_button((turn.message.payload or {}).get("button_id"))
    if parsed is None or turn.staff_id is None:
        return None
    approval_id, approve = parsed
    outcome = await answer(services, turn.tenant.id, approval_id, turn.staff_id, approve=approve)
    key = {
        Answered.APPROVED: "approval.approved",
        Answered.REJECTED: "approval.rejected",
        Answered.BEFORE: "payment.decided_before",
        Answered.NOT_ALLOWED: "owner.not_allowed",
    }[outcome]
    return [OutText(t(key, turn.lang))]


async def describe_for(
    services: Services, tenant: Tenant, approvals: list[Approval], staff: Staff
) -> list[dict[str, Any]]:
    """The waiting questions as the console shows them, in the reader's language."""
    lang = staff_language(staff, tenant)
    kinds = services.registries.approvals
    return [
        {
            "id": str(approval.id),
            "kind": approval.kind,
            "text": kinds[approval.kind].describe(tenant, approval, lang),
            "created_at": approval.created_at.isoformat(),
        }
        for approval in approvals
    ]
