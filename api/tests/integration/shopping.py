"""A customer shopping in the try-it chat with the order agent's recorded answers: one message
at a time, and placing the order with a tap."""

from __future__ import annotations

import json
from typing import Any

from sqlalchemy import select

from chatfront.ai.mock import MockProvider
from chatfront.orders.models import Order
from chatfront.services import Services
from tests.integration.chat import Client, agent, button, customer, say, use
from tests.integration.conftest import SHOP

ADDRESS = {
    "city": "Lahore",
    "address": "House 12, Street 4, Gulberg III",
    "name": "Ayesha",
    "phone": "03001234567",
}


def results(mock: MockProvider) -> list[dict[str, Any]]:
    """What the tools answered in the order agent's latest call."""
    asked = [r for r in mock.requests if (r.prompt or "").startswith("order_agent")]
    return [json.loads(m.text()) for m in asked[-1].messages if m.role == "tool"]


async def turn(
    client: Client,
    services: Services,
    guest: dict[str, str],
    words: str,
    *steps: dict[str, Any],
    reply: str = "Theek hai.",
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """One customer message: the recorded tool calls, one step each, then a reply. Returns
    the chat and what the tools answered."""
    fixtures = [agent(words, step=i, calls=calls) for i, calls in enumerate(steps)]
    mock = use(services, *fixtures, agent(words, step=len(steps), content=reply))
    messages = await say(client, services, SHOP, guest, words, token=guest["X-Chat-Token"])
    return messages, results(mock)


async def guest_of(client: Client) -> dict[str, str]:
    guest, _ = await customer(client)
    return guest


async def place(
    client: Client, services: Services, guest: dict[str, str], words: str = "order karo"
) -> Order:
    """Delivery details, the summary card, and the customer's tap on Place order."""
    messages, _ = await turn(
        client,
        services,
        guest,
        words,
        {"set_delivery_details": ADDRESS},
        {"checkout": {"payment_method": "cod"}},
    )
    card = next(m for m in reversed(messages) if m["payload"].get("buttons"))
    await say(client, services, SHOP, guest, token=guest["X-Chat-Token"], **button(card))
    async with services.db.system() as session:
        order = await session.scalar(
            select(Order)
            .where(Order.status != "cart")
            .order_by(Order.placed_at.desc().nulls_last())
            .limit(1)
        )
    assert order is not None
    return order
