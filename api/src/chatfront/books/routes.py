"""The books in the console (features 48, 49, 50): today's, this week's and this month's money,
the latest entries with Undo, an expense form, and the udhaar khata.

Amounts travel in minor units (paisa), as everywhere in the API; the console formats them.
"""

from __future__ import annotations

import uuid
from datetime import date
from typing import Annotated

from fastapi import APIRouter
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from chatfront.audit.service import Actor, ActorType
from chatfront.books import ledger, udhaar
from chatfront.books.ledger import MAX_AMOUNT, today
from chatfront.books.models import Category, EntryKind, LedgerEntry, UdhaarAccount
from chatfront.books.reports import Totals, totals, week_start
from chatfront.core.clock import utcnow
from chatfront.core.errors import AppError, NotFound
from chatfront.core.models import Customer, Tenant
from chatfront.orders.models import Order
from chatfront.owner.rights import Can
from chatfront.owner.routes import ServicesDep, needs
from chatfront.owner.sessions import OwnerSession

router = APIRouter(prefix="/api/owner")
ReadDep = Annotated[OwnerSession, needs(Can.BOOKS)]
WriteDep = Annotated[OwnerSession, needs(Can.BOOKS_WRITE)]

ENTRY_LIMIT = 50
SPENDING = tuple(c for c in Category if c != Category.SALES)


class Sums(BaseModel):
    sales: int
    udhaar_given: int
    udhaar_paid: int
    expenses: int
    orders: int
    cash_profit: int


class EntryOut(BaseModel):
    id: uuid.UUID
    kind: str
    category: str
    amount: int
    description: str
    occurred_on: date
    source: str
    order_code: str | None
    account: str | None
    reverses: uuid.UUID | None
    reversed: bool
    has_bill: bool


class AccountOut(BaseModel):
    id: uuid.UUID
    name: str
    balance: int
    owed_days: int | None
    reminders_sent: int
    customer_id: uuid.UUID | None


class AccountEntry(BaseModel):
    kind: str
    amount: int
    description: str
    occurred_on: date


class AccountDetail(BaseModel):
    account: AccountOut
    entries: list[AccountEntry]


class Books(BaseModel):
    today: Sums
    week: Sums
    month: Sums
    entries: list[EntryOut]
    accounts: list[AccountOut]
    categories: list[str]


class NewExpense(BaseModel):
    amount: int = Field(gt=0, le=MAX_AMOUNT)
    category: Category
    description: str = Field(default="", max_length=200)
    occurred_on: date | None = None


class UdhaarMove(BaseModel):
    kind: EntryKind
    amount: int = Field(gt=0, le=MAX_AMOUNT)
    account_id: uuid.UUID | None = None  # an existing account
    name: str | None = Field(default=None, max_length=120)  # or a new one
    customer_id: uuid.UUID | None = None  # linked, so reminders can reach them
    note: str = Field(default="", max_length=200)


def _sums(found: Totals) -> Sums:
    return Sums(
        sales=found.sales,
        udhaar_given=found.udhaar_given,
        udhaar_paid=found.udhaar_paid,
        expenses=found.expenses,
        orders=found.orders,
        cash_profit=found.cash_profit,
    )


def _account(account: UdhaarAccount) -> AccountOut:
    days = (utcnow() - account.owed_since).days if account.owed_since else None
    return AccountOut(
        id=account.id,
        name=account.name,
        balance=account.balance,
        owed_days=days,
        reminders_sent=account.stage,
        customer_id=account.customer_id,
    )


def _actor(session: OwnerSession) -> Actor:
    return Actor(ActorType.STAFF, str(session.staff_id))


