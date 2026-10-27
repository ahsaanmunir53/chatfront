"""Row-level security statements for migrations: one policy per tenant table.

Tenant work runs as APP_ROLE with app.tenant_id set (see core.db); APP_ROLE never owns a
table, so the policy always applies to it.
"""

from __future__ import annotations

from alembic import op

from chatfront.core.db import APP_ROLE

READ_WRITE = "SELECT, INSERT, UPDATE, DELETE"
APPEND_ONLY = "SELECT, INSERT"


def secure(table: str, privileges: str | None = None) -> None:
    privileges = privileges or READ_WRITE
    op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
    op.execute(
        f"CREATE POLICY tenant_isolation ON {table} "
        "USING (tenant_id = app_tenant()) WITH CHECK (tenant_id = app_tenant())"
    )
    op.execute(f"GRANT {privileges} ON {table} TO {APP_ROLE}")


def unsecure(table: str) -> None:
    op.execute(f"REVOKE ALL ON {table} FROM {APP_ROLE}")
    op.execute(f"DROP POLICY IF EXISTS tenant_isolation ON {table}")
