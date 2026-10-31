"""Looking ahead and back in the console: the cash-flow forecast (feature 58), the year and
zakat (62), the workbook for tax filing (63), and staff pay (60)."""

from __future__ import annotations

import uuid
from datetime import date
from typing import Annotated, Any

from fastapi import APIRouter, Query, Request
from fastapi.responses import Response
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from chatfront.audit.service import Actor, ActorType, record
from chatfront.books import export, forecast, passport, staff_pay, year
from chatfront.books.ledger import MAX_AMOUNT, today
from chatfront.books.models import CreditPassport
from chatfront.core.clock import utcnow
from chatfront.core.errors import AppError, NotFound
from chatfront.core.models import Tenant
from chatfront.owner.rights import Can
from chatfront.owner.routes import ServicesDep, needs, needs_recent
from chatfront.owner.sessions import OwnerSession
from chatfront.payments.receipts import check

router = APIRouter(prefix="/api/owner")
ReadDep = Annotated[OwnerSession, needs(Can.BOOKS)]
WriteDep = Annotated[OwnerSession, needs(Can.BOOKS_WRITE)]
TeamDep = Annotated[OwnerSession, needs(Can.TEAM)]
RecentTeamDep = Annotated[OwnerSession, needs_recent(Can.TEAM)]
XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


class CashIn(BaseModel):
    amount: int = Field(ge=0, le=MAX_AMOUNT)  # minor units, counted today


class ZakatIn(BaseModel):
    cash: int = Field(ge=0, le=year.MAX_FIGURE)
    stock: int | None = Field(default=None, ge=0, le=year.MAX_FIGURE)  # None: code's figure
    owed_to_shop: int | None = Field(default=None, ge=0, le=year.MAX_FIGURE)  # None: the khata
    debts: int = Field(default=0, ge=0, le=year.MAX_FIGURE)
    nisab: int = Field(ge=0, le=year.MAX_FIGURE)


class PayIn(BaseModel):
    salary: int = Field(ge=0, le=staff_pay.MAX_SALARY)
    commission_bp: int = Field(default=0, ge=0, le=staff_pay.MAX_COMMISSION_BP)
    pay_day: int = Field(default=1, ge=1, le=28)


class PayOut(BaseModel):
    staff_id: uuid.UUID
    name: str
    salary: int
    commission_bp: int
    pay_day: int
    sales: int
    commission: int
    due: int
    paid: int | None
    paid_at: str | None


def _actor(session: OwnerSession) -> Actor:
    return Actor(ActorType.STAFF, str(session.staff_id))


async def _tenant(db: AsyncSession, tenant_id: uuid.UUID) -> Tenant:
    tenant = await db.get(Tenant, tenant_id)
    assert tenant is not None
    return tenant


@router.get("/books/forecast")
async def cash_forecast(session: ReadDep, services: ServicesDep) -> dict[str, Any]:
    async with services.db.tenant(session.tenant_id) as db:
        tenant = await _tenant(db, session.tenant_id)
        found = await forecast.forecast(db, tenant, await forecast.staff_fixed(db))
    return forecast.as_dict(found)


@router.put("/books/cash")
async def set_cash(body: CashIn, session: WriteDep, services: ServicesDep) -> dict[str, Any]:
    """The cash in hand and in the bank, as the owner counted it today."""
    async with services.db.tenant(session.tenant_id) as db:
        tenant = await _tenant(db, session.tenant_id)
        value = {"amount": body.amount, "on": today(tenant).isoformat(), "at": utcnow().isoformat()}
        tenant.profile = {**(tenant.profile or {}), forecast.CASH: value}
        await record(
            db,
            tenant.id,
            _actor(session),
            "books.cash",
            entity=("tenant", str(tenant.id)),
            data=value,
        )
        found = await forecast.forecast(db, tenant, await forecast.staff_fixed(db))
    return forecast.as_dict(found)


@router.get("/books/year")
async def year_summary(
    session: ReadDep,
    services: ServicesDep,
    starts: int | None = Query(default=None, ge=2000, le=2100),
) -> dict[str, Any]:
    async with services.db.tenant(session.tenant_id) as db:
        tenant = await _tenant(db, session.tenant_id)
        start, end = (
            year.starting(tenant, starts) if starts else year.year_of(tenant, today(tenant))
        )
        found = await year.summary(db, tenant, start, end)
        stock, unknown = await year.stock_at_cost(db)
        owed = await year.owed_to_shop(db)
    sums = found.totals
    return {
        "label": found.label,
        "start": found.start.isoformat(),
        "end": found.end.isoformat(),
        "sales": sums.sales,
        "udhaar_paid": sums.udhaar_paid,
        "expenses": sums.expenses,
        "refunds": sums.refunds,
        "write_offs": sums.write_offs,
        "cash_profit": sums.cash_profit,
        "goods_profit": sums.goods_profit,
        "orders": sums.orders,
        "months": [
            {"month": m.month, "money_in": m.money_in, "money_out": m.money_out}
            for m in found.months
        ],
        "expense_categories": found.expenses,
        "top": [{"name": s.name, "units": s.units, "revenue": s.revenue} for s in found.top],
        "zakat_inputs": {"stock": stock, "stock_without_cost": unknown, "owed_to_shop": owed},
    }


@router.post("/books/zakat")
async def zakat(body: ZakatIn, session: ReadDep, services: ServicesDep) -> dict[str, int]:
    async with services.db.tenant(session.tenant_id) as db:
        stock, unknown = await year.stock_at_cost(db)
        owed = await year.owed_to_shop(db)
    found = year.zakat(
        body.cash,
        stock if body.stock is None else body.stock,
        unknown,
        owed if body.owed_to_shop is None else body.owed_to_shop,
        body.debts,
        body.nisab,
    )
    return {
        "cash": found.cash,
        "stock": found.stock,
        "stock_without_cost": found.stock_without_cost,
        "owed_to_shop": found.owed_to_shop,
        "debts": found.debts,
        "net": found.net,
        "nisab": found.nisab,
        "due": found.due,
    }


