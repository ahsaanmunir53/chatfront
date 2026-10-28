"""The day's pick list and packing slips (feature 103).

Code gathers every order waiting to be packed (paid, or confirmed for cash on delivery) and
adds up the pieces to pick per product and colour or size, with the orders each is for. A
pre-order line counts once its ship day has come; until then its order waits, listed apart.
A pre-order's pieces are taken from stock at packing, so a line short of stock says how many
are still to come in.

The packers get the list as a message each morning (the owner, when the shop has no
packers), and anyone who packs can ask for it ("pick list") in the chat. The console prints
it with one slip per order: the items to tick off, what the rider collects, and lines for who
packed and who checked it.
"""

from __future__ import annotations

import uuid
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import TYPE_CHECKING

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from chatfront.books.ledger import today, zone
from chatfront.catalog.models import Product, Variant
from chatfront.channels.language import staff_language
from chatfront.channels.notify import tell_owner
from chatfront.channels.types import OutText
from chatfront.core.clock import local_today, utcnow
from chatfront.core.countries import shop_money
from chatfront.core.i18n import t
from chatfront.core.models import Staff, StaffRole, Tenant
from chatfront.core.provisioning import owner_of
from chatfront.delivery.labels import TIMES, ShopPdf, named
from chatfront.delivery.shipping import to_collect
from chatfront.jobs.queue import JobContext, enqueue
from chatfront.orders.models import GroupOrder, Order, OrderItem
from chatfront.orders.states import OrderStatus
from chatfront.stock import notices

if TYPE_CHECKING:
    from chatfront.services import Services

READY = (OrderStatus.PAID, OrderStatus.CONFIRMED)  # waiting to be packed
MAX_ORDERS = 200
LINES_SHOWN = 30  # in a chat message
MORNING_HOUR = 9
EVERY = timedelta(hours=1)
DAILY_JOB = "delivery.pick_lists"
SEND_JOB = "delivery.pick_list"
A4_MARGIN = 12.0
# Asking for the list, in the languages shops write in.
ASK = (
    *("pick list", "picklist", "packing list", "pack list", "kya pack karna", "aaj kya pack"),
    *("پیکنگ لسٹ", "پک لسٹ", "پڪ لسٽ", "پيڪنگ لسٽ", "پک لېست", "د راټولولو لېست"),
    *("पिक लिस्ट", "पैकिंग लिस्ट", "পিক লিস্ট", "প্যাকিং লিস্ট", "قائمة التغليف", "قائمة التجهيز"),
)
BOX_SIDE = 3.4  # millimetres: a square to tick


@dataclass
class PickLine:
    name: str
    quantity: int = 0
    orders: dict[str, int] = field(default_factory=dict)  # order code: pieces
    short: int = 0  # pre-order pieces still to come in

    def orders_text(self) -> str:
        return ", ".join(
            f"{code} {TIMES}{pieces}" if pieces > 1 else code
            for code, pieces in self.orders.items()
        )


@dataclass(frozen=True, slots=True)
class Slip:
    order: Order
    items: list[OrderItem]
    collect: int  # what the rider collects; 0 when paid
    group: str | None = None  # the group order whose parts ship together (feature 22)


@dataclass(frozen=True, slots=True)
class PickList:
    day: date
    lines: list[PickLine]
    slips: list[Slip]
    waiting: list[tuple[str, date]]  # orders whose pre-order has not come: (code, ships from)
    # Parts of a group order still open to friends (feature 22): (code, group, closes on).
    grouped: list[tuple[str, str, date]] = field(default_factory=list)

    @property
    def pieces(self) -> int:
        return sum(line.quantity for line in self.lines)


async def _ready(session: AsyncSession) -> list[Order]:
    return list(
        await session.scalars(
            select(Order)
            .where(Order.status.in_(READY))
            .order_by(Order.placed_at.asc().nulls_last(), Order.code)
            .limit(MAX_ORDERS)
        )
    )


async def _items(session: AsyncSession, orders: list[Order]) -> dict[uuid.UUID, list[OrderItem]]:
    found: dict[uuid.UUID, list[OrderItem]] = defaultdict(list)
    rows = await session.scalars(
        select(OrderItem)
        .where(OrderItem.order_id.in_([order.id for order in orders]))
        .order_by(OrderItem.id)
    )
    for item in rows:
        found[item.order_id].append(item)
    return found


async def _ship_days(session: AsyncSession, items: list[OrderItem]) -> dict[uuid.UUID, date]:
    """The ship day of each pre-ordered product."""
    ids = {item.product_id for item in items if item.preorder}
    if not ids:
        return {}
    rows = await session.execute(select(Product.id, Product.ships_from).where(Product.id.in_(ids)))
    return {product_id: day for product_id, day in rows.all() if day is not None}


def _waits_until(
    items: list[OrderItem], ship_days: dict[uuid.UUID, date], day: date
) -> date | None:
    """The day an order can be packed, when a pre-order holds it back past today."""
    later = [
        ship_days[item.product_id]
        for item in items
        if item.preorder and ship_days.get(item.product_id, day) > day
    ]
    return max(later) if later else None


