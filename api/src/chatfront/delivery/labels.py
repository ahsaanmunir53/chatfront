"""Shipping labels (feature 101): an A6 PDF any courier can take, no courier API needed.

The label carries the shop, the customer's name, number and tidied address, the city large,
the location pin's link when there is one, the order code, what the rider collects (or PAID),
the items, and a QR code that opens the parcel's tracking page. Text in Urdu, Arabic,
Devanagari or Bengali script is shaped with HarfBuzz in the bundled Noto fonts.

A return label (feature 92) turns it round for a reverse pickup: from the customer, to the
shop, with the return's code large and no cash to collect.
"""

from __future__ import annotations

import io
from dataclasses import dataclass
from pathlib import Path

import segno
from fpdf import FPDF
from fpdf.enums import TextEmphasis

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
    return_code: str | None = None  # a return label: from the customer back to the shop
    shop_phone: str | None = None


@dataclass(frozen=True, slots=True)
class _Party:
    name: str
    phone: str = ""
    address: str = ""
    city: str = ""
    pin: str = ""


def _customer(data: LabelData) -> _Party:
    address = data.order.address or {}
    phone = address.get("phone")
    pin = address.get("pin")
    return _Party(
        name=str(address.get("name", "")),
        phone=phone_for_people(str(phone), get_pack(data.tenant.country)) if phone else "",
        address=str(address.get("address", "")),
        city=str(address.get("city", "")),
        pin=f"Pin: maps.google.com/?q={pin['lat']:.5f},{pin['lng']:.5f}" if pin else "",
    )


def _shop(data: LabelData) -> _Party:
    phone = data.shop_phone or ""
    return _Party(
        name=data.tenant.name,
        phone=phone_for_people(phone, get_pack(data.tenant.country)) if phone else "",
        city=str((data.tenant.profile or {}).get("city") or ""),
    )


class ShopPdf(FPDF):
    """A page with the bundled Noto fonts and text shaping, so names in any of the shop's
    scripts print; labels and packing slips both use it."""

    def __init__(self, size: tuple[float, float] | str, margin: float) -> None:
        super().__init__(orientation="P", unit="mm", format=size)
        self.set_margins(margin, margin, margin)
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

    def set_font(
        self, family: str | None = None, style: str | TextEmphasis = "", size: float = 0
    ) -> None:
        super().set_font(family, style, size)
        # A line drawn in a fallback font (Urdu, Hindi, Bengali) leaves that font set on the
        # page, so the next line must name its own font again or it prints with none.
        self.current_font_is_set_on_page = False

    @property
    def inner(self) -> float:
        return float(self.epw)

    def text_line(self, text: str, size: float, *, bold: bool = False, width: float = 0) -> None:
        self.set_font("Sans", "B" if bold else "", size)
        self.multi_cell(width or self.inner, size * 0.45, text, new_x="LMARGIN", new_y="NEXT")

    def caption(self, text: str) -> None:
        self.set_font("Sans", "B", 6.5)
        self.cell(self.inner, 3.4, text, new_x="LMARGIN", new_y="NEXT")

    def rule(self, gap: float = 1.8) -> None:
        self.ln(gap)
        self.line(self.l_margin, self.get_y(), self.w - self.r_margin, self.get_y())
        self.ln(gap)


def named(item: OrderItem) -> str:
    """An order line as packers and riders read it: the name and its colour or size."""
    options = " ".join(str(v) for v in item.options.values())
    return f"{item.name} ({options})" if options else item.name


def _qr_png(url: str) -> io.BytesIO:
    out = io.BytesIO()
    segno.make(url, error="m").save(out, kind="png", scale=8, border=1)
    out.seek(0)
    return out


def _codes(data: LabelData) -> tuple[tuple[str, str], tuple[str, str]]:
    """The two boxes under the address: (caption, value) on the left and on the right."""
    if data.return_code:
        return ("RETURN", data.return_code), ("ORDER", data.order.code)
    if data.collect:
        return ("ORDER", data.order.code), (
            "COLLECT ON DELIVERY",
            shop_money(data.tenant, data.collect, "en"),
        )
    return ("ORDER", data.order.code), ("PAYMENT", "PAID")


def label_pdf(data: LabelData) -> bytes:
    pdf = ShopPdf(SIZE, MARGIN)
    pdf.add_page()
    returning = data.return_code is not None
    customer, shop = _customer(data), _shop(data)
    sender, receiver = (customer, shop) if returning else (shop, customer)

    # From, with the QR code beside it.
    text_width = pdf.inner - QR_SIDE - 3
    pdf.image(_qr_png(data.track_url), x=SIZE[0] - MARGIN - QR_SIDE, y=MARGIN, w=QR_SIDE)
    pdf.caption("FROM")
    pdf.text_line(sender.name, 10, bold=True, width=text_width)
    for part in (sender.phone, sender.address, sender.city):
        if part:
            pdf.text_line(part, 8, width=text_width)
    pdf.set_y(max(pdf.get_y(), MARGIN + QR_SIDE))
    pdf.rule()

    # To: the part a rider reads.
    pdf.caption("TO")
    if receiver.name:
        pdf.text_line(receiver.name, 11, bold=True)
    if receiver.phone:
        pdf.text_line(receiver.phone, 10)
    if receiver.address:
        pdf.text_line(receiver.address, 9.5)
    pdf.text_line(receiver.city.upper(), 15, bold=True)
    if receiver.pin:
        pdf.text_line(receiver.pin, 7.5)
    pdf.rule()

    # The code and what to collect, the two things every courier asks for.
    (left_caption, left), (right_caption, right) = _codes(data)
    half = pdf.inner / 2
    top = pdf.get_y()
    pdf.caption(left_caption)
    pdf.text_line(left, 16, bold=True, width=half)
    pdf.set_xy(MARGIN + half, top)
    pdf.set_font("Sans", "B", 6.5)
    pdf.cell(half, 3.4, right_caption, align="R")
    pdf.set_xy(MARGIN + half, top + 3.4)
    pdf.set_font("Sans", "B", 16)
    pdf.cell(half, 7.2, right, align="R")
    pdf.set_y(max(pdf.get_y(), top + 3.4 + 7.2))
    pdf.rule()

    # What is inside.
    pdf.caption("ITEMS COMING BACK" if returning else "ITEMS")
    for item in data.items[:MAX_ITEMS]:
        pdf.text_line(f"{item.quantity} {TIMES} {named(item)}", 8.5)
    if len(data.items) > MAX_ITEMS:
        pdf.text_line(f"+ {len(data.items) - MAX_ITEMS} more", 8.5)
    if data.courier or data.tracking_number:
        pdf.rule()
        pdf.text_line(
            " · ".join(part for part in (data.courier, data.tracking_number) if part), 8.5
        )
    return bytes(pdf.output())
