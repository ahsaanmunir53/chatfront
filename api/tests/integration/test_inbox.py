"""The unified inbox (feature 76): every customer chat in one list, taken by a person, given
to someone else by the owner, and given back to the assistant."""

from __future__ import annotations

from datetime import timedelta
from typing import Any

import httpx
import respx
from sqlalchemy import select

from chatfront.channels.models import Channel, Conversation, CustomerIdentity
from chatfront.core.clock import utcnow
from chatfront.core.secretbox import seal
from chatfront.services import Services
from tests.integration.chat import (
    GRAPH,
    Client,
    customer,
    owner_auth,
    say,
    signed,
    wa_message,
)
from tests.integration.conftest import SHOP


async def _inbox(client: Client, auth: dict[str, str], query: str = "") -> dict[str, Any]:
    response = await client.get(f"/api/owner/inbox{query}", headers=auth)
    assert response.status_code == 200, response.text
    return dict(response.json())


async def _staff(client: Client, owner: dict[str, str], name: str, role: str) -> dict[str, str]:
    made = await client.post("/api/owner/team", json={"name": name, "role": role}, headers=owner)
    return {"Authorization": f"Bearer {made.json()['link'].split('#t=')[1]}"}


async def test_every_chat_in_one_list_taken_given_and_given_back(
    app: tuple[Client, Services],
) -> None:
    client, services = app
    owner = await owner_auth(services)
    ali = await _staff(client, owner, "Ali", "sales")
    sana = await _staff(client, owner, "Sana", "accounts")

    # A WhatsApp customer, then a web chat customer: both answered by the assistant.
    body, headers = signed(wa_message("w1", body="salam, lawn suit hai?"))
    await client.post("/webhooks/meta", content=body, headers=headers)
    with respx.mock:
        respx.post(f"{GRAPH}/PN1/messages").mock(
            return_value=httpx.Response(200, json={"messages": [{"id": "wamid.1"}]})
        )
        assert services.worker is not None
        while await services.worker.run_once():
            pass
    guest, token = await customer(client)
    await say(client, services, SHOP, guest, "salam", token=token)

    inbox = await _inbox(client, owner)
    assert [item["channel"] for item in inbox["items"]] == ["webchat", "whatsapp"]  # newest first
    assert {item["state"] for item in inbox["items"]} == {"assistant"}
    assert all(item["last"]["by"] == "agent" and item["window_open"] for item in inbox["items"])
    people = {p["name"] for p in inbox["people"]}
    assert "Ali" in people
    assert "Sana" not in people  # accounts does not answer chats
    assert inbox["can_assign"]
    web_chat, whatsapp = (item["conversation_id"] for item in inbox["items"])

    # Ali takes the web chat: the assistant stops answering it.
    taken = (await client.post(f"/api/owner/inbox/{web_chat}/take", headers=ali)).json()
    assert (taken["state"], taken["assigned"]["name"]) == ("person", "Ali")
    quiet = await say(client, services, SHOP, guest, "koi hai?", token=token)
    assert quiet[-1]["direction"] == "in"
    mine = await _inbox(client, ali, "?show=mine")
    assert [item["conversation_id"] for item in mine["items"]] == [web_chat]
    assert mine["items"][0]["last"]["by"] == "customer"
    assert not mine["can_assign"]

    # Only the owner gives chats to others, and only to people who answer chats.
    forbidden = await client.post(
        f"/api/owner/inbox/{whatsapp}/assign", json={"staff_id": None}, headers=ali
    )
    assert forbidden.status_code == 403
    sana_id = next(m["id"] for m in (await client.get("/api/owner/team", headers=owner)).json()
                   if m["name"] == "Sana")  # fmt: skip
    refused = await client.post(
        f"/api/owner/inbox/{whatsapp}/assign", json={"staff_id": sana_id}, headers=owner
    )
    assert refused.json()["error"]["code"] == "cannot_answer_chats"
    ali_id = taken["assigned"]["id"]
    given = await client.post(
        f"/api/owner/inbox/{whatsapp}/assign", json={"staff_id": ali_id}, headers=owner
    )
    assert (given.json()["state"], given.json()["assigned"]["name"]) == ("person", "Ali")

    # Back to waiting, then back to the assistant.
    unassigned = await client.post(
        f"/api/owner/inbox/{whatsapp}/assign", json={"staff_id": None}, headers=owner
    )
    assert unassigned.json()["state"] == "waiting"
    waiting = await _inbox(client, owner, "?show=waiting")
    assert waiting["waiting"] == 1
    assert [item["conversation_id"] for item in waiting["items"]] == [whatsapp]
    handover_id = waiting["items"][0]["handover_id"]
    with respx.mock:
        respx.post(f"{GRAPH}/PN1/messages").mock(
            return_value=httpx.Response(200, json={"messages": [{"id": "wamid.2"}]})
        )
        await client.post(f"/api/owner/handovers/{handover_id}/resume", headers=owner)
    back = await _inbox(client, owner, "?channel=whatsapp")
    assert [(i["state"], i["assigned"]) for i in back["items"]] == [("assistant", None)]
    assert (await client.get("/api/owner/inbox", headers=sana)).status_code == 403


async def test_closed_windows_and_other_channels_show(app: tuple[Client, Services]) -> None:
    client, services = app
    owner = await owner_auth(services)
    body, headers = signed(wa_message("w1", body="salam"))
    await client.post("/webhooks/meta", content=body, headers=headers)
    async with services.db.system() as session:
        chat = await session.scalar(select(Conversation))
        assert chat is not None
        chat.last_inbound_at = utcnow() - timedelta(days=2)
        session.add(
            CustomerIdentity(
                tenant_id=chat.tenant_id,
                customer_id=chat.customer_id,
                channel=Channel.INSTAGRAM,
                external_hash="ig-hash",
                external_sealed=seal("IGUSER", services.keys.secret_key),
            )
        )
    item = (await _inbox(client, owner))["items"][0]
    assert item["window_open"] is False
    assert item["known_on"] == ["instagram", "whatsapp"]
    assert item["last"]["by"] == "customer"  # no reply yet: the queue has not run
    missing = await client.post(
        "/api/owner/inbox/01900000-0000-7000-8000-000000000000/take", headers=owner
    )
    assert missing.status_code == 404
