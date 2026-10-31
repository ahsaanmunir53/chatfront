"""One shop can never reach another shop's rows through the owner console (security point 6,
broken object level authorisation): every route that takes another shop's id answers 404,
and nothing changes in that shop."""

from __future__ import annotations

from sqlalchemy import select

from chatfront.channels.models import ChannelAccount, Conversation
from chatfront.core.models import Customer
from chatfront.services import Services
from chatfront.stock.models import StockBatch
from tests.integration.chat import Client, owner_auth, shop_id

OTHER = "noor-salon"


async def test_another_shops_ids_are_not_found(app: tuple[Client, Services]) -> None:
    client, services = app
    mine = await owner_auth(services)  # gulmohar-lawn
    async with services.db.system() as session:
        other = await shop_id(session, OTHER)
        account = await session.scalar(
            select(ChannelAccount).where(ChannelAccount.tenant_id == other).limit(1)
        )
        assert account is not None
        person = Customer(tenant_id=other, display_name="Someone at the salon")
        session.add(person)
        await session.flush()
        chat = Conversation(tenant_id=other, customer_id=person.id, channel_account_id=account.id)
        session.add(chat)
        await session.flush()
        batch = await session.scalar(
            select(StockBatch).where(StockBatch.tenant_id == other).limit(1)
        )
        customer_id, chat_id = person.id, chat.id
        batch_id = batch.id if batch else None

    calls = [
        ("GET", f"/api/owner/customers/{customer_id}/data", None),
        ("POST", f"/api/owner/customers/{customer_id}/erase", None),
        ("PUT", f"/api/owner/customers/{customer_id}/verified", {"verified": True}),
        ("GET", f"/api/owner/conversations/{chat_id}/messages", None),
        ("POST", f"/api/owner/inbox/{chat_id}/take", None),
    ]
    if batch_id is not None:
        calls.append(("POST", f"/api/owner/batches/{batch_id}/discard", {"reason": "lost"}))
    for method, path, body in calls:
        response = await client.request(method, path, json=body, headers=mine)
        assert response.status_code == 404, (method, path, response.status_code, response.text)

    async with services.db.system() as session:
        still = await session.get(Customer, customer_id)
        assert still is not None
        assert (still.display_name, still.verified_buyer) == ("Someone at the salon", False)
        if batch_id is not None:
            kept = await session.get(StockBatch, batch_id)
            assert kept is not None
            assert kept.status != "discarded"