async def _groups(session: AsyncSession, orders: list[Order]) -> dict[uuid.UUID, GroupOrder]:
    ids = {order.group_id for order in orders if order.group_id}
    if not ids:
        return {}
    found = await session.scalars(select(GroupOrder).where(GroupOrder.id.in_(ids)))
    return {group.id: group for group in found}


async def build(session: AsyncSession, tenant: Tenant) -> PickList:
    day = today(tenant)
    orders = await _ready(session)
    groups = await _groups(session, orders)
    grouped: list[tuple[str, str, date]] = []
    items = await _items(session, orders)
    ship_days = await _ship_days(session, [i for found in items.values() for i in found])
    lines: dict[uuid.UUID, PickLine] = {}
    preordered: dict[uuid.UUID, int] = defaultdict(int)
    slips: list[Slip] = []
    waiting: list[tuple[str, date]] = []
    for order in orders:
        mine = items.get(order.id, [])
        group = groups.get(order.group_id) if order.group_id else None
        if group is not None and group.closed_at is None and group.closes_at > utcnow():
            grouped.append((order.code, group.code, local_today(tenant.timezone, group.closes_at)))
            continue
        if (until := _waits_until(mine, ship_days, day)) is not None:
            waiting.append((order.code, until))
            continue
        slips.append(Slip(order, mine, to_collect(order), group.code if group else None))
        for item in mine:
            line = lines.setdefault(item.variant_id, PickLine(named(item)))
            line.quantity += item.quantity
            line.orders[order.code] = line.orders.get(order.code, 0) + item.quantity
            if item.preorder:
                preordered[item.variant_id] += item.quantity
    for variant_id, needed in preordered.items():
        variant = await session.get(Variant, variant_id)
        stock = (variant.stock or 0) if variant else 0
        lines[variant_id].short = max(needed - stock, 0)
    ordered = sorted(lines.values(), key=lambda line: line.name.casefold())
    return PickList(day, ordered, slips, waiting, grouped)


# -- the list in a chat ----------------------------------------------------------------------


def is_ask(words: str) -> bool:
    plain = " ".join(words.casefold().split())
    return any(phrase in plain for phrase in ASK)


def text(picks: PickList, lang: str) -> str:
    if not picks.lines and not picks.waiting and not picks.grouped:
        return t("pick.none", lang)
    out = [t("pick.title", lang, orders=len(picks.slips), pieces=picks.pieces)]
    for line in picks.lines[:LINES_SHOWN]:
        entry = f"- {line.quantity} {TIMES} {line.name}: {line.orders_text()}"
        if line.short:
            entry += " " + t("pick.short", lang, count=line.short)
        out.append(entry)
    if len(picks.lines) > LINES_SHOWN:
        out.append(t("pick.more", lang, count=len(picks.lines) - LINES_SHOWN))
    if picks.waiting:
        codes = ", ".join(f"{code} ({day.isoformat()})" for code, day in picks.waiting)
        out.append(t("pick.waiting", lang, count=len(picks.waiting), orders=codes))
    if picks.grouped:
        codes = ", ".join(
            f"{code} ({group}, {day.isoformat()})" for code, group, day in picks.grouped
        )
        out.append(t("pick.grouped", lang, count=len(picks.grouped), orders=codes))
    if picks.lines:
        out.append(t("pick.slips_hint", lang))
    return "\n".join(out)


# -- the printed list and slips --------------------------------------------------------------

ROW = 4.6  # millimetres per line of text in the list
SLIP_LINE = 5.2  # millimetres per item row on a slip
SLIP_BASE = 46.0  # the slip's other rows


def _box(pdf: ShopPdf, x: float, y: float) -> None:
    pdf.rect(x, y + 0.6, BOX_SIDE, BOX_SIDE)


def _table_row(
    pdf: ShopPdf, cells: list[tuple[str, float]], *, bold: bool = False, box: bool = False
) -> None:
    top, left = pdf.get_y(), pdf.l_margin
    if box:
        _box(pdf, left, top)
    lines = 1
    for value, width in cells:
        pdf.set_xy(left, top)
        pdf.set_font("Sans", "B" if bold else "", 9.5)
        wrapped = pdf.multi_cell(width, ROW, value, dry_run=True, output="LINES")
        assert isinstance(wrapped, list)
        lines = max(lines, len(wrapped))
        pdf.multi_cell(width, ROW, value, new_x="RIGHT", new_y="TOP")
        left += width
    pdf.set_y(top + lines * ROW + 1)


