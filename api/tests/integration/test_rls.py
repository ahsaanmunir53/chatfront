"""Row-level security: a tenant transaction can only see and write its own rows."""

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.exc import DBAPIError

from chatfront.audit.models import AuditEntry
from chatfront.core.clock import utcnow
from chatfront.core.db import APP_ROLE, Database
from chatfront.core.models import Customer, Tenant
from chatfront.jobs.models import Job
from tests.factories import make_customer, make_tenant


async def test_tenant_sees_only_its_own_rows(clean_db: Database) -> None:
    a = await make_tenant(clean_db, "shop-a")
    b = await make_tenant(clean_db, "shop-b")
    await make_customer(clean_db, a.id, "Ayesha")
    await make_customer(clean_db, b.id, "Bilal")

    async with clean_db.tenant(a.id) as session:
        names = (await session.scalars(select(Customer.display_name))).all()
        tenants = (await session.scalars(select(Tenant.slug))).all()
    assert names == ["Ayesha"]
    assert tenants == ["shop-a"]


async def test_cannot_write_into_another_tenant(clean_db: Database) -> None:
    a = await make_tenant(clean_db, "shop-a")
    b = await make_tenant(clean_db, "shop-b")
    with pytest.raises(DBAPIError, match="row-level security"):
        async with clean_db.tenant(a.id) as session:
            session.add(Customer(tenant_id=b.id, display_name="Intruder"))


async def test_app_role_without_tenant_sees_nothing(clean_db: Database) -> None:
    a = await make_tenant(clean_db, "shop-a")
    await make_customer(clean_db, a.id)
    async with clean_db.sessions() as session, session.begin():
        await session.execute(text(f"SET LOCAL ROLE {APP_ROLE}"))
        count = await session.scalar(select(func.count()).select_from(Customer))
    assert count == 0


async def test_audit_log_is_append_only_for_the_app(clean_db: Database) -> None:
    a = await make_tenant(clean_db, "shop-a")
    async with clean_db.tenant(a.id) as session:
        session.add(
            AuditEntry(
                tenant_id=a.id,
                seq=1,
                actor_type="system",
                action="test",
                prev_hash="0" * 64,
                entry_hash="1" * 64,
                created_at=utcnow(),
            )
        )
    with pytest.raises(DBAPIError, match="permission denied"):
        async with clean_db.tenant(a.id) as session:
            await session.execute(text("DELETE FROM audit_log"))


async def test_jobs_only_for_own_tenant(clean_db: Database) -> None:
    a = await make_tenant(clean_db, "shop-a")
    b = await make_tenant(clean_db, "shop-b")
    async with clean_db.tenant(a.id) as session:
        session.add(Job(tenant_id=a.id, kind="noop"))
    with pytest.raises(DBAPIError, match="row-level security"):
        async with clean_db.tenant(a.id) as session:
            session.add(Job(tenant_id=b.id, kind="noop"))


async def test_every_table_with_a_tenant_has_row_level_security(db: Database) -> None:
    async with db.system() as session:
        rows = await session.execute(
            text(
                """SELECT c.relname, c.relrowsecurity, EXISTS (
                       SELECT 1 FROM pg_policies p
                       WHERE p.schemaname = col.table_schema
                         AND p.tablename = c.relname AND p.policyname = 'tenant_isolation')
                   FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
                   JOIN information_schema.columns col
                     ON col.table_schema = n.nspname AND col.table_name = c.relname
                    AND col.column_name = 'tenant_id'
                   WHERE c.relkind = 'r' AND n.nspname = 'public'"""
            )
        )
        tables = {name: (enabled, policy) for name, enabled, policy in rows}
    assert {
        "products",
        "variants",
        "orders",
        "order_items",
        "stock_movements",
        "branches",
        "wholesale_listings",
        "b2b_orders",
        "a2a_buyer_policies",
        "a2a_rounds",
        "marketplace_payments",
    } <= set(tables)
    assert {name for name, (on, policy) in tables.items() if not (on and policy)} == set()


async def test_stock_movements_are_append_only_for_the_app(clean_db: Database) -> None:
    async with clean_db.system() as session:
        grants = await session.scalars(
            text(
                "SELECT privilege_type FROM information_schema.role_table_grants "
                "WHERE table_name = 'stock_movements' AND grantee = :role"
            ),
            {"role": APP_ROLE},
        )
        assert set(grants) == {"SELECT", "INSERT"}
