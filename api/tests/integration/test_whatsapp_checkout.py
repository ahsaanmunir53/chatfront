"""An order over WhatsApp, paid in advance by transfer, placed by the customer's tap."""

from __future__ import annotations

import json
from typing import Any

import httpx
import respx

from chatfront.services import Services
from tests.integration.chat import GRAPH, Client, agent, code_of, signed, use, wa_message


async def _send(client: Client, services: Services, payload: dict[str, Any]) -> list[Any]:
    body, headers = signed(payload)
    assert (await client.post("/webhooks/meta", content=body, headers=headers)).status_code == 200
    with respx.mock:
        ids = (httpx.Response(200, json={"messages": [{"id": f"wamid.out{n}"}]}) for n in range(99))
        send = respx.post(f"{GRAPH}/PN1/messages").mock(side_effect=lambda _: next(ids))
        assert services.worker is not None
        while await services.worker.run_once():
            pass
    return [json.loads(call.request.content) for call in send.calls]


async def test_a_transfer_order_is_placed_by_a_tap_on_whatsapp(
    app: tuple[Client, Services],
) -> None:
    client, services = app
    code = await code_of(client, "Khaddar 2-piece, stitched")
    use(
        services,
        agent("khaddar", calls={"add_to_cart": {"product_code": code, "options": {"size": "S"}}}),
        agent(
            "khaddar",
            step=1,
            calls={
                "set_delivery_details": {
                    "city": "lhr",
                    "address": "Street 2, Farooq Colony",
                    "name": "Ahsaan",
                }
            },
        ),
        agent("khaddar", step=2, calls={"checkout": {"payment_method": "transfer"}}),
        agent("khaddar", step=3, content="Card bhej diya, Order karein dabayein."),
    )
    sent = await _send(client, services, wa_message("wamid.1", body="khaddar S chahiye"))
    card = next(m for m in sent if m.get("type") == "interactive")
    yes = card["interactive"]["action"]["buttons"][0]["reply"]
    tap = wa_message("wamid.2", kind="interactive", type="button_reply", button_reply=yes)
    replies = await _send(client, services, tap)
    assert replies, "the tap got no answer"
    assert "ho gaya" in replies[0]["text"]["body"]


async def test_payment_details_from_the_environment_reach_the_shop(
    app: tuple[Client, Services],
) -> None:
    from sqlalchemy import select

    from chatfront.core.models import Tenant
    from chatfront.seed.__main__ import set_payment_details

    _, services = app
    done = await set_payment_details(services.db, "gulmohar-lawn=JazzCash 0300 0000000, Demo")
    assert done == ["gulmohar-lawn"]
    async with services.db.system() as session:
        shop = await session.scalar(select(Tenant).where(Tenant.slug == "gulmohar-lawn"))
    assert shop is not None
    assert shop.profile["payment"] == "JazzCash 0300 0000000, Demo"
    assert await set_payment_details(services.db, "gulmohar-lawn=JazzCash 0300 0000000, Demo") == []


async def test_the_same_price_asked_again_gets_the_same_counter(
    app: tuple[Client, Services],
) -> None:
    from sqlalchemy import select

    from chatfront.orders.models import PriceOffer

    client, services = app
    code = await code_of(client, "Printed lawn 3-piece")
    offer = {"make_offer": {"product_code": code, "price": 3500}}
    use(
        services,
        agent("3500 ka", calls=offer),
        agent("3500 ka", step=1, content="Maazrat, shop ki counter offer yeh hai."),
        agent("3500 hi", calls=offer),
        agent("3500 hi", step=1, content="Wohi counter offer hai."),
    )
    await _send(client, services, wa_message("wamid.o1", body="3500 ka de dein"))
    await _send(client, services, wa_message("wamid.o2", body="3500 hi kar dein"))
    async with services.db.system() as session:
        rounds = list(await session.scalars(select(PriceOffer)))
    assert len(rounds) == 1  # no second, lower step for the same number
