import asyncio

from sqlalchemy import text

from chatfront.audit.service import SYSTEM, Actor, ActorType, record, verify_chain
from chatfront.core.db import Database
from tests.factories import make_tenant


async def test_chain_verifies_and_scrubs_personal_data(clean_db: Database) -> None:
    tenant = await make_tenant(clean_db)
    owner = Actor(ActorType.STAFF, "owner-1")
    async with clean_db.tenant(tenant.id) as session:
        first = await record(session, tenant.id, SYSTEM, "tenant.created")
        second = await record(
            session,
            tenant.id,
            owner,
            "order.note",
            entity=("order", "CF-123"),
            data={"note": "call 0300-1234567"},
        )
        check = await verify_chain(session, tenant.id)
    assert (first.seq, second.seq) == (1, 2)
    assert second.prev_hash == first.entry_hash
    assert second.data == {"note": "call [redacted]"}
    assert check.ok
    assert check.checked == 2


async def test_tampering_breaks_the_chain(clean_db: Database) -> None:
    tenant = await make_tenant(clean_db)
    async with clean_db.tenant(tenant.id) as session:
        for action in ("a", "b", "c"):
            await record(session, tenant.id, SYSTEM, action)
    async with clean_db.system() as session:  # only the owner role could ever do this
        await session.execute(text("UPDATE audit_log SET action = 'x' WHERE seq = 2"))
    async with clean_db.tenant(tenant.id) as session:
        check = await verify_chain(session, tenant.id)
    assert not check.ok
    assert check.broken_at_seq == 2
    assert check.checked == 1


async def test_concurrent_writers_do_not_fork_the_chain(clean_db: Database) -> None:
    tenant = await make_tenant(clean_db)

    async def write(n: int) -> None:
        async with clean_db.tenant(tenant.id) as session:
            await record(session, tenant.id, SYSTEM, f"event-{n}")

    await asyncio.gather(*(write(n) for n in range(6)))
    async with clean_db.tenant(tenant.id) as session:
        check = await verify_chain(session, tenant.id)
    assert check.ok
    assert check.checked == 6
