"""The owner decides (feature 25): a payment code could not settle reaches the owner with
the evidence and Approve and Reject, on WhatsApp and in the console. The decision is the
owner's; code then settles the money or tells the customer, and records who decided.

The evidence card is written by code. The watcher model (Glimmer, else Spark Open) adds one
line on what to look at, which goes out only when every number in it is in the evidence.
"""

from __future__ import annotations

import json
import logging
import uuid
from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING, Any

from sqlalchemy.ext.asyncio import AsyncSession

from chatfront.ai.client import ModelUnavailable
from chatfront.ai.prompts import load_prompt
from chatfront.ai.types import ChatMessage, ChatRequest, Role
from chatfront.audit.service import Actor, ActorType, record
from chatfront.channels.language import chat_language, staff_language
from chatfront.channels.notify import send_to, tell_owner
from chatfront.channels.types import Button, Outbound, OutButtons, OutText
from chatfront.core.clock import utcnow
from chatfront.core.countries import get_pack
from chatfront.core.errors import AppError
from chatfront.core.i18n import t
from chatfront.core.models import Tenant
from chatfront.core.provisioning import owner_of
from chatfront.jobs.queue import JobContext, enqueue
from chatfront.orders.models import Order
from chatfront.owner.rights import Can, staff_rights
from chatfront.payments.forensics import Signal
from chatfront.payments.messages import evidence_lines, paid_text
from chatfront.payments.models import ClaimStatus, PaymentClaim, VerifiedBy, WalletCredit
from chatfront.payments.settle import settle
from chatfront.shield.answer_check import Facts, unsupported
from chatfront.shield.autonomy import Action, Level, level

if TYPE_CHECKING:
    from chatfront.channels.conversation import Turn
    from chatfront.services import Services

log = logging.getLogger(__name__)

REVIEW_JOB = "payments.review"
APPROVE, REJECT = "ok", "no"
_OPEN = (ClaimStatus.PENDING, ClaimStatus.REVIEW)
_NOTE_LIMIT = 300


async def to_review(
    session: AsyncSession, claim: PaymentClaim, *extra: Signal, again: bool = False
) -> None:
    """Hand a claim to the owner, adding what code found; `again` re-sends an updated card."""
    claim.signals = [*claim.signals, *(signal.as_dict() for signal in extra)]
    claim.status = ClaimStatus.REVIEW
    if again:
        claim.owner_told_at = None
    await session.flush()
    await enqueue(
        session,
        REVIEW_JOB,
        {"claim_id": str(claim.id)},
        tenant_id=claim.tenant_id,
        dedupe_key=f"payment-review:{claim.id}:{len(claim.signals)}",
    )


def why_not_alone(tenant: Tenant, credit: WalletCredit) -> Signal | None:
    """Why a matching credit SMS may not settle a payment by itself: its parser has not been
    checked on a real message, or the owner wants to approve every payment (feature 104)."""
    if not credit.verified:
        return Signal("unverified_parser")
    if level(tenant, Action.SMS_PAYMENTS) != Level.AUTO:
        return Signal("owner_asks")
    return None


def review_button(claim: PaymentClaim, answer: str) -> str:
    return f"pay:{claim.id}:{answer}"


def parse_review_button(value: str | None) -> tuple[uuid.UUID, bool] | None:
    if not value or not value.startswith("pay:"):
        return None
    parts = value.split(":")
    if len(parts) != 3 or parts[2] not in (APPROVE, REJECT):
        return None
    raw_id, answer = parts[1], parts[2]
    try:
        return uuid.UUID(raw_id), answer == APPROVE
    except ValueError:
        return None


# -- the card ---------------------------------------------------------------------------------


@dataclass(frozen=True)
class Evidence:
    title: str
    lines: list[str]
    facts: dict[str, Any]  # the same evidence as data, for the model's note and its check


async def _evidence(
    session: AsyncSession, tenant: Tenant, claim: PaymentClaim, lang: str
) -> Evidence | None:
    order = await session.get(Order, claim.order_id) if claim.order_id else None
    if order is None:
        return None
    credit = await session.get(WalletCredit, claim.credit_id) if claim.credit_id else None
    customer = (order.address or {}).get("name") or order.code
    return Evidence(
        title=t("payment.review_title", lang, code=order.code, name=customer),
        lines=evidence_lines(tenant, order, claim, credit, lang),
        facts={
            "order": order.code,
            "asked": order.due_now,
            "order_total": order.total,
            "paid_before": order.paid,
            "screenshot": {
                "amount": claim.amount,
                "provider": claim.provider,
                "transaction_id": claim.transaction_id,
                "paid_at": claim.paid_at.isoformat() if claim.paid_at else None,
                "payer": claim.payer,
                "remarks": claim.remarks,
            }
            if claim.media_id
            else None,
            "sms": {"amount": credit.amount, "transaction_id": credit.transaction_id}
            if credit
            else None,
            "signals": claim.signals,
            "minor_units": "amounts are in minor units",
        },
    )


