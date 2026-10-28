"""The unified inbox (feature 76): every customer chat, from WhatsApp, Instagram, Messenger
and the web chat, newest first, with who has it.

A chat is with the assistant, waiting for a person (handed over, nobody has it yet), or with a
named person. Anyone who answers chats can take one; the owner can give one to someone else.
Taking or giving a chat stops the assistant there; giving it back clears who had it.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from enum import StrEnum
from typing import Annotated, Literal

from fastapi import APIRouter, Query
from pydantic import BaseModel
from sqlalchemy import Select, func, select
from sqlalchemy.dialects.postgresql import distinct_on
from sqlalchemy.ext.asyncio import AsyncSession

from chatfront.audit.service import Actor, ActorType, record
from chatfront.channels.base import SERVICE_WINDOW
from chatfront.channels.handover import start_handover
from chatfront.channels.models import (
    Channel,
    ChannelAccount,
    Conversation,
    CustomerIdentity,
    Direction,
    Handover,
    HandoverStatus,
    Message,
    Party,
)
from chatfront.core.clock import utcnow
from chatfront.core.errors import AppError, Forbidden, NotFound
from chatfront.core.models import Customer, Staff, Tenant
from chatfront.owner.rights import Can, rights
from chatfront.owner.routes import ChatsDep, ServicesDep
from chatfront.owner.sessions import OwnerSession
from chatfront.services import Services

router = APIRouter(prefix="/api/owner/inbox")

PAGE = 50
PREVIEW = 140


class Show(StrEnum):
    ALL = "all"
    WAITING = "waiting"  # handed over, nobody has it yet
    MINE = "mine"


class Person(BaseModel):
    id: uuid.UUID
    name: str


class LastMessage(BaseModel):
    by: Literal["customer", "agent", "staff"]
    kind: str
    text: str | None  # as written, cut short
    at: datetime


class InboxItem(BaseModel):
    conversation_id: uuid.UUID
    customer: str | None
    channel: str
    known_on: list[str]  # every channel this customer has written on (feature 79)
    state: Literal["assistant", "waiting", "person"]
    handover_id: uuid.UUID | None
    summary: str | None  # the handover's three lines
    assigned: Person | None
    last: LastMessage | None
    window_open: bool  # a reply can still go out without a template


class Inbox(BaseModel):
    items: list[InboxItem]
    waiting: int  # handed over and nobody has it, across the whole inbox
    people: list[Person]  # who can be given a chat
    can_assign: bool  # may give chats to others (the owner)


class Assign(BaseModel):
    staff_id: uuid.UUID | None  # None: back to waiting for a person


def _last_messages() -> Select[tuple[uuid.UUID, str, str, str | None, str | None, datetime]]:
    return (
        select(
            Message.conversation_id,
            Message.direction,
            Message.kind,
            Message.body,
            Message.payload["by"].astext,
            Message.created_at,
        )
        .ext(distinct_on(Message.conversation_id))
        .order_by(Message.conversation_id, Message.created_at.desc())
    )


def _window_open(channel: str, last_inbound_at: datetime | None) -> bool:
    if channel == Channel.WEBCHAT:
        return True
    return last_inbound_at is not None and utcnow() - last_inbound_at <= SERVICE_WINDOW


async def _known_on(
    session: AsyncSession, customers: list[uuid.UUID]
) -> dict[uuid.UUID, list[str]]:
    rows = await session.execute(
        select(CustomerIdentity.customer_id, CustomerIdentity.channel)
        .where(CustomerIdentity.customer_id.in_(customers))
        .distinct()
    )
    found: dict[uuid.UUID, list[str]] = {}
    for customer_id, channel in rows.all():
        found.setdefault(customer_id, []).append(channel)
    return {key: sorted(value) for key, value in found.items()}


async def _items(
    session: AsyncSession,
    me: uuid.UUID,
    *,
    show: Show = Show.ALL,
    channel: Channel | None = None,
    only: uuid.UUID | None = None,
) -> list[InboxItem]:
    last = _last_messages().subquery()
    handover = (
        select(Handover.id, Handover.conversation_id, Handover.summary)
        .where(Handover.status == HandoverStatus.OPEN)
        .subquery()
    )
    query = (
        select(
            Conversation,
            Customer.display_name,
            ChannelAccount.channel,
            Staff.name,
            handover.c.id,
            handover.c.summary,
            last,
        )
        .join(Customer, Customer.id == Conversation.customer_id)
        .join(ChannelAccount, ChannelAccount.id == Conversation.channel_account_id)
        .outerjoin(Staff, Staff.id == Conversation.assigned_staff_id)
        .outerjoin(handover, handover.c.conversation_id == Conversation.id)
        .outerjoin(last, last.c.conversation_id == Conversation.id)
        .where(Conversation.party == Party.CUSTOMER)
        .order_by(func.coalesce(last.c.created_at, Conversation.created_at).desc())
        .limit(PAGE)
    )
    if show is Show.WAITING:
        query = query.where(handover.c.id.is_not(None), Conversation.assigned_staff_id.is_(None))
    elif show is Show.MINE:
        query = query.where(Conversation.assigned_staff_id == me)
    if channel is not None:
        query = query.where(ChannelAccount.channel == channel)
    if only is not None:
        query = query.where(Conversation.id == only)
    rows = (await session.execute(query)).all()
    known = await _known_on(session, [row[0].customer_id for row in rows])
    items = []
    for chat, name, account_channel, assignee, handover_id, summary, *message in rows:
        _, direction, kind, body, by, at = message
        items.append(
            InboxItem(
                conversation_id=chat.id,
                customer=name,
                channel=account_channel,
                known_on=known.get(chat.customer_id, [account_channel]),
                state="assistant" if handover_id is None else "person" if assignee else "waiting",
                handover_id=handover_id,
                summary=summary,
                assigned=Person(id=chat.assigned_staff_id, name=assignee)
                if assignee and chat.assigned_staff_id
                else None,
                last=None
                if at is None
                else LastMessage(
                    by="customer" if direction == Direction.IN else "staff" if by else "agent",
                    kind=kind,
                    text=(body or "")[:PREVIEW] or None,
                    at=at,
                ),
                window_open=_window_open(account_channel, chat.last_inbound_at),
            )
        )
    return items


async def _people(session: AsyncSession) -> list[Person]:
    staff = await session.scalars(select(Staff).where(Staff.active.is_(True)).order_by(Staff.name))
    return [Person(id=s.id, name=s.name) for s in staff if Can.CHATS in rights(s.role)]


@router.get("")
async def inbox(
    session: ChatsDep,
    services: ServicesDep,
    show: Annotated[Show, Query()] = Show.ALL,
    channel: Annotated[Channel | None, Query()] = None,
) -> Inbox:
    async with services.db.tenant(session.tenant_id) as db:
        items = await _items(db, session.staff_id, show=show, channel=channel)
        waiting = await db.scalar(
            select(func.count(Handover.id))
            .join(Conversation, Conversation.id == Handover.conversation_id)
            .where(
                Handover.status == HandoverStatus.OPEN,
                Conversation.assigned_staff_id.is_(None),
            )
        )
        people = await _people(db)
        me = await db.get(Staff, session.staff_id)
    return Inbox(
        items=items,
        waiting=waiting or 0,
        people=people,
        can_assign=me is not None and Can.TEAM in rights(me.role),
    )


async def _hand_to(
    services: Services, session: OwnerSession, conversation_id: uuid.UUID, staff_id: uuid.UUID
) -> None:
    """The chat goes to this person: handed over first if the assistant still has it."""
    async with services.db.tenant(session.tenant_id) as db:
        chat = await db.get(Conversation, conversation_id)
        tenant = await db.get(Tenant, session.tenant_id)
        if chat is None or tenant is None or chat.party != Party.CUSTOMER:
            raise NotFound("conversation")
        open_one = await db.scalar(
            select(Handover.id).where(
                Handover.conversation_id == conversation_id,
                Handover.status == HandoverStatus.OPEN,
            )
        )
        if open_one is not None:
            chat.assigned_staff_id = staff_id
            await record(
                db,
                session.tenant_id,
                Actor(ActorType.STAFF, str(session.staff_id)),
                "conversation.assigned",
                entity=("conversation", str(conversation_id)),
                data={"to": str(staff_id)},
            )
            return
    await start_handover(
        services,
        tenant,
        conversation_id,
        reason="taken from the inbox",
        requested_by="staff",
        taken_by=staff_id,
    )


async def _item(services: Services, session: OwnerSession, conversation_id: uuid.UUID) -> InboxItem:
    async with services.db.tenant(session.tenant_id) as db:
        found = await _items(db, session.staff_id, only=conversation_id)
    if not found:
        raise NotFound("conversation")
    return found[0]


@router.post("/{conversation_id}/take")
async def take(conversation_id: uuid.UUID, session: ChatsDep, services: ServicesDep) -> InboxItem:
    """This chat is mine now; the assistant stops answering it."""
    await _hand_to(services, session, conversation_id, session.staff_id)
    return await _item(services, session, conversation_id)


@router.post("/{conversation_id}/assign")
async def assign(
    conversation_id: uuid.UUID, body: Assign, session: ChatsDep, services: ServicesDep
) -> InboxItem:
    """Give the chat to someone who answers chats (the owner), or back to waiting."""
    async with services.db.tenant(session.tenant_id) as db:
        me = await db.get(Staff, session.staff_id)
        allowed = me is not None and Can.TEAM in rights(me.role)
        person = await db.get(Staff, body.staff_id) if body.staff_id else None
    if not allowed and body.staff_id != session.staff_id:
        raise Forbidden("Only the owner gives chats to others")
    if body.staff_id is None:
        await _unassign(services, session, conversation_id)
        return await _item(services, session, conversation_id)
    if person is None or not person.active or Can.CHATS not in rights(person.role):
        raise AppError("cannot_answer_chats", "This person does not answer chats")
    await _hand_to(services, session, conversation_id, person.id)
    return await _item(services, session, conversation_id)


async def _unassign(services: Services, session: OwnerSession, conversation_id: uuid.UUID) -> None:
    async with services.db.tenant(session.tenant_id) as db:
        chat = await db.get(Conversation, conversation_id)
        if chat is None:
            raise NotFound("conversation")
        chat.assigned_staff_id = None
        await record(
            db,
            session.tenant_id,
            Actor(ActorType.STAFF, str(session.staff_id)),
            "conversation.assigned",
            entity=("conversation", str(conversation_id)),
            data={"to": None},
        )
