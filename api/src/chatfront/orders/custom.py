"""Custom orders (feature 17): a cake with a name on it, a suit stitched to measure, a printed
card or shirt.

The order agent takes down what the customer wants (the words, the colours, the size, the day
it is needed) and code draws a sketch of it, so both sides agree on the words before a price
is given. The owner sees the request and the sketch in the console and quotes a price; the
customer accepts or declines with a button. Accepted, it goes into their cart as one line at
the quoted price, and the order goes on like any other. The model never sets the price.
"""

from __future__ import annotations

import logging
import uuid
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, Field
from sqlalchemy import select

from chatfront.ai.tools import Tool
from chatfront.audit.service import Actor, record
from chatfront.catalog.models import Product, ProductStatus, Variant
from chatfront.channels.language import reply_language, staff_language
from chatfront.channels.models import Conversation
from chatfront.channels.notify import send_to, tell_owner
from chatfront.channels.types import Button, Outbound, OutButtons, OutText
from chatfront.core.clock import utcnow
from chatfront.core.countries import shop_money
from chatfront.core.errors import AppError, NotFound
from chatfront.core.i18n import t
from chatfront.core.ids import short_code
from chatfront.core.models import Tenant
from chatfront.core.provisioning import owner_of
from chatfront.jobs.queue import JobContext, enqueue
from chatfront.media import service as media_service
from chatfront.media.mockups import Kind, sketch
from chatfront.orders import service as orders
from chatfront.orders.models import CustomRequest, CustomStatus, Order, OrderItem
from chatfront.orders.tools import AgentContext

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from chatfront.services import Services

log = logging.getLogger(__name__)

BUTTON = "cq:"  # cq:<request id>:yes | no
SKETCH_JOB = "custom.sketch"
CODE_PREFIX = "C"
SOURCE = "mockup"
MAX_BYTES = 4 * 1024 * 1024
HIDDEN = "X"  # the code prefix of the hidden product a custom line stands on


class CustomIn(BaseModel):
    kind: Kind
    text: str = Field(default="", max_length=120, description="the words written on it")
    colours: list[str] = Field(default_factory=list, max_length=3)
    size: str = Field(default="", max_length=40, description="pounds, inches or a size")
    needed_by: str = Field(default="", max_length=30)
    notes: str = Field(default="", max_length=300)


def mockup_url(services: Services, slug: str, media_id: uuid.UUID) -> str:
    return f"{services.settings.public_base_url.rstrip('/')}/api/shops/{slug}/mockups/{media_id}"


def parse_button(button_id: str | None) -> tuple[uuid.UUID, bool] | None:
    if not button_id or not button_id.startswith(BUTTON):
        return None
    try:
        request_id, answer = button_id[len(BUTTON) :].split(":", 1)
        return uuid.UUID(request_id), answer == "yes"
    except ValueError:
        return None


async def custom_order(ctx: AgentContext, params: CustomIn) -> dict[str, Any]:
    """Take down a custom order; code draws its sketch and passes it to the owner to price."""
    async with ctx.db.tenant(ctx.tenant.id) as session:
        while True:
            code = f"{CODE_PREFIX}{short_code(5)}"
            taken = await session.scalar(select(CustomRequest.id).where(CustomRequest.code == code))
            if taken is None:
                break
        request = CustomRequest(
            tenant_id=ctx.tenant.id,
            code=code,
            customer_id=ctx.customer_id,
            conversation_id=ctx.conversation_id,
            kind=params.kind,
            details=params.model_dump(mode="json"),
        )
        session.add(request)
        await session.flush()
        await enqueue(
            session,
            SKETCH_JOB,
            {"request": str(request.id), "lang": ctx.lang},
            tenant_id=ctx.tenant.id,
        )
    ctx.facts.codes.add(code)
    return {
        "ok": True,
        "request": code,
        "next_step": "say a sketch is coming and the shop will send a price",
    }


def custom_tools() -> list[Tool]:
    return [
        Tool(
            "custom_order",
            "The customer wants something made for them: a cake with writing, stitching to "
            "measure, or a print. Take down the words, colours, size and the day it is needed; "
            "code draws a sketch and the owner sends the price. Never name a price yourself.",
            CustomIn,
            custom_order,
            "orders",
        )
    ]


