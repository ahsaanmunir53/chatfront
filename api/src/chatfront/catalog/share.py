"""Share cards (feature 116): a product link that shows a picture when pasted in WhatsApp or X.

Chat apps and X read a link's Open Graph and Twitter tags without running any script, so each
product has its own small page at /s/<shop>/p/<code>, served by the API: the tags name the
product, its price and the shop, and point at a 1200 by 630 card drawn by code from the
product's photo. A person who opens the link is sent on to the shop page with the product
shown.
"""

from __future__ import annotations

import io
from html import escape
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont, ImageOps

from chatfront.catalog.models import Product
from chatfront.core.countries import shop_money
from chatfront.core.i18n import t
from chatfront.core.models import Tenant
from chatfront.core.settings import Settings

FONTS = Path(__file__).parents[1] / "delivery" / "fonts"
SIZE = (1200, 630)
PHOTO = 630  # the photo's square on the left
PAD = 56
INK = (15, 30, 74)
ON_INK = (238, 242, 255)
MUTED = (170, 181, 218)
SAFFRON = (244, 183, 64)
MAX_NAME_LINES = 3
FOOTER_LINE = 40
DESCRIPTION_CHARS = 200


def share_url(settings: Settings, slug: str, code: str) -> str:
    """The link to share: served by the API, which writes the preview tags."""
    return f"{settings.public_base_url.rstrip('/')}/s/{slug}/p/{code}"


def _rtl(text: str) -> bool:
    """Urdu, Arabic or another script written right to left."""
    return any(0x0600 <= ord(c) <= 0x06FF or 0x0750 <= ord(c) <= 0x077F for c in text)


def _font(text: str, size: int, *, bold: bool = False) -> ImageFont.FreeTypeFont:
    """A font that has the text's script: Naskh for Urdu and Arabic, the Indian scripts'
    own, and Noto Sans for the rest."""
    if _rtl(text):
        name = "NotoNaskhArabic-Bold.ttf" if bold else "NotoNaskhArabic-Regular.ttf"
    elif any(0x0900 <= ord(c) <= 0x097F for c in text):
        name = "NotoSansDevanagari-Regular.ttf"
    elif any(0x0980 <= ord(c) <= 0x09FF for c in text):
        name = "NotoSansBengali-Regular.ttf"
    else:
        name = "NotoSans-Bold.ttf" if bold else "NotoSans-Regular.ttf"
    return ImageFont.truetype(str(FONTS / name), size)


def _wrap(text: str, font: ImageFont.FreeTypeFont, width: int, lines: int) -> list[str]:
    """Words in lines no wider than the panel; the last line ends with an ellipsis if cut."""
    out: list[str] = []
    current = ""
    for word in text.split():
        trial = f"{current} {word}".strip()
        if font.getlength(trial) <= width or not current:
            current = trial
            continue
        out.append(current)
        current = word
    if current:
        out.append(current)
    if len(out) > lines:
        out = out[:lines]
        out[-1] = out[-1].rstrip(".,") + "\N{HORIZONTAL ELLIPSIS}"
    return out


def _photo(photo: bytes | None, name: str) -> Image.Image:
    if photo:
        try:
            with Image.open(io.BytesIO(photo)) as image:
                return ImageOps.fit(image.convert("RGB"), (PHOTO, PHOTO))
        except OSError:
            pass  # an unreadable photo: the initial instead
    square = Image.new("RGB", (PHOTO, PHOTO), (26, 44, 102))
    draw = ImageDraw.Draw(square)
    initial = (name.strip()[:1] or "?").upper()
    font = _font(initial, 260, bold=True)
    draw.text((PHOTO / 2, PHOTO / 2), initial, font=font, fill=SAFFRON, anchor="mm")
    return square


def _line(
    draw: ImageDraw.ImageDraw,
    y: float,
    text: str,
    font: ImageFont.FreeTypeFont,
    fill: tuple[int, int, int],
    *,
    rtl: bool,
) -> None:
    """One line in the text panel, from its right edge on a right-to-left card."""
    if rtl:
        draw.text((SIZE[0] - PAD, y), text, font=font, fill=fill, anchor="ra")
    else:
        draw.text((PHOTO + PAD, y), text, font=font, fill=fill, anchor="la")


def card_png(tenant: Tenant, product: Product, photo: bytes | None) -> bytes:
    """The picture a shared product link shows: its photo, name, price and the shop."""
    card = Image.new("RGB", SIZE, INK)
    card.paste(_photo(photo, product.name), (0, 0))
    draw = ImageDraw.Draw(card)
    width = SIZE[0] - PHOTO - 2 * PAD
    rtl = _rtl(product.name)
    shop_font = _font(tenant.name, 30)
    _line(draw, PAD, _wrap(tenant.name, shop_font, width, 1)[0], shop_font, MUTED, rtl=rtl)
    name_font = _font(product.name, 58, bold=True)
    y = PAD + 70
    for line in _wrap(product.name, name_font, width, MAX_NAME_LINES):
        _line(draw, y, line, name_font, ON_INK, rtl=rtl)
        y += 72
    price = shop_money(tenant, product.price, "en")
    _line(draw, y + 24, price, _font(price, 56, bold=True), SAFFRON, rtl=rtl)
    footer = t("share.order_here", tenant.default_lang)
    footer_font = _font(footer, 28)
    footer_lines = _wrap(footer, footer_font, width, 2)
    y = SIZE[1] - PAD - FOOTER_LINE * len(footer_lines)
    for line in footer_lines:
        _line(draw, y, line, footer_font, MUTED, rtl=rtl)
        y += FOOTER_LINE
    out = io.BytesIO()
    card.save(out, "PNG", optimize=True)
    return out.getvalue()


def page(tenant: Tenant, product: Product, *, share_url: str, card_url: str, shop_url: str) -> str:
    """The small page a shared link opens: tags for previews, then on to the shop page."""
    lang = tenant.default_lang
    title = t("share.title", lang, product=product.name, shop=tenant.name)
    price = shop_money(tenant, product.price, lang)
    description = " ".join(product.description.split())[:DESCRIPTION_CHARS] or t(
        "share.description", lang, price=price, shop=tenant.name
    )
    tags = {
        "og:type": "product",
        "og:title": title,
        "og:description": description,
        "og:url": share_url,
        "og:image": card_url,
        "og:image:width": str(SIZE[0]),
        "og:image:height": str(SIZE[1]),
        "og:site_name": tenant.name,
    }
    twitter = {
        "twitter:card": "summary_large_image",
        "twitter:title": title,
        "twitter:description": description,
        "twitter:image": card_url,
    }
    metas = "\n".join(
        [f'<meta property="{k}" content="{escape(v)}" />' for k, v in tags.items()]
        + [f'<meta name="{k}" content="{escape(v)}" />' for k, v in twitter.items()]
    )
    target = escape(shop_url)
    return f"""<!doctype html>
<html lang="{escape(lang)}">
<head>
<meta charset="utf-8" />
<meta name="viewport" content="width=device-width, initial-scale=1" />
<title>{escape(title)}</title>
<meta name="description" content="{escape(description)}" />
<link rel="canonical" href="{escape(share_url)}" />
{metas}
<meta http-equiv="refresh" content="0; url={target}" />
</head>
<body>
<p><a href="{target}">{escape(title)}: {escape(price)}</a></p>
</body>
</html>
"""
