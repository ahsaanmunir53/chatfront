"""What the books say, written by code (features 50 and 75): the day's and the week's money,
payments and orders waiting, low stock and the khata. The daily card goes to the owner in the
evening and the weekly one on Sunday; the same answers come back when the owner asks
"aaj ki sale?" or "pending payments?".

Profit here is cash: money that came in (sales and udhaar paid back) less expenses. What the
goods cost comes in with stock batches (feature 119).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import TYPE_CHECKING
from zoneinfo import ZoneInfo

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from chatfront.books.ledger import today, zone
from chatfront.books.models import EntryKind, LedgerEntry, UdhaarAccount
from chatfront.catalog import stock
from chatfront.channels.handover import staff_language
from chatfront.channels.notify import tell_owner
from chatfront.channels.types import OutText
from chatfront.core.clock import utcnow
from chatfront.core.countries import get_pack, shop_money
from chatfront.core.i18n import t
from chatfront.core.models import Tenant
from chatfront.core.provisioning import owner_of
from chatfront.jobs.queue import JobContext, enqueue
from chatfront.orders.models import Order
from chatfront.orders.states import OrderStatus
from chatfront.owner.rights import Can
from chatfront.payments.models import Payment, WalletCredit
from chatfront.payments.sms.registry import provider_name
from chatfront.stock import profit
from chatfront.stock.profit import Sold

if TYPE_CHECKING:
    from chatfront.services import Services

CARDS_JOB = "books.pl_cards"
CARD_JOB = "books.pl_card"
EVERY = timedelta(hours=1)
CARD_HOUR = 21  # the shop's evening
SUNDAY = 6
LIST_LIMIT = 10
WALLET_DAYS = timedelta(days=2)
# Who may ask for which answer.
NEEDS: dict[str, Can] = {
    "today": Can.BOOKS,
    "week": Can.BOOKS,
    "udhaar": Can.BOOKS,
    "pending_payments": Can.PAYMENTS,
    "pending_orders": Can.ORDERS,
    "low_stock": Can.CATALOG,
    "products": Can.CATALOG,
    "orders": Can.ORDERS,
    "batches": Can.BOOKS,
    "wallet_sms": Can.PAYMENTS,
}


@dataclass(frozen=True, slots=True)
class Totals:
    sales: int = 0
    udhaar_given: int = 0
    udhaar_paid: int = 0
    expenses: int = 0
    orders: int = 0
    write_offs: int = 0  # stock thrown away: a loss, not cash
    refunds: int = 0  # money sent back to customers (feature 28)
    goods: Sold = field(default_factory=Sold)  # goods sold, with their cost (feature 119)

    @property
    def cash_profit(self) -> int:
        return self.sales + self.udhaar_paid - self.expenses - self.refunds

    @property
    def goods_profit(self) -> int:
        """Goods profit on the units whose cost is known, less what was thrown away."""
        return self.goods.known_revenue - self.goods.cost - self.write_offs


def week_start(day: date) -> date:
    return day - timedelta(days=day.weekday())


async def totals(session: AsyncSession, tenant: Tenant, start: date, end: date) -> Totals:
    """Ledger sums for the shop's days from start to end, both included."""
    sums = dict(
        (
            await session.execute(
                select(LedgerEntry.kind, func.sum(LedgerEntry.amount))
                .where(
                    LedgerEntry.tenant_id == tenant.id,
                    LedgerEntry.occurred_on >= start,
                    LedgerEntry.occurred_on <= end,
                )
                .group_by(LedgerEntry.kind)
            )
        ).all()
    )
    zone = ZoneInfo(get_pack(tenant.country).timezone)
    placed = await session.scalar(
        select(func.count()).where(
            Order.tenant_id == tenant.id,
            Order.placed_at.is_not(None),
            func.timezone(zone.key, Order.placed_at) >= start,
            func.timezone(zone.key, Order.placed_at) < end + timedelta(days=1),
        )
    )
    return Totals(
        sales=int(sums.get(EntryKind.SALE) or 0),
        udhaar_given=int(sums.get(EntryKind.UDHAAR_GIVEN) or 0),
        udhaar_paid=int(sums.get(EntryKind.UDHAAR_PAID) or 0),
        expenses=int(sums.get(EntryKind.EXPENSE) or 0),
        orders=int(placed or 0),
        write_offs=int(sums.get(EntryKind.WRITE_OFF) or 0),
        refunds=int(sums.get(EntryKind.REFUND) or 0),
        goods=await profit.in_period(session, tenant, start, end),
    )


def money_card(tenant: Tenant, title: str, sums: Totals, lang: str) -> str:
    def money(amount: int) -> str:
        return shop_money(tenant, amount, lang)

    lines = [
        title,
        t("books.line_orders", lang, count=sums.orders),
        t("books.line_sales", lang, amount=money(sums.sales)),
        t("books.line_udhaar_given", lang, amount=money(sums.udhaar_given)),
        t("books.line_udhaar_paid", lang, amount=money(sums.udhaar_paid)),
        t("books.line_expenses", lang, amount=money(sums.expenses)),
        *([t("books.line_refunds", lang, amount=money(sums.refunds))] if sums.refunds else []),
        t("books.line_cash_profit", lang, amount=money(sums.cash_profit)),
    ]
    goods = sums.goods
    if goods.cost or sums.write_offs:
        lines.append(t("books.line_goods_profit", lang, amount=money(sums.goods_profit)))
    if sums.write_offs:
        lines.append(t("books.line_write_offs", lang, amount=money(sums.write_offs)))
    if goods.unknown:
        lines.append(t("books.line_cost_unknown", lang, count=goods.unknown))
    return "\n".join(lines)


