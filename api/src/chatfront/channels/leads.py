"""Leads (features 78 and 81): who came from a story, a mention or an ad, and which orders
they brought.

Meta tells the webhook where a chat began: a reply to the shop's Instagram story, a story in
which a customer mentioned the shop, an ad that opened the chat (click-to-WhatsApp, or an
Instagram or Messenger ad), or the shop's own ig.me or m.me link with a ref. Code keeps each as
a lead and, when that customer places an order within seven days, credits the order to the
latest one, so the console can show what each ad and each story brought. A mention is thanked
once a day and the owner is told; a story reply goes on to the order agent like any message.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import timedelta
from enum import StrEnum
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from chatfront.channels.models import Lead
from chatfront.core.clock import utcnow
from chatfront.orders.models import Order
from chatfront.orders.states import OrderStatus

ATTRIBUTION = timedelta(days=7)
THANKS_EVERY = timedelta(days=1)
NOT_PLACED = (OrderStatus.CART, OrderStatus.CANCELLED)


class LeadKind(StrEnum):
    STORY_REPLY = "story_reply"
    STORY_MENTION = "story_mention"
    AD = "ad"
    LINK = "link"  # the shop's own ig.me or m.me link with a ref


@dataclass(frozen=True, slots=True)
class Source:
    kind: LeadKind
    ref: str  # the ad's id, the link's ref, or the story's id
    title: str  # the ad's headline, when Meta gives it
    url: str


def _referral(referral: dict[str, Any]) -> Source | None:
    """WhatsApp: {"source_type": "ad", "source_id", "headline", "source_url"}; Instagram and
    Messenger: {"source": "ADS", "ad_id", "ads_context_data": {"ad_title"}} or a link's
    {"ref", "source": "SHORTLINK"}."""
    context = referral.get("ads_context_data") or {}
    ad = referral.get("ad_id") or (
        referral.get("source_id") if referral.get("source_type") == "ad" else None
    )
    if ad or str(referral.get("source", "")).upper() == "ADS":
        return Source(
            LeadKind.AD,
            str(ad or referral.get("ref") or "ad")[:120],
            str(referral.get("headline") or context.get("ad_title") or "")[:200],
            str(referral.get("source_url") or context.get("photo_url") or "")[:500],
        )
    ref = referral.get("ref") or referral.get("source_id")
    if ref:
        return Source(
            LeadKind.LINK, str(ref)[:120], "", str(referral.get("source_url") or "")[:500]
        )
    return None


def source_of(payload: dict[str, Any]) -> Source | None:
    if (referral := payload.get("referral")) and isinstance(referral, dict):
        return _referral(referral)
    reply_to = payload.get("reply_to")
    if isinstance(reply_to, dict) and isinstance(reply_to.get("story"), dict):
        story = reply_to["story"]
        return Source(
            LeadKind.STORY_REPLY,
            str(story.get("id") or "")[:120],
            "",
            str(story.get("url") or "")[:500],
        )
    if payload.get("attachment_type") == "story_mention":
        return Source(LeadKind.STORY_MENTION, "", "", str(payload.get("url") or "")[:500])
    return None


async def note(
    session: AsyncSession,
    tenant_id: uuid.UUID,
    customer_id: uuid.UUID,
    conversation_id: uuid.UUID,
    channel: str,
    payload: dict[str, Any],
    text: str | None,
) -> Lead | None:
    source = source_of(payload)
    if source is None:
        return None
    lead = Lead(
        tenant_id=tenant_id,
        customer_id=customer_id,
        conversation_id=conversation_id,
        channel=channel,
        kind=source.kind,
        ref=source.ref,
        title=source.title,
        url=source.url,
        text=(text or "")[:1000],
    )
    session.add(lead)
    await session.flush()
    return lead


async def attribute(session: AsyncSession, order: Order) -> None:
    """Credit a placed order to the customer's latest lead of the last seven days."""
    since = (order.placed_at or utcnow()) - ATTRIBUTION
    lead = await session.scalar(
        select(Lead)
        .where(Lead.customer_id == order.customer_id, Lead.created_at >= since)
        .order_by(Lead.created_at.desc())
        .limit(1)
    )
    if lead is not None:
        order.attribution = {
            "lead": str(lead.id),
            "kind": lead.kind,
            "ref": lead.ref,
            "title": lead.title,
        }


async def thanked_lately(session: AsyncSession, customer_id: uuid.UUID, lead_id: uuid.UUID) -> bool:
    """Whether this customer's mention was thanked in the last day."""
    count = await session.scalar(
        select(func.count(Lead.id)).where(
            Lead.customer_id == customer_id,
            Lead.kind == LeadKind.STORY_MENTION,
            Lead.id != lead_id,
            Lead.created_at >= utcnow() - THANKS_EVERY,
        )
    )
    return bool(count)


@dataclass(frozen=True, slots=True)
class Brought:
    kind: str
    ref: str
    title: str
    chats: int  # leads from it
    orders: int  # placed orders credited to it
    value: int


async def brought(session: AsyncSession, since_days: int) -> list[Brought]:
    """What each ad, link and story kind brought in the last days, the most orders first."""
    since = utcnow() - timedelta(days=since_days)
    chats = {
        (kind, ref): (int(count), title)
        for kind, ref, title, count in (
            await session.execute(
                select(Lead.kind, Lead.ref, func.max(Lead.title), func.count(Lead.id))
                .where(Lead.created_at >= since)
                .group_by(Lead.kind, Lead.ref)
            )
        ).all()
    }
    kind_col, ref_col = Order.attribution["kind"].astext, Order.attribution["ref"].astext
    sold = {
        (kind, ref): (int(count), int(value))
        for kind, ref, count, value in (
            await session.execute(
                select(
                    kind_col, ref_col, func.count(Order.id), func.coalesce(func.sum(Order.total), 0)
                )
                .where(
                    Order.attribution.is_not(None),
                    Order.placed_at >= since,
                    Order.status.not_in(NOT_PLACED),
                )
                .group_by(kind_col, ref_col)
            )
        ).all()
    }
    rows = []
    for key in {*chats, *sold}:
        count, title = chats.get(key, (0, ""))
        orders, value = sold.get(key, (0, 0))
        rows.append(Brought(key[0], key[1] or "", title or "", count, orders, value))
    return sorted(rows, key=lambda row: (-row.orders, -row.value, -row.chats))
