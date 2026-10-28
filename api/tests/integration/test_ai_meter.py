import uuid
from datetime import datetime, timedelta

from sqlalchemy import select

from chatfront.ai import meter
from chatfront.ai.client import CallRecord
from chatfront.ai.meter import db_meter
from chatfront.ai.models import AiCall
from chatfront.channels.models import ChannelAccount, Conversation
from chatfront.core.clock import utcnow
from chatfront.core.db import Database
from chatfront.orders.models import Order
from tests.factories import make_customer, make_tenant


async def test_meter_writes_one_row_per_call(clean_db: Database) -> None:
    tenant = await make_tenant(clean_db)
    meter = db_meter(clean_db)
    await meter(
        CallRecord(
            tenant_id=tenant.id,
            role="chat",
            provider="baseten",
            model="m",
            prompt="p@v1",
            input_tokens=1000,
            output_tokens=200,
            cost_millipaisa=59_150,
            latency_ms=900,
            ok=True,
            error=None,
            ref=("order", "CF-1"),
            effort="xhigh",
        )
    )
    async with clean_db.tenant(tenant.id) as session:
        call = await session.scalar(select(AiCall))
    assert call is not None
    assert (call.cost_millipaisa, call.ref_type, call.ref_id) == (59_150, "order", "CF-1")
    assert call.effort == "xhigh"


async def _chat(db: Database, tenant_id: uuid.UUID, customer_id: uuid.UUID) -> uuid.UUID:
    async with db.tenant(tenant_id) as session:
        account = ChannelAccount(tenant_id=tenant_id, channel="webchat", external_id="web")
        session.add(account)
        await session.flush()
        chat = Conversation(
            tenant_id=tenant_id, customer_id=customer_id, channel_account_id=account.id
        )
        session.add(chat)
        await session.flush()
        return chat.id


async def _call(
    db: Database,
    tenant_id: uuid.UUID,
    ref: tuple[str, str],
    at: datetime,
    cost: int,
    *,
    provider: str = "baseten",
    ok: bool = True,
) -> None:
    async with db.tenant(tenant_id) as session:
        session.add(
            AiCall(
                tenant_id=tenant_id,
                role="chat",
                provider=provider,
                ok=ok,
                model="m",
                prompt="order_agent@v7",
                input_tokens=100,
                output_tokens=20,
                cost_millipaisa=cost,
                ref_type=ref[0],
                ref_id=ref[1],
                effort="low",
                created_at=at,
            )
        )


async def test_an_order_costs_its_chat_since_the_last_order_and_its_own_calls(
    clean_db: Database,
) -> None:
    tenant = await make_tenant(clean_db)
    customer = await make_customer(clean_db, tenant.id)
    chat = await _chat(clean_db, tenant.id, customer.id)
    start = utcnow() - timedelta(hours=5)
    async with clean_db.tenant(tenant.id) as session:
        first = Order(
            tenant_id=tenant.id,
            code="A1",
            customer_id=customer.id,
            conversation_id=chat,
            status="placed",
            currency="PKR",
            placed_at=start + timedelta(hours=1),
        )
        second = Order(
            tenant_id=tenant.id,
            code="B2",
            customer_id=customer.id,
            conversation_id=chat,
            status="awaiting_payment",
            currency="PKR",
            placed_at=start + timedelta(hours=3),
        )
        session.add_all([first, second])
    talk = ("conversation", str(chat))
    await _call(clean_db, tenant.id, talk, start + timedelta(minutes=10), 1_000)  # first
    await _call(clean_db, tenant.id, talk, start + timedelta(hours=2), 2_000)  # second
    await _call(clean_db, tenant.id, ("order", str(second.id)), start + timedelta(hours=4), 5_000)
    await _call(clean_db, tenant.id, talk, start + timedelta(hours=4), 7_000)  # the next cart
    async with clean_db.tenant(tenant.id) as session:
        orders = list(await session.scalars(select(Order).order_by(Order.placed_at)))
        used = await meter.per_order(session, orders)
        total = await meter.in_period(session, start, utcnow())
        jobs = await meter.by_job(session, start, utcnow())
        efforts = await meter.by_effort(session, start, utcnow())
    assert used[orders[0].id].cost_millipaisa == 1_000
    assert (used[orders[1].id].calls, used[orders[1].id].cost_millipaisa) == (2, 7_000)
    assert (total.calls, total.tokens, total.cost_millipaisa) == (4, 480, 15_000)
    assert list(jobs) == ["order_agent"]
    assert efforts == {"low": 4}


async def test_the_meter_says_which_endpoint_answered(clean_db: Database) -> None:
    tenant = await make_tenant(clean_db)
    start = utcnow() - timedelta(hours=1)
    talk = ("conversation", str(uuid.uuid4()))
    for provider, ok in [("baseten", False), ("fireworks", True), ("baseten", True)] + [
        ("modal", True)
    ] * 2:
        await _call(clean_db, tenant.id, talk, start, 0, provider=provider, ok=ok)
    async with clean_db.tenant(tenant.id) as session:
        served = await meter.by_endpoint(session, start - timedelta(minutes=1), utcnow())
    assert served == {
        "modal": meter.Served(2, 0),
        "baseten": meter.Served(1, 1),
        "fireworks": meter.Served(1, 0),
    }
