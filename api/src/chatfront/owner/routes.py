"""The owner console's own endpoints. Its chat goes through the shared chat API with a Bearer
session; these cover signing in to a demo copy, the owner's view of the shop, and the chats
handed over to a person (feature 90)."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Header, Request
from pydantic import BaseModel, Field
from sqlalchemy import select

from chatfront.catalog.models import ProductStatus
from chatfront.catalog.service import load_items
from chatfront.catalog.translations import set_owner_text, target_languages
from chatfront.channels.handover import (
    HANDOVER,
    reply_as_staff,
    resume,
    show_to_staff,
    staff_language,
)
from chatfront.channels.models import (
    ChannelAccount,
    Conversation,
    Direction,
    Handover,
    HandoverStatus,
    Message,
)
from chatfront.core.errors import AppError, NotFound
from chatfront.core.models import Customer, Staff, Tenant
from chatfront.owner.sandbox import copy_demo_shop
from chatfront.owner.sessions import NotSignedIn, OwnerSession, verify
from chatfront.services import Services, get_services

router = APIRouter(prefix="/api/owner")
ServicesDep = Annotated[Services, Depends(get_services)]


def owner_session(
    services: ServicesDep, authorization: Annotated[str | None, Header(max_length=600)] = None
) -> OwnerSession:
    scheme, _, token = (authorization or "").partition(" ")
    if scheme.lower() != "bearer" or not token:
        raise NotSignedIn
    return verify(token, services.settings.secret_key.get_secret_value())


OwnerDep = Annotated[OwnerSession, Depends(owner_session)]


class DemoRequest(BaseModel):
    slug: str = Field(min_length=1, max_length=60)


class SignedIn(BaseModel):
    token: str
    slug: str


class Me(BaseModel):
    slug: str
    shop_name: str
    staff_name: str
    lang: str
    template: str
    country: str
    currency: str
    is_demo: bool
    languages: list[str]  # the customer languages product texts are written in, beside English


class OwnerVariant(BaseModel):
    id: uuid.UUID
    options: dict[str, str]
    price: int
    stock: int | None


class OwnerProduct(BaseModel):
    id: uuid.UUID
    code: str
    status: str
    name: str
    price: int
    floor_price: int | None
    translations: dict[str, Any]
    variants: list[OwnerVariant]


class OwnerText(BaseModel):
    name: str = Field(min_length=1, max_length=160)
    description: str = Field(default="", max_length=2000)


@router.post("/demo")
async def demo_owner(body: DemoRequest, request: Request, services: ServicesDep) -> SignedIn:
    """Your own copy of a demo shop, signed in as its owner (feature 109)."""
    client = request.client.host if request.client else "unknown"
    services.limiter.check(f"demo-owner:{client}")
    tenant, token = await copy_demo_shop(
        services.db,
        services.registries.modules,
        body.slug,
        services.settings.secret_key.get_secret_value(),
    )
    return SignedIn(token=token, slug=tenant.slug)


@router.get("/me")
async def me(session: OwnerDep, services: ServicesDep) -> Me:
    async with services.db.tenant(session.tenant_id) as db:
        tenant = await db.get(Tenant, session.tenant_id)
        staff = await db.get(Staff, session.staff_id)
    if tenant is None or staff is None or not staff.active:
        raise NotSignedIn
    return Me(
        slug=tenant.slug,
        shop_name=tenant.name,
        staff_name=staff.name,
        lang=staff.lang or tenant.default_lang,
        template=tenant.template,
        country=tenant.country,
        currency=tenant.currency,
        is_demo=tenant.is_demo,
        languages=target_languages(tenant),
    )


@router.get("/products")
async def products(session: OwnerDep, services: ServicesDep) -> list[OwnerProduct]:
    async with services.db.tenant(session.tenant_id) as db:
        items = await load_items(db, statuses=tuple(ProductStatus))
    return [
        OwnerProduct(
            id=item.product.id,
            code=item.product.code,
            status=item.product.status,
            name=item.product.name,
            price=item.product.price,
            floor_price=item.product.floor_price,
            translations=item.product.translations,
            variants=[
                OwnerVariant(id=v.id, options=v.options, price=item.price_of(v), stock=v.stock)
                for v in item.variants
            ],
        )
        for item in items
    ]


@router.put("/products/{product_id}/translations/{lang}")
async def set_translation(
    product_id: uuid.UUID, lang: str, body: OwnerText, session: OwnerDep, services: ServicesDep
) -> dict[str, bool]:
    """The owner's own wording in one language; a model never overwrites it."""
    async with services.db.tenant(session.tenant_id) as db:
        await set_owner_text(db, product_id, lang, body.name, body.description)
    return {"saved": True}