async def _note(services: Services, tenant: Tenant, evidence: Evidence, lang: str) -> str | None:
    """One line from the watcher model on what to check; dropped if it quotes a number the
    evidence does not hold, or if no model answers."""
    prompt = load_prompt("payment_evidence")
    request = ChatRequest(
        role=Role.WATCHER,
        messages=[
            ChatMessage("system", prompt.render(owner_language=lang)),
            ChatMessage("user", json.dumps(evidence.facts, ensure_ascii=False, default=str)),
        ],
        tenant_id=tenant.id,
        prompt=prompt.ref,
        ref=("order", str(evidence.facts["order"])),
        lang=lang,
    )
    try:
        reply = await services.ai.complete(request)
    except (ModelUnavailable, AppError) as exc:
        log.info("no note for a payment review: %s", exc)
        return None
    note = " ".join((reply.content or "").split())[:_NOTE_LIMIT]
    facts = Facts(get_pack(tenant.country).minor_digits)
    facts.data(evidence.facts, lang)
    facts.text("\n".join(evidence.lines), lang)
    facts.codes.add(str(evidence.facts["order"]))
    if not note or unsupported(note, facts, lang):
        return None
    return note


async def review_job(ctx: JobContext) -> None:
    """Send the owner the card for one claim. Safe to run twice: a told claim is skipped."""
    services: Services = ctx.services
    assert ctx.tenant_id is not None
    claim_id = uuid.UUID(ctx.payload["claim_id"])
    owner = await owner_of(services.db, ctx.tenant_id)
    async with services.db.tenant(ctx.tenant_id) as session:
        claim = await session.get(PaymentClaim, claim_id)
        tenant = await session.get(Tenant, ctx.tenant_id)
        if claim is None or tenant is None or claim.status != ClaimStatus.REVIEW:
            return
        if claim.owner_told_at is not None:
            return
        lang = staff_language(owner, tenant)
        evidence = await _evidence(session, tenant, claim, lang)
    if evidence is None:
        return
    note = await _note(services, tenant, evidence, lang)
    text = "\n".join([evidence.title, *evidence.lines, *([note] if note else [])])
    card = OutButtons(
        text,
        (
            Button(review_button(claim, APPROVE), t("payment.approve", lang)),
            Button(review_button(claim, REJECT), t("payment.reject", lang)),
        ),
    )
    await tell_owner(services, tenant.id, owner, card, lang)
    async with services.db.tenant(tenant.id) as session:
        stored = await session.get(PaymentClaim, claim_id)
        if stored is not None:
            stored.owner_told_at = utcnow()


# -- the decision -----------------------------------------------------------------------------


class Decided(StrEnum):
    APPROVED = "approved"
    REJECTED = "rejected"
    BEFORE = "before"  # decided already, or nothing to decide


@dataclass(frozen=True)
class Decision:
    outcome: Decided
    order_code: str | None = None
    receipt_code: str | None = None


async def decide(
    services: Services,
    tenant_id: uuid.UUID,
    claim_id: uuid.UUID,
    staff_id: uuid.UUID,
    *,
    approve: bool,
) -> Decision:
    """The owner's Approve or Reject. Approve settles the amount on the screenshot (or the
    SMS); Reject asks the customer to check. Only an open claim can be decided."""
    async with services.db.tenant(tenant_id) as session:
        claim = await session.get(PaymentClaim, claim_id)
        tenant = await session.get(Tenant, tenant_id)
        order = await session.get(Order, claim.order_id) if claim and claim.order_id else None
        if claim is None or tenant is None or order is None or claim.status not in _OPEN:
            return Decision(Decided.BEFORE)
        lang = await chat_language(session, tenant, order.conversation_id)
        if approve:
            credit = await session.get(WalletCredit, claim.credit_id) if claim.credit_id else None
            amount = (
                credit.amount
                if credit
                else claim.amount or order.due_now or max(order.total - order.paid, 0)
            )
            receipt = await settle(
                session,
                tenant,
                order,
                amount=amount,
                verified_by=VerifiedBy.OWNER,
                secret_key=services.settings.secret_key.get_secret_value(),
                claim=claim,
                credit=credit,
                staff_id=staff_id,
            )
            message = paid_text(tenant, order, receipt, lang, services.settings.web_url)
            decision = Decision(Decided.APPROVED, order.code, receipt.code)
        else:
            claim.status, claim.decided_at = ClaimStatus.REJECTED, utcnow()
            await record(
                session,
                tenant_id,
                Actor(ActorType.STAFF, str(staff_id)),
                "payment.rejected",
                entity=("order", str(order.id)),
                data={"claim": str(claim.id)},
            )
            message = t("payment.not_confirmed", lang, code=order.code)
            decision = Decision(Decided.REJECTED, order.code)
    if order.conversation_id is not None:
        await send_to(services, tenant_id, order.conversation_id, OutText(message), lang)
    return decision


async def payment_button(services: Services, turn: Turn) -> list[Outbound] | None:
    """The owner tapped Approve or Reject on a payment card in their own chat."""
    parsed = parse_review_button((turn.message.payload or {}).get("button_id"))
    if parsed is None or turn.staff_id is None:
        return None
    if Can.PAYMENTS not in await staff_rights(services.db, turn.tenant.id, turn.staff_id):
        return [OutText(t("owner.not_allowed", turn.lang))]
    claim_id, approve = parsed
    decision = await decide(services, turn.tenant.id, claim_id, turn.staff_id, approve=approve)
    if decision.outcome == Decided.BEFORE:
        return [OutText(t("payment.decided_before", turn.lang))]
    key = "payment.approved" if decision.outcome == Decided.APPROVED else "payment.rejected"
    return [OutText(t(key, turn.lang, code=decision.order_code))]
