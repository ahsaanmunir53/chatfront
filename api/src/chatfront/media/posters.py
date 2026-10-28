"""Images for the catalog and for posts, drawn by code from the product's own photo (features 3,
7 and 82).

Muse Image and SAM would cut the product out and set it in a scene; both are Meta Model API
only, out of reach from Pakistan. Until then `TemplateImages` stands in behind the same
interface: it cleans the owner's photo (turned upright, colour cast taken out, levels set,
squared, sharpened) and sets it on a card in the shop's colour with its name and price, square
for the catalog, 4:5 for an Instagram post and 9:16 for a WhatsApp Status. Nothing is invented:
the product in the picture is the owner's own photo.
"""

from __future__ import annotations

import io
from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol

from PIL import Image, ImageDraw, ImageFilter, ImageFont, ImageOps, ImageStat, features

from chatfront.delivery.labels import FONTS

QUALITY = 88
SIDE = 1080
# The shop's colour by its business template, as on the web app's avatars.
TONES = {
    "clothing": (244, 183, 64),
    "electronics": (90, 176, 240),
    "food": (239, 107, 91),
    "grocery": (61, 187, 127),
    "services": (168, 121, 214),
    "wholesale": (43, 179, 177),
}
INK = (15, 30, 74)
PAPER = (255, 255, 255)
_SHAPED = features.check("raqm")  # right-to-left and joined scripts need text shaping
_MAX_CAST = 1.25  # never pull a channel further than this when taking out a colour cast


class Shape(StrEnum):
    SQUARE = "square"  # the catalog and a Facebook post
    POST = "post"  # an Instagram post, 4:5
    STATUS = "status"  # a WhatsApp Status or a story, 9:16


SIZES = {
    Shape.SQUARE: (SIDE, SIDE),
    Shape.POST: (SIDE, 1350),
    Shape.STATUS: (SIDE, 1920),
}


@dataclass(frozen=True, slots=True)
class Card:
    shop: str
    name: str
    price: str  # already in the shop's money, e.g. "Rs 4,850"
    template: str
    badge: str | None = None  # "New", "Sale", in the shop's language


class ImageMaker(Protocol):
    def clean(self, photo: bytes) -> bytes: ...

    def card(self, photo: bytes, card: Card, shape: Shape) -> bytes: ...


def _jpeg(image: Image.Image) -> bytes:
    out = io.BytesIO()
    image.convert("RGB").save(out, "JPEG", quality=QUALITY, optimize=True)
    return out.getvalue()


def _balanced(image: Image.Image) -> Image.Image:
    """Grey-world white balance: each channel pulled toward the photo's average grey, within
    limits, so a yellow bulb or a blue shade does not colour the product."""
    means = ImageStat.Stat(image).mean[:3]
    grey = sum(means) / 3
    factors = [min(max(grey / m, 1 / _MAX_CAST), _MAX_CAST) if m else 1.0 for m in means]
    channels = [
        band.point(lambda value, f=factor: min(255, round(value * f)))
        for band, factor in zip(image.split(), factors, strict=True)
    ]
    return Image.merge("RGB", channels)


def cleaned(photo: bytes) -> Image.Image:
    with Image.open(io.BytesIO(photo)) as raw:
        image = ImageOps.exif_transpose(raw).convert("RGB")
    image = ImageOps.autocontrast(_balanced(image), cutoff=1)
    image = ImageOps.fit(image, (SIDE, SIDE), Image.Resampling.LANCZOS)
    return image.filter(ImageFilter.UnsharpMask(radius=2, percent=60, threshold=3))


