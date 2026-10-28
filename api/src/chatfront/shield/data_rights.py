"""A customer's own data (feature 44): everything the shop holds about them as one file, and
erasing it when they ask.

A customer asks in their chat. A copy needs nothing from the owner: code sends a link that
works for a day and only for that customer. Erasing waits for the owner's Approve, because a
shop may have a reason to keep something for a while (an order on its way, money owed), and
the owner can also erase from the console. Erasing removes the name, the number, the address,
the messages and the photos they sent, the notes of their bookings and custom orders, and
their reviews. Orders stay in the shop's books as amounts with no person attached; a khata
that still has money owed keeps its name, since that is the shop's own claim.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Any

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from chatfront.audit.service import SYSTEM, Actor, ActorType, record
from chatfront.bookings.models import Appointment
from chatfront.books.models import UdhaarAccount
from chatfront.channels.language import reply_language
from chatfront.channels.models import Conversation, CustomerIdentity, Direction, Message
from chatfront.channels.notify import send_to
from chatfront.channels.types import OutText
from chatfront.core.clock import utcnow
from chatfront.core.i18n import t
from chatfront.core.models import Customer, Tenant
from chatfront.media.models import Media
from chatfront.orders.models import CustomRequest, Order, OrderItem, ReturnRequest
from chatfront.owner.rights import Can
from chatfront.shield.approvals import ApprovalKind
from chatfront.shield.models import Approval, Consent, Review

if TYPE_CHECKING:
    from chatfront.media.storage import Storage
    from chatfront.services import Services

ERASE = "data_erase"  # the approval kind
COPY_FOR = timedelta(days=1)
ERASED_ADDRESS_KEYS = ("city",)  # what an erased order keeps of its address: for the books
_MESSAGES = 2000


@dataclass(frozen=True, slots=True)
class Erased:
    messages: int
    media: int
    orders: int
    reviews: int


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode().rstrip("=")


def _mac(secret: str, body: str) -> str:
    return _b64(hmac.new(secret.encode(), f"mydata:{body}".encode(), hashlib.sha256).digest())


def copy_token(secret: str, tenant_id: uuid.UUID, customer_id: uuid.UUID, now: datetime) -> str:
    """A link token for one customer's copy, good for a day."""
    body = f"{tenant_id.hex}.{customer_id.hex}.{int((now + COPY_FOR).timestamp())}"
    return f"{body}.{_mac(secret, body)}"


def read_token(secret: str, token: str, now: datetime) -> tuple[uuid.UUID, uuid.UUID] | None:
    parts = token.split(".")
    if len(parts) != 4:
        return None
    body = ".".join(parts[:3])
    if not hmac.compare_digest(parts[3], _mac(secret, body)):
        return None
    try:
        tenant_id, customer_id, until = uuid.UUID(parts[0]), uuid.UUID(parts[1]), int(parts[2])
    except ValueError:
        return None
    return (tenant_id, customer_id) if until >= now.timestamp() else None


def _when(moment: datetime | None) -> str | None:
    return moment.isoformat() if moment else None


async def _orders(session: AsyncSession, customer_id: uuid.UUID) -> list[dict[str, Any]]:
    found = list(
        await session.scalars(
            select(Order)
            .where(Order.customer_id == customer_id, Order.placed_at.is_not(None))
            .order_by(Order.placed_at)
        )
    )
    out = []
    for order in found:
        items = await session.scalars(select(OrderItem).where(OrderItem.order_id == order.id))
        out.append(
            {
                "code": order.code,
                "status": order.status,
                "placed_at": _when(order.placed_at),
                "total": order.total,
                "paid": order.paid,
                "address": order.address,
                "gift_message": order.gift_message or None,
                "items": [
                    {
                        "name": i.name,
                        "options": i.options,
                        "quantity": i.quantity,
                        "price": i.unit_price,
                    }
                    for i in items
                ],
            }
        )
    return out


async def _messages(session: AsyncSession, customer_id: uuid.UUID) -> list[dict[str, Any]]:
    rows = await session.execute(
        select(Message.direction, Message.kind, Message.body, Message.channel, Message.created_at)
        .join(Conversation, Conversation.id == Message.conversation_id)
        .where(Conversation.customer_id == customer_id)
        .order_by(Message.created_at)
        .limit(_MESSAGES)
    )
    return [
        {
            "from": "you" if direction == Direction.IN else "shop",
            "kind": kind,
            "text": body,
            "channel": channel,
            "at": _when(at),
        }
        for direction, kind, body, channel, at in rows
    ]


