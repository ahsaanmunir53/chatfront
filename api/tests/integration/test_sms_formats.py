"""Wallet SMS formats on real PostgreSQL (feature 73): a message no parser read is dropped
unless the shop agreed to share its shape; Chatfront's staff see the shapes next to every
parser and mark them handled; saying no again deletes them."""

from __future__ import annotations

from sqlalchemy import func, select

from chatfront.payments.models import SmsSample, WalletCredit
from chatfront.services import Services
from tests.integration.chat import Client, owner_auth
from tests.integration.money import forward, jazzcash, set_forwarding
from tests.integration.test_shield import PLATFORM

NEW_FORMAT = {"from": "JazzCash", "text": "Mubarak! Rs 1,200 aapke account mein Sara Khan se aaye."}
FROM_A_PERSON = {"from": "03001112222", "text": "Rs 500 bhej do please"}


async def kept(services: Services) -> list[SmsSample]:
    async with services.db.system() as session:
        return list(await session.scalars(select(SmsSample)))


async def test_shapes_are_kept_only_when_the_shop_agrees(app: tuple[Client, Services]) -> None:
    client, services = app
    owner = await owner_auth(services)
    await set_forwarding(services)

    assert await forward(client, NEW_FORMAT) == "ignored"
    assert await kept(services) == []

    agreed = await client.put("/api/owner/sms-samples", json={"share": True}, headers=owner)
    assert agreed.json() == {"share": True}
    assert await forward(client, NEW_FORMAT) == "sampled"
    assert await forward(client, NEW_FORMAT | {"text": NEW_FORMAT["text"] + " "}) == "sampled"
    assert await forward(client, FROM_A_PERSON) == "ignored"
    assert await forward(client, jazzcash("4,850")) == "kept"  # a format a parser reads
    [sample] = await kept(services)
    assert (sample.sender, sample.seen) == ("JazzCash", 2)
    assert sample.text == "Mubarak! Rs 9,999 aapke account mein NAME se aaye."

    payments = (await client.get("/api/owner/payments", headers=owner)).json()
    assert (payments["share_unread"], payments["unread_kept"]) == (True, 1)
    jazz = next(w for w in payments["wallets"] if w["name"] == "JazzCash")
    assert (jazz["read"], jazz["checked"]) == (True, False)

    # Chatfront's own staff, and nobody else.
    assert (await client.get("/api/platform/sms-formats", headers=owner)).status_code == 403
    team = await owner_auth(services, PLATFORM)
    formats = (await client.get("/api/platform/sms-formats", headers=team)).json()
    parser = next(p for p in formats["parsers"] if p["parser"] == "pk.jazzcash")
    async with services.db.system() as session:
        read = await session.scalar(
            select(func.count())
            .select_from(WalletCredit)
            .where(WalletCredit.parser == "pk.jazzcash")
        )
    assert (parser["credits"], parser["verified"]) == (read, False)
    assert parser["last_seen"] is not None
    [unread] = formats["unread"]
    assert (unread["country"], unread["seen"], unread["text"]) == ("PK", 2, sample.text)
    assert "Sara" not in str(formats)

    done = await client.post(f"/api/platform/sms-formats/{unread['id']}/handled", headers=team)
    assert done.json() == {"handled": True}
    again = await client.post(f"/api/platform/sms-formats/{unread['id']}/handled", headers=team)
    assert again.status_code == 404
    assert (await client.get("/api/platform/sms-formats", headers=team)).json()["unread"] == []
    assert await forward(client, NEW_FORMAT) == "sampled"  # still unread: it comes back
    [back] = (await client.get("/api/platform/sms-formats", headers=team)).json()["unread"]
    assert back["seen"] == 3

    await client.put("/api/owner/sms-samples", json={"share": False}, headers=owner)
    assert await kept(services) == []
    assert await forward(client, NEW_FORMAT) == "ignored"
