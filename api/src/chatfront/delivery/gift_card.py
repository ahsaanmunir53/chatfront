"""The message card for a gift (feature 21): an A6 card, folded into the parcel, with the
buyer's words in their own script and who it is from. Drawn from a template by code, since
Muse Image is out of reach; no price appears on it.
"""

from __future__ import annotations

from chatfront.core.models import Tenant
from chatfront.delivery.labels import ShopPdf
from chatfront.orders.models import Order

CARD = (148.0, 105.0)  # A6 across, in millimetres
MARGIN = 12.0
_ACCENT = (224, 69, 58)


def card_pdf(tenant: Tenant, order: Order) -> bytes:
    pdf = ShopPdf(CARD, MARGIN)
    pdf.add_page()
    pdf.set_draw_color(*_ACCENT)
    pdf.set_line_width(0.8)
    pdf.rect(5, 5, CARD[0] - 10, CARD[1] - 10)
    pdf.set_y(MARGIN + 2)
    pdf.set_font("Sans", "B", 7)
    pdf.cell(pdf.inner, 4, tenant.name.upper(), align="C", new_x="LMARGIN", new_y="NEXT")
    message = order.gift_message or ""
    pdf.set_font("Sans", "", 16 if len(message) < 90 else 12)
    height = 8 if len(message) < 90 else 6
    lines = pdf.multi_cell(pdf.inner, height, message, dry_run=True, output="LINES")
    assert isinstance(lines, list)
    pdf.set_y(max((CARD[1] - len(lines) * height) / 2 - 4, MARGIN + 8))
    pdf.set_font("Sans", "", 16 if len(message) < 90 else 12)
    pdf.multi_cell(pdf.inner, height, message, align="C", new_x="LMARGIN", new_y="NEXT")
    if order.gift_from:
        pdf.ln(4)
        pdf.set_font("Sans", "B", 11)
        pdf.cell(pdf.inner, 6, order.gift_from, align="C", new_x="LMARGIN", new_y="NEXT")
    return bytes(pdf.output())
