"""A sketch of a custom order (feature 17): a cake from above with its writing, a kameez with
its colour and notes, or a printed card or shirt with its words. Muse Image would draw a real
picture and is Meta Model API only; until then code draws these plain sketches, so the customer
and the shop agree on the words, the colours and the size before a price is given."""

from __future__ import annotations

import io
import textwrap
from enum import StrEnum
from math import cos, radians, sin

from PIL import Image, ImageDraw

from chatfront.media.posters import INK, PAPER, drawable, script_font

SIDE = 1080
# Colours customers name, in English, Roman Urdu and Urdu; anything else is drawn soft grey.
COLOURS = {
    "white": (250, 250, 247),
    "safaid": (250, 250, 247),
    "سفید": (250, 250, 247),
    "pink": (244, 166, 196),
    "gulabi": (244, 166, 196),
    "گلابی": (244, 166, 196),
    "red": (214, 48, 49),
    "laal": (214, 48, 49),
    "لال": (214, 48, 49),
    "blue": (74, 144, 226),
    "neela": (74, 144, 226),
    "نیلا": (74, 144, 226),
    "green": (46, 160, 67),
    "hara": (46, 160, 67),
    "ہرا": (46, 160, 67),
    "yellow": (247, 202, 24),
    "peela": (247, 202, 24),
    "پیلا": (247, 202, 24),
    "black": (34, 34, 34),
    "kala": (34, 34, 34),
    "کالا": (34, 34, 34),
    "chocolate": (123, 63, 0),
    "brown": (123, 63, 0),
    "bhoora": (123, 63, 0),
    "purple": (142, 68, 173),
    "jamni": (142, 68, 173),
    "orange": (243, 156, 18),
    "maroon": (128, 0, 32),
    "golden": (212, 175, 55),
    "gold": (212, 175, 55),
    "silver": (192, 192, 192),
    "cream": (255, 248, 220),
    "peach": (255, 203, 164),
}
SOFT = (215, 215, 210)


class Kind(StrEnum):
    CAKE = "cake"
    STITCHING = "stitching"
    PRINT = "print"


def colour_of(name: str | None, fallback: tuple[int, int, int] = SOFT) -> tuple[int, int, int]:
    return COLOURS.get((name or "").strip().lower(), fallback)


def _contrast(fill: tuple[int, int, int]) -> tuple[int, int, int]:
    return INK if sum(fill) > 380 else PAPER


def _writing(
    draw: ImageDraw.ImageDraw, text: str, box: tuple[int, int, int, int], fill: tuple[int, int, int]
) -> None:
    """The customer's words, wrapped and centred in the box, in their own script."""
    if not text or not drawable(text):
        return
    left, top, right, bottom = box
    for size, width in ((72, 16), (56, 22), (44, 28)):
        lines = textwrap.wrap(text, width)[:4]
        font = script_font(text, size, bold=True)
        height = len(lines) * size * 1.3
        if (
            all(draw.textlength(line, font=font) <= right - left for line in lines)
            and height <= bottom - top
        ):
            break
    y = (top + bottom) / 2 - height / 2 + size * 0.65
    for line in lines:
        draw.text(((left + right) / 2, y), line, font=font, fill=fill, anchor="mm")
        y += size * 1.3


def _notes(draw: ImageDraw.ImageDraw, notes: list[str]) -> None:
    y = SIDE - 40 - 44 * len(notes)
    for note in notes:
        if drawable(note):
            draw.text((SIDE / 2, y), note, font=script_font(note, 34), fill=INK, anchor="mm")
        y += 44


def sketch(kind: Kind, text: str, colours: list[str], notes: list[str]) -> bytes:
    canvas = Image.new("RGB", (SIDE, SIDE), (247, 245, 240))
    draw = ImageDraw.Draw(canvas)
    main = colour_of(colours[0] if colours else None)
    trim = colour_of(colours[1] if len(colours) > 1 else None, _contrast(main))
    if kind is Kind.CAKE:
        draw.ellipse((150, 120, 930, 900), fill=trim)
        draw.ellipse((180, 150, 900, 870), fill=main)
        for angle in range(0, 360, 20):  # piping around the edge
            x = 540 + 375 * cos(radians(angle))
            y = 510 + 375 * sin(radians(angle))
            draw.ellipse((x - 16, y - 16, x + 16, y + 16), fill=trim)
        _writing(draw, text, (260, 330, 820, 690), _contrast(main))
    elif kind is Kind.STITCHING:
        body = [
            (380, 160),
            (460, 130),
            (540, 170),
            (620, 130),
            (700, 160),
            (860, 330),
            (780, 400),
            (720, 330),
            (740, 880),
            (340, 880),
            (360, 330),
            (300, 400),
            (220, 330),
        ]
        draw.polygon(body, fill=main, outline=INK)
        draw.arc((470, 120, 610, 230), 0, 180, fill=trim, width=10)  # the neckline
        _writing(draw, text, (390, 420, 690, 760), _contrast(main))
    else:
        draw.rounded_rectangle((140, 200, 940, 820), radius=28, fill=main, outline=trim, width=18)
        _writing(draw, text, (200, 260, 880, 760), _contrast(main))
    _notes(draw, notes[:3])
    out = io.BytesIO()
    canvas.save(out, "PNG", optimize=True)
    return out.getvalue()
