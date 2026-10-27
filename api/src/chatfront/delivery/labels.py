"""Shipping labels (feature 101): an A6 PDF any courier can take, no courier API needed.

The label carries the shop, the customer's name, number and tidied address, the city large,
the location pin's link when there is one, the order code, what the rider collects (or PAID),
the items, and a QR code that opens the parcel's tracking page. Text in Urdu, Arabic,
Devanagari or Bengali script is shaped with HarfBuzz in the bundled Noto fonts.
"""

from __future__ import annotations

import io
from dataclasses import dataclass
from pathlib import Path

import segno
from fpdf import FPDF

from chatfront.core.countries import get_pack, shop_money
from chatfront.core.models import Tenant
from chatfront.delivery.address import phone_for_people
from chatfront.orders.models import Order, OrderItem

FONTS = Path(__file__).parent / "fonts"
SIZE = (105.0, 148.0)  # A6, millimetres
MARGIN = 5.0
QR_SIDE = 26.0
MAX_ITEMS = 6
_INK = (24, 24, 24)
_RULE = (150, 150, 150)
TIMES = "\N{MULTIPLICATION SIGN}"


@dataclass(frozen=True, slots=True)
class LabelData:
    tenant: Tenant
    order: Order
    items: list[OrderItem]
    collect: int  # minor units the rider collects; 0 when paid
    track_url: str
    courier: str | None = None
    tracking_number: str | None = None


class _Label(FPDF):
    def __init__(self) -> None:
        super().__init__(orientation="P", unit="mm", format=SIZE)
        self.set_margins(MARGIN, MARGIN, MARGIN)
        self.set_auto_page_break(False)
        self.add_font("Sans", "", FONTS / "NotoSans-Regular.ttf")
        self.add_font("Sans", "B", FONTS / "NotoSans-Bold.ttf")
        self.add_font("Naskh", "", FONTS / "NotoNaskhArabic-Regular.ttf")
        self.add_font("Naskh", "B", FONTS / "NotoNaskhArabic-Bold.ttf")
        self.add_font("Deva", "", FONTS / "NotoSansDevanagari-Regular.ttf")
        self.add_font("Beng", "", FONTS / "NotoSansBengali-Regular.ttf")
        self.set_fallback_fonts(["Naskh", "Deva", "Beng"], exact_match=False)
        self.set_text_shaping(True)
        self.set_text_color(*_INK)
        self.set_draw_color(*_RULE)
        self.set_creator("Chatfront")

    @property
    def inner(self) -> float:
        return SIZE[0] - 2 * MARGIN

    def text_line(self, text: str, size: float, *, bold: bool = False, width: float = 0) -> None:
        self.set_font("Sans", "B" if bold else "", size)
        self.multi_cell(width or self.inner, size * 0.45, text, new_x="LMARGIN", new_y="NEXT")

    def caption(self, text: str) -> None:
        self.set_font("Sans", "B", 6.5)
        self.cell(self.inner, 3.4, text, new_x="LMARGIN", new_y="NEXT")

    def rule(self, gap: float = 1.8) -> None:
        self.ln(gap)
        self.line(MARGIN, self.get_y(), SIZE[0] - MARGIN, self.get_y())
        self.ln(gap)


def _qr_png(url: str) -> io.BytesIO:
    out = io.BytesIO()
    segno.make(url, error="m").save(out, kind="png", scale=8, border=1)
    out.seek(0)
    return out


def label_pdf(data: LabelData) -> bytes:
    pdf = _Label()
    pdf.add_page()
    order, address = data.order, data.order.address or {}

    # From, with the QR code beside it.
    text_width = pdf.inner - QR_SIDE - 3
    pdf.image(_qr_png(data.track_url), x=SIZE[0] - MARGIN - QR_SIDE, y=MARGIN, w=QR_SIDE)
    pdf.caption("FROM")
    pdf.text_line(data.tenant.name, 10, bold=True, width=text_width)
    if city := (data.tenant.profile or {}).get("city"):
        pdf.text_line(str(city), 8, width=text_width)
    pdf.set_y(max(pdf.get_y(), MARGIN + QR_SIDE))
    pdf.rule()

    # To: the part a rider reads.
    pdf.caption("TO")
    if name := address.get("name"):
        pdf.text_line(str(name), 11, bold=True)
    if phone := address.get("phone"):
        pdf.text_line(phone_for_people(str(phone), get_pack(data.tenant.country)), 10)
    pdf.text_line(str(address.get("address", "")), 9.5)
    pdf.text_line(str(address.get("city", "")).upper(), 15, bold=True)
    if pin := address.get("pin"):
        pdf.text_line(f"Pin: maps.google.com/?q={pin['lat']:.5f},{pin['lng']:.5f}", 7.5)
    pdf.rule()

    # The order code and what to collect, the two things every courier asks for.
    half = pdf.inner / 2
    top = pdf.get_y()
    pdf.caption("ORDER")
    pdf.text_line(order.code, 16, bold=True, width=half)
    pdf.set_xy(MARGIN + half, top)
    pdf.set_font("Sans", "B", 6.5)
    pdf.cell(half, 3.4, "COLLECT ON DELIVERY" if data.collect else "PAYMENT", align="R")
    pdf.set_xy(MARGIN + half, top + 3.4)
    pdf.set_font("Sans", "B", 16)
    collect = shop_money(data.tenant, data.collect, "en") if data.collect else "PAID"
    pdf.cell(half, 7.2, collect, align="R")
    pdf.set_y(max(pdf.get_y(), top + 3.4 + 7.2))
    pdf.rule()

    # What is inside.
    pdf.caption("ITEMS")
    for item in data.items[:MAX_ITEMS]:
        options = " ".join(str(v) for v in item.options.values())
        named = f"{item.name} ({options})" if options else item.name
        pdf.text_line(f"{item.quantity} {TIMES} {named}", 8.5)
    if len(data.items) > MAX_ITEMS:
        pdf.text_line(f"+ {len(data.items) - MAX_ITEMS} more", 8.5)
    if data.courier or data.tracking_number:
        pdf.rule()
        pdf.text_line(
            " · ".join(part for part in (data.courier, data.tracking_number) if part), 8.5
        )
    return bytes(pdf.output())
