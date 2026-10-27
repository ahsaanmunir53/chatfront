"""Writes each model call to ai_calls, in its own transaction so failed work is still counted."""

from __future__ import annotations

from chatfront.ai.client import CallRecord, Meter
from chatfront.ai.models import AiCall
from chatfront.core.db import Database


def db_meter(db: Database) -> Meter:
    async def write(record: CallRecord) -> None:
        async with db.tenant(record.tenant_id) as session:
            session.add(
                AiCall(
                    tenant_id=record.tenant_id,
                    role=record.role,
                    provider=record.provider,
                    model=record.model,
                    prompt=record.prompt,
                    input_tokens=record.input_tokens,
                    output_tokens=record.output_tokens,
                    cost_millipaisa=record.cost_millipaisa,
                    latency_ms=record.latency_ms,
                    ok=record.ok,
                    error=record.error,
                    ref_type=record.ref[0] if record.ref else None,
                    ref_id=record.ref[1] if record.ref else None,
                )
            )

    return write