# -- chats handed over to the owner (feature 90) ------------------------------------------


class HandoverOut(BaseModel):
    id: uuid.UUID
    conversation_id: uuid.UUID
    customer: str | None
    channel: str
    reason: str
    summary: str
    requested_by: str
    created_at: datetime


class ConsoleMessage(BaseModel):
    id: uuid.UUID
    direction: str
    kind: str
    body: str | None  # in the owner's language when it was translated
    original: str | None  # the words as written, when body is a translation
    by: str  # customer, agent or staff
    created_at: datetime


class StaffReply(BaseModel):
    text: str = Field(min_length=1, max_length=2000)


@router.get("/handovers")
async def handovers(session: OwnerDep, services: ServicesDep) -> list[HandoverOut]:
    async with services.db.tenant(session.tenant_id) as db:
        rows = await db.execute(
            select(Handover, Customer.display_name, ChannelAccount.channel)
            .join(Conversation, Conversation.id == Handover.conversation_id)
            .join(Customer, Customer.id == Conversation.customer_id)
            .join(ChannelAccount, ChannelAccount.id == Conversation.channel_account_id)
            .where(Handover.status == HandoverStatus.OPEN)
            .order_by(Handover.created_at.desc())
            .limit(50)
        )
        return [
            HandoverOut(
                id=handover.id,
                conversation_id=handover.conversation_id,
                customer=name,
                channel=channel,
                reason=handover.reason,
                summary=handover.summary,
                requested_by=handover.requested_by,
                created_at=handover.created_at,
            )
            for handover, name, channel in rows.all()
        ]


async def _conversation(
    services: Services, session: OwnerSession, conversation_id: uuid.UUID
) -> Conversation:
    async with services.db.tenant(session.tenant_id) as db:
        conversation = await db.get(Conversation, conversation_id)
    if conversation is None:
        raise NotFound("conversation")
    return conversation


@router.get("/conversations/{conversation_id}/messages")
async def conversation_messages(
    conversation_id: uuid.UUID, session: OwnerDep, services: ServicesDep
) -> list[ConsoleMessage]:
    """The chat, newest last, with the customer's words in the owner's language."""
    await _conversation(services, session, conversation_id)
    async with services.db.tenant(session.tenant_id) as db:
        staff = await db.get(Staff, session.staff_id)
        tenant = await db.get(Tenant, session.tenant_id)
        messages = list(
            await db.scalars(
                select(Message)
                .where(Message.conversation_id == conversation_id)
                .order_by(Message.created_at.desc())
                .limit(50)
            )
        )[::-1]
    if staff is None or tenant is None:
        raise NotSignedIn
    reader = staff_language(staff, tenant)
    out = []
    for message in messages:
        payload = message.payload or {}
        body, original = message.body, None
        if message.direction == Direction.IN and message.body:
            shown = await show_to_staff(services, session.tenant_id, message.id, reader)
            if shown.translated:
                body, original = shown.text, message.body
        elif payload.get("original"):
            original = payload["original"]
        by = "customer" if message.direction == Direction.IN else payload.get("by", "agent")
        out.append(
            ConsoleMessage(
                id=message.id,
                direction=message.direction,
                kind=message.kind,
                body=body,
                original=original,
                by=by,
                created_at=message.created_at,
            )
        )
    return out


@router.post("/conversations/{conversation_id}/reply")
async def staff_reply(
    conversation_id: uuid.UUID, body: StaffReply, session: OwnerDep, services: ServicesDep
) -> dict[str, bool]:
    """The owner's words to the customer, translated into the customer's language. Only in a
    handed-over chat, so the owner and the agent never answer at once."""
    conversation = await _conversation(services, session, conversation_id)
    if conversation.status != HANDOVER:
        raise AppError("not_handed_over", "The assistant has this chat", status_code=409)
    await reply_as_staff(services, session.tenant_id, conversation_id, session.staff_id, body.text)
    return {"sent": True}


@router.post("/handovers/{handover_id}/resume")
async def resume_handover(
    handover_id: uuid.UUID, session: OwnerDep, services: ServicesDep
) -> dict[str, bool]:
    return {"resumed": await resume(services, session.tenant_id, handover_id, session.staff_id)}