@router.get("/books")
async def books(session: ReadDep, services: ServicesDep) -> Books:
    async with services.db.tenant(session.tenant_id) as db:
        tenant = await db.get(Tenant, session.tenant_id)
        assert tenant is not None
        day = today(tenant)
        rows = (
            await db.execute(
                select(LedgerEntry, Order.code, UdhaarAccount.name)
                .outerjoin(Order, Order.id == LedgerEntry.order_id)
                .outerjoin(UdhaarAccount, UdhaarAccount.id == LedgerEntry.account_id)
                .order_by(LedgerEntry.created_at.desc())
                .limit(ENTRY_LIMIT)
            )
        ).all()
        undone = set(
            await db.scalars(select(LedgerEntry.reverses).where(LedgerEntry.reverses.is_not(None)))
        )
        accounts = await db.scalars(
            select(UdhaarAccount)
            .where(UdhaarAccount.balance > 0)
            .order_by(UdhaarAccount.balance.desc())
        )
        return Books(
            today=_sums(await totals(db, tenant, day, day)),
            week=_sums(await totals(db, tenant, week_start(day), day)),
            month=_sums(await totals(db, tenant, day.replace(day=1), day)),
            entries=[
                EntryOut(
                    id=entry.id,
                    kind=entry.kind,
                    category=entry.category,
                    amount=entry.amount,
                    description=entry.description,
                    occurred_on=entry.occurred_on,
                    source=entry.source,
                    order_code=code,
                    account=name,
                    reverses=entry.reverses,
                    reversed=entry.id in undone,
                    has_bill=entry.media_id is not None,
                )
                for entry, code, name in rows
            ],
            accounts=[_account(account) for account in accounts],
            categories=[str(category) for category in SPENDING],
        )


@router.post("/books/expenses")
async def add_expense(body: NewExpense, session: WriteDep, services: ServicesDep) -> dict[str, str]:
    if body.category == Category.SALES:
        raise AppError("not_an_expense", "Sales come in from orders, not from this form")
    async with services.db.tenant(session.tenant_id) as db:
        tenant = await db.get(Tenant, session.tenant_id)
        assert tenant is not None
        if body.occurred_on is not None and body.occurred_on > today(tenant):
            raise AppError("future_date", "An expense cannot be in the future")
        entry = await ledger.post(
            db,
            tenant,
            EntryKind.EXPENSE,
            body.amount,
            _actor(session),
            category=body.category,
            source="console",
            description=body.description,
            occurred_on=body.occurred_on,
        )
        return {"id": str(entry.id)}


@router.post("/books/entries/{entry_id}/reverse")
async def reverse_entry(
    entry_id: uuid.UUID, session: WriteDep, services: ServicesDep
) -> dict[str, str]:
    """Undo an entry with a mirror one; the first stays, so the books show both."""
    async with services.db.tenant(session.tenant_id) as db:
        tenant = await db.get(Tenant, session.tenant_id)
        assert tenant is not None
        mirror = await ledger.reverse(db, tenant, entry_id, _actor(session))
        return {"id": str(mirror.id)}


async def _udhaar_account(db: AsyncSession, tenant: Tenant, body: UdhaarMove) -> UdhaarAccount:
    if body.account_id is not None:
        account = await db.get(UdhaarAccount, body.account_id)
        if account is None:
            raise NotFound("udhaar account")
        return account
    if body.kind != EntryKind.UDHAAR_GIVEN:
        raise AppError("no_account", "Money can only come back on an account that owes it")
    if body.customer_id is not None:
        customer = await db.get(Customer, body.customer_id)
        if customer is None:
            raise NotFound("customer")
        return await udhaar.for_customer(db, tenant, customer)
    return await udhaar.open_account(db, tenant, body.name or "")


@router.post("/udhaar")
async def move_udhaar(body: UdhaarMove, session: WriteDep, services: ServicesDep) -> AccountOut:
    if body.kind not in (EntryKind.UDHAAR_GIVEN, EntryKind.UDHAAR_PAID):
        raise AppError("not_udhaar", "Give or receive udhaar here")
    async with services.db.tenant(session.tenant_id) as db:
        tenant = await db.get(Tenant, session.tenant_id)
        assert tenant is not None
        account = await _udhaar_account(db, tenant, body)
        move = udhaar.give if body.kind == EntryKind.UDHAAR_GIVEN else udhaar.receive
        await move(
            db, tenant, account, body.amount, _actor(session), source="console", note=body.note
        )
        return _account(account)


@router.get("/udhaar/{account_id}")
async def udhaar_account(
    account_id: uuid.UUID, session: ReadDep, services: ServicesDep
) -> AccountDetail:
    """One account with every entry on it, oldest first."""
    async with services.db.tenant(session.tenant_id) as db:
        account = await db.get(UdhaarAccount, account_id)
        if account is None:
            raise NotFound("udhaar account")
        entries = await db.scalars(
            select(LedgerEntry)
            .where(LedgerEntry.account_id == account_id)
            .order_by(LedgerEntry.created_at)
        )
        return AccountDetail(
            account=_account(account),
            entries=[
                AccountEntry(
                    kind=entry.kind,
                    amount=entry.amount,
                    description=entry.description,
                    occurred_on=entry.occurred_on,
                )
                for entry in entries
            ],
        )
