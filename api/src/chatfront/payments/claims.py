"""A payment screenshot from a customer whose order waits for money (features 23, 98, 99).

Spark Open reads it; code picks the order (the code in the remarks, else the unique amount,
else the latest), adds its own checks and looks for the same picture sent before, here or to
another shop. Then one of three things happens: a matching credit SMS from a verified parser
settles it at once; anything doubtful goes to the owner; otherwise it waits for the SMS, and
the watcher hands it to the owner if none comes.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from datetime import timedelta
from typing import TYPE_CHECKING

from sqlalchemy import ColumnElement, select
from sqlalchemy.ext.asyncio import AsyncSession

from chatfront.ai.client import ModelUnavailable
from chatfront.ai.types import ImagePart
from chatfront.channels.types import Kind, Outbound, OutText
from chatfront.core.clock import utcnow
from chatfront.core.countries import get_pack
from chatfront.core.errors import AppError
from chatfront.core.i18n import t
from chatfront.core.models import Tenant
from chatfront.media.service import from_data_url
from chatfront.orders.models import Order
from chatfront.orders.states import OrderStatus
from chatfront.payments import forensics
from chatfront.payments.forensics import STRONG, Expected, PaymentReading, Signal, same_picture
from chatfront.payments.matcher import credit_for_claim, find_order_code, same_transaction
from chatfront.payments.messages import paid_text
from chatfront.payments.models import ClaimStatus, PaymentClaim, VerifiedBy
from chatfront.payments.review import to_review, why_not_alone
from chatfront.payments.settle import claim_facts, credit_facts, settle, unused_credits

if TYPE_CHECKING:
    from chatfront.channels.conversation import Turn
    from chatfront.services import Services

log = logging.getLogger(__name__)

LOOK_BACK = timedelta(days=90)  # how far back a reused picture is looked for
_REUSED = frozenset({"reused_here", "reused_elsewhere"})


@dataclass(frozen=True, slots=True)
class Picture:
    reading: PaymentReading | None  # None when no model could read it
    phash: str | None
    amount: int | None  # minor units


def _close(phash: str | None, other: PaymentClaim, picture: Picture, tid: str | None) -> bool:
    """The same screenshot: near-identical pixels and the same transaction (or, when neither
    shows an id, the same amount). Two screenshots of one app look alike; the ids tell them
    apart."""
    if not same_picture(phash, other.phash):
        return False
    if tid and other.transaction_id:
        return same_transaction(tid, other.transaction_id)
    return not tid and not other.transaction_id and picture.amount == other.amount


async def _waiting(session: AsyncSession, customer_id: uuid.UUID) -> list[Order]:
    return list(
        await session.scalars(
            select(Order)
            .where(
                Order.customer_id == customer_id,
                Order.status == OrderStatus.AWAITING_PAYMENT,
            )
            .order_by(Order.placed_at.desc())
        )
    )


def _pick(orders: list[Order], picture: Picture, body: str) -> Order:
    """The order this money is for: its code in the remarks or the message, else the one
    asking for exactly this amount, else the latest."""
    remarks = picture.reading.remarks if picture.reading else None
    code = find_order_code(f"{remarks or ''} {body}", [order.code for order in orders])
    if code is not None:
        return next(order for order in orders if order.code == code)
    by_amount = [order for order in orders if picture.amount and order.due_now == picture.amount]
    return by_amount[0] if len(by_amount) == 1 else orders[0]


async def _read(services: Services, turn: Turn, image: ImagePart, waiting: list[Order]) -> Picture:
    pack = get_pack(turn.tenant.country)
    # The cost meter books the reading to the order when only one waits (feature 112).
    ref = ("order", str(waiting[0].id)) if len(waiting) == 1 else ("message", str(turn.message.id))
    try:
        reading: PaymentReading | None = await forensics.read_screenshot(
            services.ai,
            tenant_id=turn.tenant.id,
            picture=image,
            currency=pack.currency,
            ref=ref,
        )
    except (ModelUnavailable, AppError) as exc:
        log.warning("payment screenshot not read: %s", exc)
        reading = None
    data = from_data_url(image.url)
    amount = (
        round(reading.amount * 10**pack.minor_digits)
        if reading is not None and reading.amount is not None
        else None
    )
    return Picture(reading, forensics.picture_hash(data) if data else None, amount)


def _same_money(picture: Picture, tid: str | None) -> ColumnElement[bool]:
    """Claims that could be this screenshot again: the same amount, or the same id."""
    if picture.amount is not None:
        return PaymentClaim.amount == picture.amount
    return PaymentClaim.transaction_id == (tid or "")


async def _earlier(
    session: AsyncSession, picture: Picture, *where: ColumnElement[bool]
) -> list[PaymentClaim]:
    tid = picture.reading.transaction_id if picture.reading else None
    if picture.phash is None or (picture.amount is None and not tid):
        return []
    found = await session.scalars(
        select(PaymentClaim)
        .where(
            PaymentClaim.phash.is_not(None),
            PaymentClaim.created_at > utcnow() - LOOK_BACK,
            _same_money(picture, tid),
            *where,
        )
        .limit(500)
    )
    return [claim for claim in found if _close(picture.phash, claim, picture, tid)]


async def _seen_elsewhere(services: Services, tenant_id: uuid.UUID, picture: Picture) -> bool:
    """The same screenshot already sent to another shop. Read across shops with the system
    session, and only to answer yes or no."""
    async with services.db.system() as session:
        return bool(await _earlier(session, picture, PaymentClaim.tenant_id != tenant_id))


async def handle_screenshot(services: Services, turn: Turn) -> list[Outbound] | None:
    """A picture from a customer whose order waits for money. None when it is not a payment
    screenshot (or nothing waits), so the order agent answers as usual."""
    image = turn.media
    if turn.message.kind != Kind.IMAGE or not isinstance(image, ImagePart):
        return None
    async with services.db.tenant(turn.tenant.id) as session:
        waiting = await _waiting(session, turn.conversation.customer_id)
    if not waiting:
        return None
    picture = await _read(services, turn, image, waiting)
    if picture.reading is not None and not picture.reading.is_payment:
        return None
    elsewhere = await _seen_elsewhere(services, turn.tenant.id, picture)
    async with services.db.tenant(turn.tenant.id) as session:
        tenant = await session.get(Tenant, turn.tenant.id)
        orders = await _waiting(session, turn.conversation.customer_id)
        if tenant is None or not orders:
            return None
        order = _pick(orders, picture, turn.message.body or "")
        return await _claim(services, session, tenant, order, turn, picture, elsewhere)


async def _claim(
    services: Services,
    session: AsyncSession,
    tenant: Tenant,
    order: Order,
    turn: Turn,
    picture: Picture,
    elsewhere: bool,
) -> list[Outbound]:
    earlier = await _earlier(session, picture)
    if any(claim.order_id == order.id for claim in earlier):
        return [OutText(t("payment.already", turn.lang, code=order.code))]
    signals = _signals(tenant, order, picture)
    if earlier:
        signals.append(Signal("reused_here"))
    if elsewhere:
        signals.append(Signal("reused_elsewhere"))
    claim = _new_claim(tenant, order, turn, picture, signals)
    session.add(claim)
    await session.flush()
    return await _decide(services, session, tenant, order, claim, turn.lang)


def _new_claim(
    tenant: Tenant, order: Order, turn: Turn, picture: Picture, signals: list[Signal]
) -> PaymentClaim:
    claim = PaymentClaim(
        tenant_id=tenant.id,
        order_id=order.id,
        conversation_id=turn.conversation.id,
        media_id=turn.message.media_id,
        amount=picture.amount,
        currency=order.currency,
        signals=[signal.as_dict() for signal in signals],
        phash=picture.phash,
        status=ClaimStatus.PENDING,
        remarks="",
    )
    reading = picture.reading
    if reading is not None:
        payee = " ".join(filter(None, [reading.payee_name, reading.payee_account]))
        claim.provider = (reading.provider or "")[:60] or None
        claim.transaction_id = (reading.transaction_id or "")[:60] or None
        claim.paid_at = forensics.when(reading.paid_at, get_pack(tenant.country).timezone)
        claim.payer = (reading.payer_name or "")[:120] or None
        claim.payee = payee[:160] or None
        claim.remarks = (reading.remarks or "")[:500]
        if find_order_code(claim.remarks, [order.code]):
            claim.order_code = order.code
    return claim


def _signals(tenant: Tenant, order: Order, picture: Picture) -> list[Signal]:
    if picture.reading is None:
        return [Signal("unread")]
    pack = get_pack(tenant.country)
    expected = Expected(
        due=order.due_now,
        digits=pack.minor_digits,
        receiving=str((tenant.profile or {}).get("payment") or ""),
        placed_at=order.placed_at,
        now=utcnow(),
        timezone=pack.timezone,
    )
    return forensics.signals(picture.reading, expected)


async def _decide(
    services: Services,
    session: AsyncSession,
    tenant: Tenant,
    order: Order,
    claim: PaymentClaim,
    lang: str,
) -> list[Outbound]:
    codes = {signal["code"] for signal in claim.signals}
    credits = {credit.id: credit for credit in await unused_credits(session, tenant.id)}
    match = credit_for_claim(claim_facts(claim), [credit_facts(c) for c in credits.values()])
    credit = credits[match.id] if match else None
    blocked = why_not_alone(tenant, credit) if credit is not None else None
    if credit is not None and blocked is None and not codes & _REUSED:
        receipt = await settle(
            session,
            tenant,
            order,
            amount=credit.amount,
            verified_by=VerifiedBy.SMS,
            secret_key=services.settings.secret_key.get_secret_value(),
            claim=claim,
            credit=credit,
        )
        return [OutText(paid_text(tenant, order, receipt, lang, services.settings.web_url))]
    if credit is not None:
        claim.credit_id = credit.id
        await to_review(session, claim, *([blocked] if blocked else []))
        return [OutText(t("payment.with_owner", lang, code=order.code))]
    # With no forwarder, no SMS will ever come: only the owner can say the money arrived.
    extra = () if tenant.sms_secret_hash else (Signal("no_forwarder"),)
    if not (codes & STRONG or "unread" in codes or extra):
        return [OutText(t("payment.checking", lang, code=order.code))]
    await to_review(session, claim, *extra)
    return [OutText(t("payment.with_owner", lang, code=order.code))]
