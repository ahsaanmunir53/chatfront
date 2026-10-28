"""One customer across channels (feature 79): a code sent from WhatsApp joins a web chat or
Instagram record to the WhatsApp one, with its chats, cart, orders, khata and verified status."""

from __future__ import annotations

import json
import re
import uuid
from datetime import timedelta

import httpx
import pytest
import respx
from sqlalchemy import func, select

from chatfront.books.models import UdhaarAccount
from chatfront.catalog.models import Product, Variant
from chatfront.channels import identity
from chatfront.channels import service as channels
from chatfront.channels.codes import link_code_of
from chatfront.channels.models import (
    Channel,
    ChannelAccount,
    Conversation,
    CustomerIdentity,
)
from chatfront.core.clock import utcnow
from chatfront.core.errors import AppError
from chatfront.core.models import Customer, Tenant
from chatfront.orders import service as orders
from chatfront.orders.models import Order, OrderItem
from chatfront.orders.states import OrderStatus
from chatfront.services import Services
from tests.integration.chat import (
    GRAPH,
    Client,
    agent,
    code_of,
    customer,
    said,
    say,
    shop_id,
    signed,
    use,
    wa_message,
)

WA_NUMBER = "923001234567"
CODE_IN_LINK = re.compile(r"text=LINK\+([0-9A-Z]{6})")


def _rust(code: str) -> dict[str, object]:
    return {"product_code": code, "options": {"colour": "Rust"}}


async def _whatsapp(client: Client, services: Services, text: str, mid: str) -> str:
    """A WhatsApp message from the customer's number; returns the shop's answer."""
    body, headers = signed(wa_message(mid, body=text))
    await client.post("/webhooks/meta", content=body, headers=headers)
    with respx.mock:
        send = respx.post(f"{GRAPH}/PN1/messages").mock(
            return_value=httpx.Response(200, json={"messages": [{"id": f"wamid.{mid}"}]})
        )
        assert services.worker is not None
        while await services.worker.run_once():
            pass
    return str(json.loads(send.calls[-1].request.content)["text"]["body"])


async def _whatsapp_customer(services: Services) -> Customer:
    async with services.db.system() as session:
        found = await session.scalar(
            select(Customer)
            .join(CustomerIdentity, CustomerIdentity.customer_id == Customer.id)
            .where(CustomerIdentity.channel == Channel.WHATSAPP)
        )
    assert found is not None
    return found


async def test_a_web_chat_joins_the_customers_whatsapp_record(
    app: tuple[Client, Services],
) -> None:
    client, services = app
    use(services, agent("salam", content="Wa alaikum assalam!"))
    await _whatsapp(client, services, "salam", "w1")
    known = await _whatsapp_customer(services)
    async with services.db.tenant(known.tenant_id) as session:
        stored = await session.get(Customer, known.id)
        assert stored is not None
        stored.verified_buyer, stored.verified_by = True, "owner"
        session.add(
            UdhaarAccount(
                tenant_id=known.tenant_id, name="Ayesha", customer_id=known.id, balance=50_000
            )
        )

    # On the website the same person fills a cart, then asks to join their WhatsApp chat.
    lawn = await code_of(client, "Printed lawn 3-piece")
    use(
        services,
        agent("rust wala lawn", calls={"add_to_cart": _rust(lawn)}),
        agent("rust wala lawn", step=1, content="Cart mein daal diya."),
        agent("whatsapp par bhi", calls={"link_whatsapp": {}}),
        agent("whatsapp par bhi", step=1, content="Link bhej diya hai."),
    )  # fmt: skip
    web, token = await customer(client)
    await say(client, services, "gulmohar-lawn", web, "rust wala lawn", token=token)
    replies = await say(
        client,
        services,
        "gulmohar-lawn",
        web,
        "main whatsapp par bhi order karti hoon",
        token=token,
    )
    card = next(m for m in replies if "wa.me/923000000000" in (m["body"] or ""))
    found = CODE_IN_LINK.search(card["body"])
    assert found is not None
    code = found.group(1)

    answer = await _whatsapp(client, services, f"LINK {code}", "w2")
    assert answer in said("link.done")
    web_chat = await say(client, services, "gulmohar-lawn", web, "shukriya", token=token)
    assert any(m["body"] in said("link.done_there") for m in web_chat)

    async with services.db.tenant(known.tenant_id) as session:
        people = await session.scalar(select(func.count(Customer.id)))
        merged = await session.get(Customer, known.id)
        chats = list(
            await session.scalars(select(Conversation).where(Conversation.customer_id == known.id))
        )
        cart = await session.scalar(
            select(Order).where(Order.customer_id == known.id, Order.status == OrderStatus.CART)
        )
        khata = await session.scalar(
            select(UdhaarAccount).where(UdhaarAccount.customer_id == known.id)
        )
    assert people == 1
    assert merged is not None
    assert merged.verified_buyer
    assert len(chats) == 2  # WhatsApp and the web chat, one person
    assert cart is not None
    assert khata is not None

    # A code works once.
    assert await _whatsapp(client, services, f"link {code.lower()}", "w3") in said("link.unknown")


