"""The udhaar khata (feature 49): what each person owes the shop, written by code.

Udhaar is given when the owner says so (by chat, voice or the console) or when a verified
buyer orders on the khata at a shop that gives credit; it is paid back the same ways. Every
change is a ledger entry and the account's balance moves with it. Reminders grow firmer the
longer a balance stays open; see books.reminders.
"""

from __future__ import annotations

import unicodedata

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from chatfront.audit.service import Actor
from chatfront.books import ledger
from chatfront.books.models import Category, EntryKind, UdhaarAccount
from chatfront.core.clock import utcnow
from chatfront.core.errors import AppError
from chatfront.core.models import Customer, Tenant
from chatfront.orders.models import Order


def _key(name: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", name).casefold().split())


async def find(session: AsyncSession, name: str) -> list[UdhaarAccount]:
    """Accounts whose name is the one given, or starts with it ("Bilal" finds "Bilal Ahmed")."""
    wanted = _key(name)
    if not wanted:
        return []
    accounts = list(await session.scalars(select(UdhaarAccount).order_by(UdhaarAccount.created_at)))
    exact = [a for a in accounts if _key(a.name) == wanted]
    return exact or [a for a in accounts if _key(a.name).startswith(wanted)]


async def for_customer(session: AsyncSession, tenant: Tenant, customer: Customer) -> UdhaarAccount:
    account = await session.scalar(
        select(UdhaarAccount).where(UdhaarAccount.customer_id == customer.id)
    )
    if account is None:
        account = UdhaarAccount(
            tenant_id=tenant.id,
            name=(customer.display_name or "Customer")[:120],
            customer_id=customer.id,
            balance=0,
            stage=0,
        )
        session.add(account)
        await session.flush()
    return account


async def open_account(session: AsyncSession, tenant: Tenant, name: str) -> UdhaarAccount:
    clean = " ".join(name.split())[:120]
    if not clean:
        raise AppError("no_name", "An udhaar account needs a name")
    account = UdhaarAccount(tenant_id=tenant.id, name=clean, balance=0, stage=0)
    session.add(account)
    await session.flush()
    return account


async def give(
    session: AsyncSession,
    tenant: Tenant,
    account: UdhaarAccount,
    amount: int,
    actor: Actor,
    *,
    source: str,
    note: str = "",
    order: Order | None = None,
) -> None:
    """Goods given on credit: the balance goes up."""
    await ledger.post(
        session,
        tenant,
        EntryKind.UDHAAR_GIVEN,
        amount,
        actor,
        category=Category.SALES,
        source=source,
        description=note or (f"Order {order.code}" if order else account.name),
        order_id=order.id if order else None,
        account_id=account.id,
    )
    if account.balance <= 0:
        account.owed_since, account.stage, account.reminded_at = utcnow(), 0, None
    account.balance += amount
    await session.flush()


async def receive(
    session: AsyncSession,
    tenant: Tenant,
    account: UdhaarAccount,
    amount: int,
    actor: Actor,
    *,
    source: str,
    note: str = "",
) -> None:
    """Credit paid back: the balance goes down, never below nothing."""
    if amount > account.balance:
        raise AppError("more_than_owed", "That is more than this account owes", status_code=409)
    await ledger.post(
        session,
        tenant,
        EntryKind.UDHAAR_PAID,
        amount,
        actor,
        category=Category.SALES,
        source=source,
        description=note or account.name,
        account_id=account.id,
    )
    account.balance -= amount
    if account.balance == 0:
        account.owed_since, account.stage, account.reminded_at = None, 0, None
    await session.flush()


async def on_credit(session: AsyncSession, tenant: Tenant, order: Order, actor: Actor) -> None:
    """A verified buyer's order on the khata: its total goes on their account."""
    customer = await session.get(Customer, order.customer_id)
    if customer is None:  # pragma: no cover - the foreign key forbids it
        raise AppError("no_customer", "The order has no customer")
    account = await for_customer(session, tenant, customer)
    await give(session, tenant, account, order.total, actor, source="order", order=order)