async def sketch_job(ctx: JobContext) -> None:
    """Draw the sketch, send its link to the customer, and tell the owner."""
    services: Services = ctx.services
    assert ctx.tenant_id is not None
    lang = str(ctx.payload.get("lang") or "")
    async with services.db.tenant(ctx.tenant_id) as session:
        request = await session.get(CustomRequest, uuid.UUID(ctx.payload["request"]))
        tenant = await session.get(Tenant, ctx.tenant_id)
        if request is None or tenant is None or request.mockup_media_id is not None:
            return
        details = CustomIn.model_validate(request.details)
        notes = [line for line in (details.size, details.needed_by, details.notes) if line][:3]
        picture = sketch(details.kind, details.text, details.colours, notes)
        media = await media_service.store(
            session, services.storage, tenant.id, picture, source=SOURCE, max_bytes=MAX_BYTES
        )
        request.mockup_media_id = media.id
        chat_id = request.conversation_id
    link = mockup_url(services, tenant.slug, media.id)
    if chat_id is not None:
        text = t("custom.sent", lang or tenant.default_lang, code=request.code, link=link)
        await send_to(services, tenant.id, chat_id, OutText(text), lang or tenant.default_lang)
    owner = await owner_of(services.db, tenant.id)
    owner_lang = staff_language(owner, tenant)
    kind = t(f"custom.kind.{request.kind}", owner_lang)
    notice = t("custom.owner_new", owner_lang, code=request.code, kind=kind, link=link)
    await tell_owner(services, tenant.id, owner, OutText(notice), owner_lang)


async def quote(
    services: Services,
    tenant_id: uuid.UUID,
    request_id: uuid.UUID,
    price: int,
    note: str,
    actor: Actor,
) -> CustomRequest:
    """The owner's price, sent to the customer to accept or decline."""
    async with services.db.tenant(tenant_id) as session:
        request = await session.get(CustomRequest, request_id)
        tenant = await session.get(Tenant, tenant_id)
        if request is None or tenant is None:
            raise NotFound("custom request")
        if request.status not in (CustomStatus.WAITING, CustomStatus.QUOTED):
            raise AppError("custom_closed", "This request was answered already", status_code=409)
        request.status, request.price, request.note = CustomStatus.QUOTED, price, note[:300]
        request.quoted_at = utcnow()
        await record(
            session,
            tenant_id,
            actor,
            "custom.quote",
            entity=("custom_request", str(request.id)),
            data={"code": request.code, "price": price},
        )
        chat = (
            await session.get(Conversation, request.conversation_id)
            if request.conversation_id
            else None
        )
        if chat is None:
            return request
        lang = reply_language(chat, tenant)
        text = t(
            "custom.quote",
            lang,
            code=request.code,
            price=shop_money(tenant, price, lang),
            note=note or "-",
        )
        buttons = (
            Button(f"{BUTTON}{request.id}:yes", t("custom.accept", lang)),
            Button(f"{BUTTON}{request.id}:no", t("custom.decline", lang)),
        )
    await send_to(services, tenant_id, chat.id, OutButtons(text, buttons), lang)
    return request


async def _hidden(session: AsyncSession, tenant: Tenant, kind: str) -> Variant:
    """The product a custom line stands on: never on sale, never in the catalog."""
    code = f"{HIDDEN}{kind[:5].upper()}"
    product = await session.scalar(select(Product).where(Product.code == code))
    if product is None:
        product = Product(
            tenant_id=tenant.id,
            code=code,
            status=ProductStatus.ARCHIVED,
            name=t(f"custom.kind.{kind}", tenant.default_lang),
            price=0,
            attributes={"custom": kind},
            source="custom",
        )
        session.add(product)
        await session.flush()
        session.add(Variant(tenant_id=tenant.id, product_id=product.id, options={}, stock=None))
        await session.flush()
    variant = await session.scalar(select(Variant).where(Variant.product_id == product.id))
    assert variant is not None
    return variant


async def answer(
    session: AsyncSession,
    tenant: Tenant,
    customer_id: uuid.UUID,
    conversation_id: uuid.UUID,
    request_id: uuid.UUID,
    yes: bool,
    lang: str,
) -> list[Outbound]:
    request = await session.get(CustomRequest, request_id)
    if request is None or request.customer_id != customer_id:
        return [OutText(t("custom.gone", lang))]
    if request.status != CustomStatus.QUOTED or request.price is None:
        return [OutText(t("custom.gone", lang))]
    if not yes:
        request.status = CustomStatus.DECLINED
        return [OutText(t("custom.declined", lang, code=request.code))]
    cart = await orders.open_cart(session, tenant, customer_id, conversation_id=conversation_id)
    variant = await _hidden(session, tenant, request.kind)
    title = " ".join(
        part for part in (t(f"custom.kind.{request.kind}", lang), request.code) if part
    )
    session.add(
        OrderItem(
            tenant_id=tenant.id,
            order_id=cart.id,
            product_id=variant.product_id,
            variant_id=variant.id,
            name=title[:160],
            options={"text": str(request.details.get("text") or "")[:80]},
            unit_price=request.price,
            quantity=1,
            line_total=request.price,
        )
    )
    await session.flush()
    await orders.recalculate(session, cart)
    request.status, request.order_id = CustomStatus.ACCEPTED, cart.id
    return [
        OutText(
            t(
                "custom.accepted",
                lang,
                code=request.code,
                price=shop_money(tenant, request.price, lang),
            )
        )
    ]


async def by_order(session: AsyncSession, order: Order) -> CustomRequest | None:
    return await session.scalar(select(CustomRequest).where(CustomRequest.order_id == order.id))
