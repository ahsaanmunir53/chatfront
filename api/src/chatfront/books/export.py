"""Sales and expenses as a workbook for tax filing (feature 63).

One Excel file for the dates the owner picks: a summary sheet, every sale with its order and
how the money came, every expense with its category and whether a bill photo backs it, and
the refunds. It is the shop's own books laid out for the person who files its return (in
Pakistan, with FBR); it does not file anything, and the summary sheet says to check it with a
tax adviser before filing.
"""

from __future__ import annotations

import io
from datetime import date

from openpyxl import Workbook
from openpyxl.styles import Font
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from chatfront.books.models import EntryKind, LedgerEntry
from chatfront.books.reports import totals
from chatfront.core.countries import get_pack
from chatfront.core.errors import AppError
from chatfront.core.models import Tenant
from chatfront.orders.models import Order

MAX_DAYS = 400
BOLD = Font(bold=True)


def check_range(start: date, end: date) -> None:
    if end < start or (end - start).days > MAX_DAYS:
        raise AppError("bad_range", f"Pick up to {MAX_DAYS} days, the start first")


def _major(amount: int, digits: int) -> float:
    return float(amount) / float(10**digits)


async def _entries(
    session: AsyncSession, start: date, end: date, kind: EntryKind
) -> list[tuple[LedgerEntry, str | None]]:
    rows = await session.execute(
        select(LedgerEntry, Order.code)
        .outerjoin(Order, Order.id == LedgerEntry.order_id)
        .where(
            LedgerEntry.kind == kind,
            LedgerEntry.occurred_on >= start,
            LedgerEntry.occurred_on <= end,
        )
        .order_by(LedgerEntry.occurred_on, LedgerEntry.created_at)
    )
    return [(entry, code) for entry, code in rows]


def _sheet(book: Workbook, title: str, headers: list[str], rows: list[list[object]]) -> None:
    sheet = book.create_sheet(title)
    sheet.append(headers)
    for cell in sheet[1]:
        cell.font = BOLD
    for row in rows:
        sheet.append(row)
    for column, header in zip("ABCDEFG", headers, strict=False):
        sheet.column_dimensions[column].width = max(12, len(header) + 4)


async def workbook(session: AsyncSession, tenant: Tenant, start: date, end: date) -> bytes:
    check_range(start, end)
    digits = get_pack(tenant.country).minor_digits
    sums = await totals(session, tenant, start, end)
    sales = await _entries(session, start, end, EntryKind.SALE)
    expenses = await _entries(session, start, end, EntryKind.EXPENSE)
    refunds = await _entries(session, start, end, EntryKind.REFUND)
    book = Workbook()
    summary = book.active
    assert summary is not None
    summary.title = "Summary"
    for row in (
        [tenant.name],
        [f"From {start.isoformat()} to {end.isoformat()}", f"Amounts in {tenant.currency}"],
        [],
        ["Sales received", _major(sums.sales, digits)],
        ["Khata payments received", _major(sums.udhaar_paid, digits)],
        ["Expenses", _major(sums.expenses, digits)],
        ["Refunds", _major(sums.refunds, digits)],
        ["Cash profit", _major(sums.cash_profit, digits)],
        ["Orders placed", sums.orders],
        [],
        ["From the shop's own books in Chatfront. Check with a tax adviser before filing."],
    ):
        summary.append(row)
    summary["A1"].font = BOLD
    summary.column_dimensions["A"].width = 32
    _sheet(
        book,
        "Sales",
        ["Date", "Order", "Amount", "How it came", "Description"],
        [
            [e.occurred_on, code or "", _major(e.amount, digits), e.source, e.description]
            for e, code in sales
        ],
    )
    _sheet(
        book,
        "Expenses",
        ["Date", "Category", "Amount", "Bill photo", "Description"],
        [
            [
                e.occurred_on,
                e.category,
                _major(e.amount, digits),
                "yes" if e.media_id else "",
                e.description,
            ]
            for e, _ in expenses
        ],
    )
    _sheet(
        book,
        "Refunds",
        ["Date", "Order", "Amount", "Description"],
        [
            [e.occurred_on, code or "", _major(e.amount, digits), e.description]
            for e, code in refunds
        ],
    )
    out = io.BytesIO()
    book.save(out)
    return out.getvalue()