def _script_font(text: str, size: int, *, bold: bool = False) -> ImageFont.FreeTypeFont:
    """A bundled Noto font that has the text's script."""
    name = "NotoSans-Bold.ttf" if bold else "NotoSans-Regular.ttf"
    for char in text:
        code = ord(char)
        if 0x0600 <= code <= 0x06FF or 0x0750 <= code <= 0x077F or 0xFB50 <= code <= 0xFEFF:
            name = "NotoNaskhArabic-Bold.ttf" if bold else "NotoNaskhArabic-Regular.ttf"
            break
        if 0x0900 <= code <= 0x097F:
            name = "NotoSansDevanagari-Regular.ttf"
            break
        if 0x0980 <= code <= 0x09FF:
            name = "NotoSansBengali-Regular.ttf"
            break
    layout = ImageFont.Layout.RAQM if _SHAPED else ImageFont.Layout.BASIC
    return ImageFont.truetype(str(FONTS / name), size, layout_engine=layout)


def drawable(text: str) -> bool:
    """Text this machine can draw correctly: any script with shaping, else Latin only."""
    return _SHAPED or all(ord(char) < 0x0590 for char in text)


def _fitted(
    draw: ImageDraw.ImageDraw, text: str, width: int, size: int, *, bold: bool
) -> tuple[ImageFont.FreeTypeFont, str]:
    """The largest font, down to two thirds of `size`, that fits the text; then the text cut."""
    for step in range(size, size * 2 // 3, -4):
        font = _script_font(text, step, bold=bold)
        if draw.textlength(text, font=font) <= width:
            return font, text
    font = _script_font(text, size * 2 // 3, bold=bold)
    while text and draw.textlength(text + "…", font=font) > width:
        text = text[:-1]
    return font, text.rstrip() + "…"


def _tint(tone: tuple[int, int, int], share: float) -> tuple[int, int, int]:
    return tuple(round(c * share + 255 * (1 - share)) for c in tone)  # type: ignore[return-value]


class TemplateImages:
    """The stand-in for Muse Image: the owner's photo, cleaned, on a card."""

    def clean(self, photo: bytes) -> bytes:
        return _jpeg(cleaned(photo))

    def card(self, photo: bytes, card: Card, shape: Shape) -> bytes:
        width, height = SIZES[shape]
        tone = TONES.get(card.template, INK)
        canvas = Image.new("RGB", (width, height), _tint(tone, 0.22))
        draw = ImageDraw.Draw(canvas)
        margin = 72
        frame = width - 2 * margin
        band = 300 if shape is Shape.SQUARE else 360
        top = margin if shape is not Shape.STATUS else 220
        side = min(frame, height - band - top - margin // 2)
        picture = cleaned(photo).resize((side, side), Image.Resampling.LANCZOS)
        left = (width - side) // 2
        draw.rounded_rectangle(
            (left - 14, top - 14, left + side + 14, top + side + 14), radius=36, fill=PAPER
        )
        mask = Image.new("L", (side, side), 0)
        ImageDraw.Draw(mask).rounded_rectangle((0, 0, side, side), radius=26, fill=255)
        canvas.paste(picture, (left, top), mask)
        if card.badge and drawable(card.badge):
            font = _script_font(card.badge, 44, bold=True)
            pad = 22
            label = draw.textlength(card.badge, font=font)
            box = (left + 28, top + 28, left + 28 + label + 2 * pad, top + 28 + 76)
            draw.rounded_rectangle(box, radius=38, fill=tone)
            draw.text((box[0] + pad, box[1] + 38), card.badge, font=font, fill=INK, anchor="lm")
        y = top + side + 14 + (height - top - side - 14) // 2 - 70
        if drawable(card.name):
            font, name = _fitted(draw, card.name, frame, 64, bold=True)
            draw.text((width // 2, y), name, font=font, fill=INK, anchor="mm")
        price_font = _script_font(card.price, 84, bold=True)
        draw.text((width // 2, y + 96), card.price, font=price_font, fill=INK, anchor="mm")
        if drawable(card.shop):
            font, shop = _fitted(draw, card.shop, frame, 40, bold=False)
            draw.text(
                (width // 2, height - margin // 2 - 20), shop, font=font, fill=INK, anchor="mm"
            )
        return _jpeg(canvas)


def image_maker() -> ImageMaker:
    """Muse Image takes this place when the Model API is reachable."""
    return TemplateImages()