def _list_page(pdf: ShopPdf, tenant: Tenant, picks: PickList) -> None:
    pdf.add_page()
    pdf.text_line(f"Pick list: {tenant.name}", 16, bold=True)
    pdf.text_line(f"{picks.day.isoformat()}: {len(picks.slips)} orders, {picks.pieces} pieces", 10)
    pdf.rule()
    widths = [12.0, 14.0, pdf.inner * 0.45, 0.0]
    widths[3] = pdf.inner - sum(widths[:3])
    _table_row(pdf, list(zip(["", "Qty", "Item", "For orders"], widths, strict=True)), bold=True)
    for line in picks.lines:
        if pdf.get_y() > pdf.h - 30:
            pdf.add_page()
        item = line.name + (f"\n({line.short} still to come in)" if line.short else "")
        values = ["", str(line.quantity), item, line.orders_text()]
        _table_row(pdf, list(zip(values, widths, strict=True)), box=True)
    if picks.waiting:
        pdf.rule()
        codes = ", ".join(f"{code} (from {day.isoformat()})" for code, day in picks.waiting)
        pdf.text_line(f"Waiting for pre-order stock: {codes}", 9)
    if picks.grouped:
        pdf.rule()
        codes = ", ".join(f"{code} ({group}, closes {day})" for code, group, day in picks.grouped)
        pdf.text_line(f"Group orders still open to friends: {codes}", 9)


def _slip(pdf: ShopPdf, tenant: Tenant, slip: Slip) -> None:
    needed = SLIP_BASE + SLIP_LINE * len(slip.items)
    if pdf.get_y() + needed > pdf.h - A4_MARGIN:
        pdf.add_page()
    else:
        pdf.set_dash_pattern(dash=2, gap=2)
        pdf.rule(4)
        pdf.set_dash_pattern()
    order, address = slip.order, slip.order.address or {}
    pdf.caption(f"PACKING SLIP: {tenant.name}")
    pdf.text_line(order.code, 18, bold=True)
    who = ", ".join(str(part) for part in (address.get("name"), address.get("city")) if part)
    if who:
        pdf.text_line(who, 10)
    pay = (
        f"Collect on delivery: {shop_money(tenant, slip.collect, 'en')}" if slip.collect else "Paid"
    )
    pdf.text_line(pay, 10, bold=True)
    if order.gift:
        pdf.text_line("GIFT: put the message card in. No prices or bill inside.", 10, bold=True)
    if slip.group:
        pdf.text_line(f"Group order {slip.group}: pack with its other parts.", 10, bold=True)
    pdf.ln(1)
    for item in slip.items:
        if pdf.get_y() > pdf.h - A4_MARGIN - 2 * SLIP_LINE:  # a long order runs on
            pdf.add_page()
            pdf.caption(f"PACKING SLIP: {order.code} (continued)")
        _box(pdf, pdf.l_margin, pdf.get_y())
        pdf.set_x(pdf.l_margin + BOX_SIDE + 2)
        pdf.set_font("Sans", "", 10)
        pdf.multi_cell(
            pdf.inner - BOX_SIDE - 2,
            4.6,
            f"{item.quantity} {TIMES} {named(item)}",
            new_x="LMARGIN",
            new_y="NEXT",
        )
    if order.notes:
        pdf.text_line(f"Note: {order.notes}", 9)
    pdf.ln(2)
    pdf.text_line("Packed by: ____________     Checked by: ____________", 9)


def pdf_bytes(tenant: Tenant, picks: PickList) -> bytes:
    pdf = ShopPdf("A4", A4_MARGIN)
    _list_page(pdf, tenant, picks)
    for slip in picks.slips:
        _slip(pdf, tenant, slip)
    return bytes(pdf.output())


# -- the morning message -----------------------------------------------------------------------


async def daily_job(ctx: JobContext) -> None:
    """Hourly: queue each shop's pick list once a day, after its morning hour."""
    services: Services = ctx.services
    async with services.db.system() as session:
        tenants = list(await session.scalars(select(Tenant).where(Tenant.is_platform.is_(False))))
        for tenant in tenants:
            if utcnow().astimezone(zone(tenant)).hour < MORNING_HOUR:
                continue
            day = today(tenant)
            await enqueue(
                session, SEND_JOB, {}, tenant_id=tenant.id, dedupe_key=f"pick:{tenant.id}:{day}"
            )


async def _packers(session: AsyncSession) -> list[Staff]:
    return list(
        await session.scalars(
            select(Staff).where(Staff.role == StaffRole.PACKING, Staff.active.is_(True))
        )
    )


async def send_job(ctx: JobContext) -> None:
    """The morning's list to each packer; to the owner when no packer could be reached."""
    services: Services = ctx.services
    assert ctx.tenant_id is not None
    async with services.db.tenant(ctx.tenant_id) as session:
        tenant = await session.get(Tenant, ctx.tenant_id)
        if tenant is None:
            return
        picks = await build(session, tenant)
        packers = await _packers(session)
    if not picks.lines:
        return
    reached = False
    for packer in packers:
        lang = staff_language(packer, tenant)
        reached |= await tell_owner(services, tenant.id, packer, OutText(text(picks, lang)), lang)
    if not reached:
        owner = await owner_of(services.db, tenant.id)
        lang = staff_language(owner, tenant)
        await notices.notify(services, tenant.id, "pick_list", text(picks, lang))
