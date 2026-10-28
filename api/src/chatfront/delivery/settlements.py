"""The courier's sheet, matched to the shop's orders (features 31 and 32).

Every courier in Pakistan gives its merchants a sheet: the parcels with their status, and the
cash-on-delivery money it collected and paid over, less its charges. The owner uploads it as it
comes (CSV or Excel); code finds the columns by their usual names, matches each row to an
order by its tracking number or the order's code, and reports what does not add up: money
short of what the rider was to collect, a parcel the shop has no order for, a row paid twice.
A row that says delivered or returned moves a dispatched order on, so the customer hears. Code
writes the courier's charge on the parcel when the owner had not; it never changes an amount
the customer paid.
"""

from __future__ import annotations

import hashlib
import re
import uuid
from dataclasses import dataclass, field
from enum import StrEnum

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from chatfront.core.errors import AppError
from chatfront.core.numbers import find_numbers
from chatfront.delivery.couriers import Stage, stage_of
from chatfront.delivery.models import CodSettlement, Shipment
from chatfront.orders.models import Order
from chatfront.stock.sheets import read_rows

FIELDS = ("tracking", "order", "status", "cod", "charges", "net")
HEADER_WORDS: dict[str, tuple[str, ...]] = {
    "tracking": ("tracking", "cn", "consignment", "cn #", "cn no", "booked packet", "awb"),
    "order": ("order", "reference", "ref", "order id", "order ref", "invoice"),
    "status": ("status", "delivery status", "current status", "state"),
    "cod": ("cod", "cod amount", "collected", "collection", "invoice amount", "amount"),
    "charges": ("charges", "delivery charges", "service charges", "fee", "shipping", "deduction"),
    "net": ("net", "net amount", "payable", "paid", "net payable", "settled amount"),
}


class Finding(StrEnum):
    MATCHED = "matched"
    SHORT = "short"  # paid less than the rider was to collect, after charges
    UNKNOWN = "unknown"  # no order with that tracking number or code
    PAID_BEFORE = "paid_before"  # this row was settled in an earlier sheet


@dataclass(frozen=True, slots=True)
class Row:
    tracking: str
    order: str
    status: str
    cod: int | None
    charges: int | None
    net: int | None


@dataclass
class Report:
    rows: int = 0
    matched: int = 0
    short: list[str] = field(default_factory=list)
    unknown: list[str] = field(default_factory=list)
    paid_before: list[str] = field(default_factory=list)
    delivered: list[str] = field(default_factory=list)  # orders the sheet moved on
    returned: list[str] = field(default_factory=list)
    collected: int = 0  # minor units the courier says it collected
    paid_out: int = 0  # minor units it paid over


def _column(headers: list[str], name: str) -> int | None:
    words = HEADER_WORDS[name]
    lowered = [" ".join(h.lower().replace("_", " ").split()) for h in headers]
    for word in words:  # an exact header first, then one that contains the word
        if word in lowered:
            return lowered.index(word)
    for index, header in enumerate(lowered):
        if any(re.search(rf"\b{re.escape(word)}\b", header) for word in words):
            return index
    return None


def _money(text: str, digits: int) -> int | None:
    found = find_numbers(text)
    if not found:
        return None
    minor: int = round(found[0] * 10**digits)
    return minor


def read(data: bytes, digits: int) -> list[Row]:
    table = read_rows(data)
    if len(table) < 2:
        raise AppError("empty_sheet", "The sheet has no rows")
    headers = table[0]
    where = {name: _column(headers, name) for name in FIELDS}
    if where["tracking"] is None and where["order"] is None:
        raise AppError("no_tracking_column", "No tracking number or order column was found")

    def cell(row: list[str], name: str) -> str:
        index = where[name]
        return row[index].strip() if index is not None and index < len(row) else ""

    return [
        Row(
            tracking=cell(row, "tracking").upper(),
            order=cell(row, "order").upper(),
            status=cell(row, "status"),
            cod=_money(cell(row, "cod"), digits),
            charges=_money(cell(row, "charges"), digits),
            net=_money(cell(row, "net"), digits),
        )
        for row in table[1:]
        if any(part.strip() for part in row)
    ]


def row_key(row: Row) -> str:
    """The same row in two sheets is one settlement."""
    text = "|".join([row.tracking, row.order, str(row.cod), str(row.net)])
    return hashlib.sha256(text.encode()).hexdigest()[:32]


async def match(session: AsyncSession, row: Row) -> tuple[Order, Shipment] | None:
    """The order a row is about: by its tracking number, else by the order's own code."""
    wanted = [
        *([Shipment.tracking_number == row.tracking] if row.tracking else []),
        *([Order.code == row.order] if row.order else []),
    ]
    if not wanted:
        return None
    found = (
        await session.execute(
            select(Order, Shipment)
            .join(Shipment, Shipment.order_id == Order.id)
            .where(or_(*wanted))
            .limit(1)
        )
    ).first()
    return (found[0], found[1]) if found else None


def short_by(shipment: Shipment, row: Row) -> int:
    """How much less the courier paid than the rider was to collect, after its charges."""
    if row.net is None or not shipment.cod_amount:
        return 0
    charges = row.charges if row.charges is not None else shipment.courier_charge or 0
    return max(shipment.cod_amount - charges - row.net, 0)


async def settle(
    session: AsyncSession, tenant_id: uuid.UUID, rows: list[Row], source: str
) -> tuple[Report, list[tuple[str, Stage]]]:
    """Keep each matched row's money; the moves the sheet asks for come back for the caller,
    which makes them as the owner would."""
    report = Report(rows=len(rows))
    moves: list[tuple[str, Stage]] = []
    for row in rows:
        found = await match(session, row)
        label = row.tracking or row.order
        if found is None:
            report.unknown.append(label)
            continue
        order, shipment = found
        report.matched += 1
        stage = stage_of(row.status) if row.status else Stage.ON_THE_WAY
        if stage is not Stage.ON_THE_WAY:
            moves.append((order.code, stage))
        if row.net is None and row.cod is None:
            continue
        key = row_key(row)
        if await session.scalar(select(CodSettlement.id).where(CodSettlement.row_key == key)):
            report.paid_before.append(order.code)
            continue
        short = short_by(shipment, row)
        if short:
            report.short.append(order.code)
        if row.charges is not None and shipment.courier_charge is None:
            shipment.courier_charge = row.charges  # what the courier says it charged
        report.collected += row.cod or 0
        report.paid_out += row.net or 0
        session.add(
            CodSettlement(
                tenant_id=tenant_id,
                order_id=order.id,
                row_key=key,
                source=source,
                collected=row.cod,
                charges=row.charges,
                paid=row.net,
                short=short,
            )
        )
    await session.flush()
    return report, moves