async def test_the_tool_needs_another_channel_and_a_whatsapp_number(
    app: tuple[Client, Services],
) -> None:
    client, services = app
    mock = use(
        services,
        agent("join whatsapp", calls={"link_whatsapp": {}}),
        agent("join whatsapp", step=1, content="Sorry."),
    )
    guest, token = await customer(client, "saffron-kitchen")  # no WhatsApp number connected
    await say(client, services, "saffron-kitchen", guest, "join whatsapp please", token=token)
    tool_result = next(m for m in mock.requests[-1].messages if m.role == "tool")
    assert json.loads(tool_result.text())["error"] == "no_whatsapp"

    async with services.db.system() as session:
        tenant_id = await shop_id(session)
    async with services.db.tenant(tenant_id) as session:
        person = Customer(tenant_id=tenant_id)
        session.add(person)
        await session.flush()
        with pytest.raises(AppError, match="already on WhatsApp"):
            await identity.start_link(
                session, tenant_id, person.id, uuid.uuid4(), Channel.WHATSAPP, key="k", now=utcnow()
            )


async def test_codes_expire_and_only_their_own_message_counts(
    app: tuple[Client, Services],
) -> None:
    _, services = app
    assert link_code_of("LINK k7q2m9") == "K7Q2M9"
    assert link_code_of("  link K7Q2M9. ") == "K7Q2M9"
    assert link_code_of("please link K7Q2M9") is None
    assert link_code_of("LINK K7Q2M") is None
    async with services.db.system() as session:
        tenant_id = await shop_id(session)
    now = utcnow()
    async with services.db.tenant(tenant_id) as session:
        first, second = Customer(tenant_id=tenant_id), Customer(tenant_id=tenant_id)
        session.add_all([first, second])
        await session.flush()
        code = await identity.start_link(
            session, tenant_id, first.id, None, Channel.INSTAGRAM, key="k", now=now
        )
        late = now + identity.LINK_LIFETIME + timedelta(seconds=1)
        expired = await identity.redeem(session, tenant_id, second.id, code, key="k", now=late)
        same = await identity.redeem(session, tenant_id, first.id, code, key="k", now=now)
    assert expired.outcome is identity.Linked.UNKNOWN
    assert same.outcome is identity.Linked.ALREADY


async def test_an_instagram_record_joins_with_its_cart_and_two_web_chats_stay_apart(
    app: tuple[Client, Services],
) -> None:
    client, services = app
    lawn = await code_of(client, "Printed lawn 3-piece")
    use(
        services,
        agent("lawn rust", calls={"add_to_cart": _rust(lawn)}),
        agent("lawn rust", step=1, content="Done."),
    )  # fmt: skip
    web, web_token = await customer(client)
    await say(client, services, "gulmohar-lawn", web, "lawn rust", token=web_token)
    second, second_token = await customer(client)
    await say(client, services, "gulmohar-lawn", second, "salam", token=second_token)

    async with services.db.system() as session:
        tenant_id = await shop_id(session)
    async with services.db.tenant(tenant_id) as session:
        tenant = await session.get(Tenant, tenant_id)
        assert tenant is not None
        keep, browser_two = list(
            await session.scalars(select(Customer).order_by(Customer.created_at))
        )
        instagram = await channels.identify_sender(
            session, tenant_id, Channel.INSTAGRAM, "IGUSER9", services.keys, "Ayesha"
        )
        account = await session.scalar(
            select(ChannelAccount).where(ChannelAccount.channel == Channel.INSTAGRAM)
        )
        assert account is not None
        await channels.open_conversation(session, tenant_id, instagram.id, account.id)
        cart = await orders.open_cart(session, tenant, instagram.id)
        dupatta = await session.scalar(
            select(Variant.id).join(Product).where(Product.name == "Embroidered chiffon dupatta")
        )
        assert dupatta is not None
        await orders.set_quantity(session, cart, dupatta, 2)
        session.add_all(
            [
                UdhaarAccount(tenant_id=tenant_id, name="A", customer_id=keep.id),
                UdhaarAccount(tenant_id=tenant_id, name="B", customer_id=instagram.id),
            ]
        )
        await session.flush()
        moved = await identity.merge(session, keep, instagram)
        chats = list(
            await session.scalars(select(Conversation).where(Conversation.customer_id == keep.id))
        )
        lines = list(await session.scalars(select(OrderItem)))
        carts = await session.scalar(select(func.count(Order.id)))
        accounts = {a.name: a.customer_id for a in await session.scalars(select(UdhaarAccount))}
        known_on = await identity.channels_of(session, keep.id)
        code = await identity.start_link(
            session, tenant_id, browser_two.id, None, Channel.WEBCHAT, key="k", now=utcnow()
        )
        apart = await identity.redeem(session, tenant_id, keep.id, code, key="k", now=utcnow())
        with pytest.raises(AppError, match="same account"):
            await identity.merge(session, keep, browser_two)
    assert moved == ["chats"]  # the Instagram cart joined the web one; the khata stayed apart
    assert known_on == ["instagram", "webchat"]
    assert len(chats) == 2
    assert carts == 1
    assert sorted((line.name, line.quantity) for line in lines) == [
        ("Embroidered chiffon dupatta", 2),
        ("Printed lawn 3-piece", 1),
    ]
    assert accounts == {"A": keep.id, "B": None}
    assert apart.outcome is identity.Linked.TAKEN

    async with services.db.tenant(tenant_id) as session:
        with pytest.raises(AppError, match="two shops"):
            await identity.merge(session, keep, Customer(tenant_id=uuid.uuid4()))