async def _orders_text(session: AsyncSession, tenant: Tenant, report: str, lang: str) -> str:
    waiting = report == "pending_payments"
    statuses = (
        (OrderStatus.AWAITING_PAYMENT,)
        if waiting
        else (OrderStatus.PAID, OrderStatus.CONFIRMED, OrderStatus.PACKED)
    )
    found = list(
        await session.scalars(
            select(Order)
            .where(Order.status.in_(statuses))
            .order_by(Order.placed_at)
            .limit(LIST_LIMIT)
        )
    )
    if not found:
        return t(f"books.none_{report}", lang)
    lines = [t(f"books.title_{report}", lang, count=len(found))]
    for order in found:
        amount = shop_money(
            tenant, order.due_now if waiting and order.due_now else order.total, lang
        )
        status = t(f"order.status.{order.status}", lang)
        lines.append(f"- {order.code}: {amount}, {status}")
    return "\n".join(lines)


async def _udhaar_text(session: AsyncSession, tenant: Tenant, lang: str) -> str:
    accounts = list(
        await session.scalars(
            select(UdhaarAccount)
            .where(UdhaarAccount.balance > 0)
            .order_by(UdhaarAccount.balance.desc())
        )
    )
    if not accounts:
        return t("books.none_udhaar", lang)
    total = shop_money(tenant, sum(a.balance for a in accounts), lang)
    lines = [t("books.title_udhaar", lang, count=len(accounts), total=total)]
    now = utcnow()
    for account in accounts[:LIST_LIMIT]:
        days = (now - account.owed_since).days if account.owed_since else 0
        owed = shop_money(tenant, account.balance, lang)
        lines.append(f"- {t('books.udhaar_line', lang, name=account.name, amount=owed, days=days)}")
    return "\n".join(lines)


async def _wallet_text(session: AsyncSession, tenant: Tenant, lang: str) -> str:
    """What the shop's wallets and bank really received, from the forwarded SMS: the answer
    to "kya 5000 aaye?" before sending any money back (feature 29)."""
    rows = (
        await session.execute(
            select(WalletCredit, Order.code)
            .outerjoin(Payment, Payment.credit_id == WalletCredit.id)
            .outerjoin(Order, Order.id == Payment.order_id)
            .where(WalletCredit.received_at > utcnow() - WALLET_DAYS)
            .order_by(WalletCredit.received_at.desc())
            .limit(LIST_LIMIT)
        )
    ).all()
    if not rows:
        return t("books.none_wallet_sms", lang)
    local = zone(tenant)
    lines = [t("books.title_wallet_sms", lang, count=len(rows))]
    for credit, code in rows:
        line = t(
            "books.wallet_line",
            lang,
            amount=shop_money(tenant, credit.amount, lang),
            payer=credit.payer or "—",
            wallet=provider_name(credit.parser),
            time=f"{credit.received_at.astimezone(local):%d/%m %H:%M}",
            tid=credit.transaction_id,
            order=code or "none",
        )
        lines.append(f"- {line}")
    return "\n".join(lines)


async def report_text(session: AsyncSession, tenant: Tenant, report: str, lang: str) -> str:
    """The answer to one of the owner's questions, from the database."""
    day = today(tenant)
    if report == "today":
        return money_card(
            tenant, t("books.title_today", lang), await totals(session, tenant, day, day), lang
        )
    if report == "week":
        start = week_start(day)
        sums = await totals(session, tenant, start, day)
        return money_card(tenant, t("books.title_week", lang), sums, lang)
    if report in ("pending_payments", "pending_orders"):
        return await _orders_text(session, tenant, report, lang)
    if report == "wallet_sms":
        return await _wallet_text(session, tenant, lang)
    if report == "low_stock":
        low = await stock.low_now(session, tenant)
        if not low:
            return t("books.none_low_stock", lang)
        lines = [t("books.title_low_stock", lang, count=len(low))]
        lines += [f"- {item['name']}: {item['left']}" for item in low[:LIST_LIMIT]]
        return "\n".join(lines)
    return await _udhaar_text(session, tenant, lang)


# -- the evening cards (feature 50) ----------------------------------------------------------


async def cards_job(ctx: JobContext) -> None:
    """Hourly: queue each shop's daily card after its evening hour, and the weekly one on
    Sunday; each card once, however often this runs."""
    services: Services = ctx.services
    async with services.db.system() as session:
        tenants = list(await session.scalars(select(Tenant).where(Tenant.is_platform.is_(False))))
        for tenant in tenants:
            local = utcnow().astimezone(ZoneInfo(get_pack(tenant.country).timezone))
            if local.hour < CARD_HOUR:
                continue
            day = local.date()
            await enqueue(
                session,
                CARD_JOB,
                {"period": "today"},
                tenant_id=tenant.id,
                dedupe_key=f"pl:{tenant.id}:{day.isoformat()}",
            )
            if day.weekday() == SUNDAY:
                week = day.isocalendar()
                await enqueue(
                    session,
                    CARD_JOB,
                    {"period": "week"},
                    tenant_id=tenant.id,
                    dedupe_key=f"pl-week:{tenant.id}:{week.year}-{week.week}",
                )


async def card_job(ctx: JobContext) -> None:
    services: Services = ctx.services
    assert ctx.tenant_id is not None
    owner = await owner_of(services.db, ctx.tenant_id)
    async with services.db.tenant(ctx.tenant_id) as session:
        tenant = await session.get(Tenant, ctx.tenant_id)
        if tenant is None:
            return
        lang = staff_language(owner, tenant)
        text = await report_text(session, tenant, ctx.payload["period"], lang)
    await tell_owner(services, ctx.tenant_id, owner, OutText(text), lang)
