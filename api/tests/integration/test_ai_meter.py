from sqlalchemy import select

from chatfront.ai.client import CallRecord
from chatfront.ai.meter import db_meter
from chatfront.ai.models import AiCall
from chatfront.core.db import Database
from tests.factories import make_tenant


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
        )
    )
    async with clean_db.tenant(tenant.id) as session:
        call = await session.scalar(select(AiCall))
    assert call is not None
    assert (call.cost_millipaisa, call.ref_type, call.ref_id) == (59_150, "order", "CF-1")