async def export(session: AsyncSession, tenant: Tenant, customer: Customer) -> dict[str, Any]:
    """Everything the shop holds about this customer, readable as it is."""
    cid = customer.id
    channels = await session.scalars(
        select(CustomerIdentity.channel).where(CustomerIdentity.customer_id == cid)
    )
    consents = await session.scalars(
        select(Consent).where(Consent.customer_id == cid).order_by(Consent.created_at)
    )
    reviews = await session.scalars(select(Review).where(Review.customer_id == cid))
    bookings = await session.scalars(select(Appointment).where(Appointment.customer_id == cid))
    custom = await session.scalars(select(CustomRequest).where(CustomRequest.customer_id == cid))
    khata = await session.scalar(select(UdhaarAccount).where(UdhaarAccount.customer_id == cid))
    returns = await session.scalars(
        select(ReturnRequest)
        .join(Order, Order.id == ReturnRequest.order_id)
        .where(Order.customer_id == cid)
    )
    return {
        "shop": tenant.name,
        "made_at": utcnow().isoformat(),
        "you": {
            "name": customer.display_name,
            "language": customer.lang,
            "verified_buyer": customer.verified_buyer,
            "since": _when(customer.created_at),
            "channels": sorted(set(channels)),
        },
        "orders": await _orders(session, cid),
        "returns": [{"code": r.code, "status": r.status, "reason": r.reason} for r in returns],
        "bookings": [
            {
                "code": a.code,
                "service": a.service,
                "starts_at": _when(a.starts_at),
                "status": a.status,
            }
            for a in bookings
        ],
        "custom_orders": [
            {
                "code": c.code,
                "kind": c.kind,
                "details": c.details,
                "price": c.price,
                "status": c.status,
            }
            for c in custom
        ],
        "khata": {"owes": khata.balance} if khata else None,
        "consents": [
            {
                "purpose": c.purpose,
                "granted": c.granted,
                "wording": c.wording,
                "at": _when(c.created_at),
            }
            for c in consents
        ],
        "reviews": [
            {"rating": r.rating, "text": r.text, "at": _when(r.created_at)} for r in reviews
        ],
        "messages": await _messages(session, cid),
    }


async def forget_identities(session: AsyncSession, customer_id: uuid.UUID) -> None:
    """The channel ids that reach the person: gone last, after they were told."""
    for identity in await session.scalars(
        select(CustomerIdentity).where(CustomerIdentity.customer_id == customer_id)
    ):
        await session.delete(identity)


async def erase(
    session: AsyncSession,
    storage: Storage,
    tenant_id: uuid.UUID,
    customer_id: uuid.UUID,
    actor: Actor,
    *,
    keep_identities: bool = False,
) -> Erased:
    """Remove the person from the shop's records; the shop's own amounts stay."""
    customer = await session.get(Customer, customer_id)
    if customer is None:
        return Erased(0, 0, 0, 0)
    chats = select(Conversation.id).where(Conversation.customer_id == customer_id)
    sent = list(
        await session.scalars(
            select(Message.media_id).where(
                Message.conversation_id.in_(chats),
                Message.direction == Direction.IN,
                Message.media_id.is_not(None),
            )
        )
    )
    messages = await session.execute(
        update(Message)
        .where(Message.conversation_id.in_(chats))
        .values(body=None, payload={}, media_id=None)
    )
    media = 0
    for media_id in sent:
        found = await session.get(Media, media_id)
        if found is None:
            continue
        for key in (found.storage_key, found.blurred_key):
            if key:
                await storage.delete(key)
        await session.delete(found)
        media += 1
    orders = list(await session.scalars(select(Order).where(Order.customer_id == customer_id)))
    for order in orders:
        order.address = {k: v for k, v in (order.address or {}).items() if k in ERASED_ADDRESS_KEYS}
        order.notes, order.gift_message, order.gift_from, order.group_member = "", "", "", ""
    reviews = list(await session.scalars(select(Review).where(Review.customer_id == customer_id)))
    for review in reviews:
        await session.delete(review)
    for appointment in await session.scalars(
        select(Appointment).where(Appointment.customer_id == customer_id)
    ):
        appointment.note = ""
    for request in await session.scalars(
        select(CustomRequest).where(CustomRequest.customer_id == customer_id)
    ):
        request.details, request.note = {"kind": request.kind}, ""
    khata = await session.scalar(
        select(UdhaarAccount).where(UdhaarAccount.customer_id == customer_id)
    )
    if khata is not None and khata.balance <= 0:
        khata.name = t("data.erased_name", "en")
    if not keep_identities:
        await forget_identities(session, customer_id)
    customer.display_name, customer.phone_hash = None, None
    done = Erased(int(getattr(messages, "rowcount", 0) or 0), media, len(orders), len(reviews))
    await record(
        session,
        tenant_id,
        actor,
        "customer.erased",
        entity=("customer", str(customer_id)),
        data={"messages": done.messages, "media": done.media, "orders": done.orders},
    )
    await session.flush()
    return done


# -- asked in the chat ------------------------------------------------------------------------


def describe(tenant: Tenant, approval: Approval, lang: str) -> str:
    del tenant
    return t("approval.data_erase", lang, name=approval.payload.get("name") or "-")


async def carry_out(services: Services, tenant: Tenant, approval: Approval, approved: bool) -> None:
    customer_id = uuid.UUID(approval.payload["customer_id"])
    async with services.db.tenant(tenant.id) as session:
        chat = await session.scalar(
            select(Conversation)
            .where(Conversation.customer_id == customer_id)
            .order_by(Conversation.last_inbound_at.desc().nulls_last())
            .limit(1)
        )
        lang = reply_language(chat, tenant) if chat else tenant.default_lang
        if approved:
            actor = (
                Actor(ActorType.STAFF, str(approval.decided_by)) if approval.decided_by else SYSTEM
            )
            await erase(
                session, services.storage, tenant.id, customer_id, actor, keep_identities=True
            )
    if chat is not None:
        key = "data.erased" if approved else "data.erase_refused"
        await send_to(services, tenant.id, chat.id, OutText(t(key, lang, shop=tenant.name)), lang)
    if approved:  # told on the channel they asked on, and only then forgotten there
        async with services.db.tenant(tenant.id) as session:
            await forget_identities(session, customer_id)


KIND = ApprovalKind(Can.TRUST, describe, carry_out)