@router.get("/books/export.xlsx")
async def tax_workbook(session: ReadDep, services: ServicesDep, start: date, end: date) -> Response:
    async with services.db.tenant(session.tenant_id) as db:
        tenant = await _tenant(db, session.tenant_id)
        data = await export.workbook(db, tenant, start, end)
    name = f"{tenant.slug}-books-{start.isoformat()}-{end.isoformat()}.xlsx"
    return Response(
        data, media_type=XLSX, headers={"Content-Disposition": f'attachment; filename="{name}"'}
    )


def _pay_out(pay: staff_pay.Pay) -> PayOut:
    return PayOut(
        staff_id=pay.staff_id,
        name=pay.name,
        salary=pay.salary,
        commission_bp=pay.commission_bp,
        pay_day=pay.pay_day,
        sales=pay.sales,
        commission=pay.commission,
        due=pay.due,
        paid=pay.paid.amount if pay.paid else None,
        paid_at=pay.paid.created_at.isoformat() if pay.paid else None,
    )


@router.get("/team/pay")
async def team_pay(
    session: TeamDep, services: ServicesDep, month: str = Query(pattern=r"^\d{4}-\d{2}$")
) -> list[PayOut]:
    first = staff_pay.month_of(month)
    async with services.db.tenant(session.tenant_id) as db:
        tenant = await _tenant(db, session.tenant_id)
        return [_pay_out(p) for p in await staff_pay.month_pay(db, tenant, first)]


@router.put("/team/{staff_id}/pay")
async def set_team_pay(
    staff_id: uuid.UUID, body: PayIn, session: RecentTeamDep, services: ServicesDep
) -> dict[str, int]:
    async with services.db.tenant(session.tenant_id) as db:
        pay = await staff_pay.set_pay(
            db,
            session.tenant_id,
            staff_id,
            body.salary,
            body.commission_bp,
            body.pay_day,
            _actor(session),
        )
        return {"salary": pay.salary, "commission_bp": pay.commission_bp, "pay_day": pay.pay_day}


@router.post("/team/{staff_id}/pay/{month}")
async def pay_month(
    staff_id: uuid.UUID, month: str, session: RecentTeamDep, services: ServicesDep
) -> PayOut:
    first = staff_pay.month_of(month)
    async with services.db.tenant(session.tenant_id) as db:
        tenant = await _tenant(db, session.tenant_id)
        await staff_pay.pay_month(db, tenant, staff_id, first, _actor(session), session.staff_id)
        found = await staff_pay.month_pay(db, tenant, first)
    return _pay_out(next(p for p in found if p.staff_id == staff_id))


# -- the credit passport (feature 64) -------------------------------------------------------

SettingsDep = Annotated[OwnerSession, needs(Can.SETTINGS)]


class PassportIn(BaseModel):
    agree: bool
    wording: str = Field(min_length=10, max_length=500)  # what the owner read and agreed to


class PassportOut(BaseModel):
    code: str
    made_on: str
    revoked: bool
    totals: dict[str, int]


class PublicPassport(BaseModel):
    statement: dict[str, Any]
    signature: str
    public_key: str
    signed_text: str
    valid: bool
    revoked: bool


def _passport_out(found: CreditPassport) -> PassportOut:
    return PassportOut(
        code=found.code,
        made_on=str(found.statement.get("made_on")),
        revoked=found.revoked_at is not None,
        totals=dict(found.statement.get("totals") or {}),
    )


@router.get("/passports")
async def passports(session: ReadDep, services: ServicesDep) -> list[PassportOut]:
    async with services.db.tenant(session.tenant_id) as db:
        found = await db.scalars(
            select(CreditPassport).order_by(CreditPassport.created_at.desc()).limit(20)
        )
        return [_passport_out(p) for p in found]


@router.post("/passports")
async def make_passport(
    body: PassportIn, session: SettingsDep, services: ServicesDep
) -> PassportOut:
    if not body.agree:
        raise AppError("not_agreed", "A passport is made only when you agree to share it")
    async with services.db.tenant(session.tenant_id) as db:
        tenant = await _tenant(db, session.tenant_id)
        found = await passport.issue(
            db,
            tenant,
            services.settings.secret_key.get_secret_value(),
            _actor(session),
            session.staff_id,
            body.wording,
        )
        return _passport_out(found)


@router.post("/passports/{code}/revoke")
async def revoke_passport(
    code: str, session: SettingsDep, services: ServicesDep
) -> dict[str, bool]:
    async with services.db.tenant(session.tenant_id) as db:
        done = await passport.revoke(db, session.tenant_id, code.strip().upper(), _actor(session))
    if not done:
        raise NotFound("passport")
    return {"revoked": True}


public = APIRouter()


@public.get("/api/passport/{code}")
async def read_passport(code: str, request: Request, services: ServicesDep) -> PublicPassport:
    """What a lender opens: the figures, the signature, and whether it checks out."""
    client = request.client.host if request.client else "unknown"
    services.limiter.check(f"passport:{client}")
    async with services.db.system() as db:  # public lookup by code, before any tenant
        found = await db.scalar(
            select(CreditPassport).where(CreditPassport.code == code.strip().upper())
        )
    if found is None:
        raise NotFound("passport")
    verdict = check(found.statement, found.signature, found.public_key)
    return PublicPassport(
        statement=found.statement,
        signature=found.signature,
        public_key=found.public_key,
        signed_text=verdict.text,
        valid=verdict.valid,
        revoked=found.revoked_at is not None,
    )
