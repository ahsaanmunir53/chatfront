"""What each staff role may do (feature 42). The owner may do everything; the others only
what their work needs, and the console and the owner's chat check the same table."""

from __future__ import annotations

import uuid
from enum import StrEnum

from chatfront.core.db import Database
from chatfront.core.models import Staff, StaffRole


class Can(StrEnum):
    CATALOG = "catalog"  # products, prices, shop policies, stock limits
    STOCK = "stock"  # restock, batches, expiry and write-offs
    ORDERS = "orders"  # see orders and customers
    SHIP = "ship"  # pack, dispatch, deliver, return; shipping labels
    PAYMENTS = "payments"  # approve or reject payments
    BOOKS = "books"  # see the books and the khata
    BOOKS_WRITE = "books_write"  # add expenses and udhaar
    CHATS = "chats"  # answer chats handed to a person
    TRUST = "trust"  # verified buyers, discounts the owner is asked about
    TEAM = "team"  # add and remove staff
    SETTINGS = "settings"  # autonomy levels, SMS forwarding
    AUDIT = "audit"  # the activity log


ROLE_RIGHTS: dict[str, frozenset[Can]] = {
    StaffRole.OWNER: frozenset(Can),
    StaffRole.SALES: frozenset({Can.ORDERS, Can.CHATS}),
    StaffRole.PACKING: frozenset({Can.ORDERS, Can.SHIP}),
    StaffRole.ACCOUNTS: frozenset({Can.ORDERS, Can.PAYMENTS, Can.BOOKS, Can.BOOKS_WRITE}),
    StaffRole.AUDITOR: frozenset({Can.ORDERS, Can.BOOKS, Can.AUDIT}),
    StaffRole.STOCK: frozenset({Can.ORDERS, Can.CATALOG, Can.STOCK}),
}


def rights(role: str) -> frozenset[Can]:
    return ROLE_RIGHTS.get(role, frozenset())


async def staff_rights(
    db: Database, tenant_id: uuid.UUID, staff_id: uuid.UUID | None
) -> frozenset[Can]:
    """An active staff member's rights; nothing for anyone else."""
    if staff_id is None:
        return frozenset()
    async with db.tenant(tenant_id) as session:
        staff = await session.get(Staff, staff_id)
    return rights(staff.role) if staff is not None and staff.active else frozenset()
