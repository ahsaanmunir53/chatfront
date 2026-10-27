"""Hash-chained audit log: each entry's hash covers the previous one, so an edit breaks the chain.

The app role can only insert and read audit rows (see migration 0001), and entries are
chained per tenant under an advisory lock, so concurrent writers cannot fork the chain.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from chatfront.audit.models import AuditEntry
from chatfront.core.clock import utcnow
from chatfront.core.logging import redact

GENESIS_HASH = "0" * 64


class ActorType(StrEnum):
    STAFF = "staff"
    AGENT = "agent"
    SYSTEM = "system"
    CUSTOMER = "customer"


@dataclass(frozen=True)
class Actor:
    type: ActorType
    id: str | None = None


SYSTEM = Actor(ActorType.SYSTEM)
AGENT = Actor(ActorType.AGENT, "order-agent")


def _scrub(value: Any) -> Any:
    if isinstance(value, str):
        return redact(value)
    if isinstance(value, dict):
        return {str(k): _scrub(v) for k, v in value.items()}
    if isinstance(value, list | tuple):
        return [_scrub(v) for v in value]
    return value


def compute_hash(prev_hash: str, fields: dict[str, Any]) -> str:
    canonical = json.dumps(fields, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256((prev_hash + canonical).encode()).hexdigest()


def _fields(entry: AuditEntry) -> dict[str, Any]:
    return {
        "tenant_id": str(entry.tenant_id),
        "seq": entry.seq,
        "actor_type": entry.actor_type,
        "actor_id": entry.actor_id,
        "action": entry.action,
        "entity_type": entry.entity_type,
        "entity_id": entry.entity_id,
        "data": entry.data,
        "created_at": entry.created_at.isoformat(),
    }


def _lock_key(tenant_id: uuid.UUID) -> int:
    return int.from_bytes(tenant_id.bytes[:8], "big", signed=True)


async def record(
    session: AsyncSession,
    tenant_id: uuid.UUID,
    actor: Actor,
    action: str,
    *,
    entity: tuple[str, str] | None = None,
    data: dict[str, Any] | None = None,
    at: datetime | None = None,
) -> AuditEntry:
    """Append one entry to the tenant's chain inside the caller's transaction."""
    await session.execute(select(func.pg_advisory_xact_lock(_lock_key(tenant_id))))
    last = (
        await session.execute(
            select(AuditEntry.seq, AuditEntry.entry_hash)
            .where(AuditEntry.tenant_id == tenant_id)
            .order_by(AuditEntry.seq.desc())
            .limit(1)
        )
    ).first()
    seq, prev_hash = (last[0] + 1, last[1]) if last else (1, GENESIS_HASH)
    entry = AuditEntry(
        tenant_id=tenant_id,
        seq=seq,
        actor_type=actor.type,
        actor_id=actor.id,
        action=action,
        entity_type=entity[0] if entity else None,
        entity_id=entity[1] if entity else None,
        data=_scrub(data or {}),
        prev_hash=prev_hash,
        created_at=at or utcnow(),
    )
    entry.entry_hash = compute_hash(prev_hash, _fields(entry))
    session.add(entry)
    await session.flush()
    return entry


@dataclass(frozen=True)
class ChainCheck:
    ok: bool
    checked: int
    broken_at_seq: int | None = None


async def verify_chain(session: AsyncSession, tenant_id: uuid.UUID) -> ChainCheck:
    """Recompute every hash in order; report the first entry that does not match."""
    rows = await session.scalars(
        select(AuditEntry).where(AuditEntry.tenant_id == tenant_id).order_by(AuditEntry.seq)
    )
    prev_hash = GENESIS_HASH
    expected_seq = 1
    checked = 0
    for entry in rows:
        intact = (
            entry.seq == expected_seq
            and entry.prev_hash == prev_hash
            and entry.entry_hash == compute_hash(prev_hash, _fields(entry))
        )
        if not intact:
            return ChainCheck(ok=False, checked=checked, broken_at_seq=entry.seq)
        prev_hash = entry.entry_hash
        expected_seq += 1
        checked += 1
    return ChainCheck(ok=True, checked=checked)
