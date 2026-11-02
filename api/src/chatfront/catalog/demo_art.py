"""Pictures for the demo shops' products, drawn by code (no stock photos, nothing to license):
a lawn suit, a khaddar suit, a dupatta and a kurta, each in its own colour and print, so the
catalog cards in WhatsApp, Instagram, Messenger and the shop page show something real-looking.
A shop's own products always use the owner's photos instead.
"""

from __future__ import annotations

import io
import math
import random
from collections.abc import Sequence
from functools import lru_cache

from PIL import Image, ImageDraw, ImageFilter

SIZE = (800, 1000)
SCALE = 2  # drawn twice as large, then reduced, for smooth edges

COLOURS = {
    "sea green": (46, 139, 120),
    "rust": (183, 80, 44),
    "black": (34, 34, 38),
    "off-white": (238, 230, 214),
    "maroon": (120, 30, 48),
    "mustard": (214, 160, 40),
    "beige": (214, 196, 160),
    "navy": (34, 48, 92),
}
BACKGROUND = ((246, 240, 232), (232, 222, 210))


def _mix(a: tuple[int, int, int], b: tuple[int, int, int], t: float) -> tuple[int, int, int]:
    return (
        round(a[0] + (b[0] - a[0]) * t),
        round(a[1] + (b[1] - a[1]) * t),
        round(a[2] + (b[2] - a[2]) * t),
    )


def _lighter(c: tuple[int, int, int], t: float) -> tuple[int, int, int]:
    return _mix(c, (255, 255, 255), t)


def _darker(c: tuple[int, int, int], t: float) -> tuple[int, int, int]:
    return _mix(c, (0, 0, 0), t)


def _canvas() -> tuple[Image.Image, ImageDraw.ImageDraw]:
    w, h = SIZE[0] * SCALE, SIZE[1] * SCALE
    image = Image.new("RGB", (w, h))
    draw = ImageDraw.Draw(image)
    for y in range(h):  # a soft vertical gradient, like a studio backdrop
        draw.line([(0, y), (w, y)], fill=_mix(BACKGROUND[0], BACKGROUND[1], y / h))
    shadow = Image.new("L", (w, h), 0)
    ImageDraw.Draw(shadow).ellipse([w * 0.2, h * 0.88, w * 0.8, h * 0.95], fill=90)
    image.paste((150, 130, 115), mask=shadow.filter(ImageFilter.GaussianBlur(30 * SCALE)))
    return image, ImageDraw.Draw(image)


def _pts(points: Sequence[tuple[float, float]]) -> list[tuple[float, float]]:
    return [(x * SCALE, y * SCALE) for x, y in points]


def _print(
    image: Image.Image, shape: Sequence[tuple[float, float]], base: tuple[int, int, int], seed: int
) -> None:
    """Fill a garment shape with its colour and a small block print of flowers and dots."""
    mask = Image.new("L", image.size, 0)
    ImageDraw.Draw(mask).polygon(_pts(shape), fill=255)
    layer = Image.new("RGB", image.size, base)
    draw = ImageDraw.Draw(layer)
    rng = random.Random(seed)  # noqa: S311 - a print pattern, not a secret
    accent, light = _lighter(base, 0.55), _darker(base, 0.25)
    step = 46 * SCALE
    for row, y in enumerate(range(0, image.size[1], step)):
        for x in range(-step, image.size[0], step):
            cx = x + (step // 2 if row % 2 else 0) + rng.randint(-4, 4)
            cy = y + rng.randint(-4, 4)
            r = 7 * SCALE
            for k in range(5):  # a five-petal flower
                a = k * 2 * math.pi / 5
                px, py = cx + math.cos(a) * r, cy + math.sin(a) * r
                draw.ellipse([px - r * 0.7, py - r * 0.7, px + r * 0.7, py + r * 0.7], fill=accent)
            draw.ellipse([cx - r * 0.5, cy - r * 0.5, cx + r * 0.5, cy + r * 0.5], fill=light)
            dx, dy = cx + step // 2, cy + step // 2
            draw.ellipse(
                [dx - 3 * SCALE, dy - 3 * SCALE, dx + 3 * SCALE, dy + 3 * SCALE], fill=light
            )
    image.paste(layer, mask=mask)


def _weave(
    image: Image.Image, shape: Sequence[tuple[float, float]], base: tuple[int, int, int]
) -> None:
    """A plain heavy weave for khaddar and linen: fine crossing lines."""
    mask = Image.new("L", image.size, 0)
    ImageDraw.Draw(mask).polygon(_pts(shape), fill=255)
    layer = Image.new("RGB", image.size, base)
    draw = ImageDraw.Draw(layer)
    line = _darker(base, 0.08)
    for i in range(0, image.size[0] + image.size[1], 6 * SCALE):
        draw.line([(i, 0), (i - image.size[1], image.size[1])], fill=line, width=SCALE)
    image.paste(layer, mask=mask)


def _outline(
    draw: ImageDraw.ImageDraw, shape: Sequence[tuple[float, float]], colour: tuple[int, int, int]
) -> None:
    draw.line([*_pts(shape), _pts(shape)[0]], fill=_darker(colour, 0.35), width=3 * SCALE)


KAMEEZ = [
    (330, 150),
    (370, 135),
    (400, 165),
    (430, 135),
    (470, 150),  # neckline
    (585, 205),
    (650, 420),
    (600, 440),
    (545, 290),  # right sleeve
    (560, 700),
    (240, 700),
    (255, 290),  # hem and left side
    (200, 440),
    (150, 420),
    (215, 205),  # left sleeve
]
TROUSER = [(285, 700), (515, 700), (520, 880), (430, 880), (400, 760), (370, 880), (280, 880)]
DUPATTA = [(215, 205), (330, 150), (300, 230), (250, 470), (205, 760), (150, 750), (175, 480)]
KURTA = [
    (335, 140),
    (370, 130),
    (400, 150),
    (430, 130),
    (465, 140),
    (590, 200),
    (640, 470),
    (590, 485),
    (545, 300),
    (550, 820),
    (250, 820),
    (255, 300),
    (210, 485),
    (160, 470),
    (210, 200),
]
SCARF = [(250, 140), (550, 140), (600, 820), (200, 820)]


def _suit(colour: tuple[int, int, int], seed: int, printed: bool) -> Image.Image:
    image, draw = _canvas()
    trouser = _darker(colour, 0.15) if printed else _lighter(colour, 0.15)
    _weave(image, TROUSER, trouser)
    _outline(draw, TROUSER, trouser)
    (_print if printed else _weave)(image, KAMEEZ, colour, *((seed,) if printed else ()))
    _outline(draw, KAMEEZ, colour)
    if printed:
        dupatta = _lighter(colour, 0.35)
        _print(image, DUPATTA, dupatta, seed + 1)
        _outline(draw, DUPATTA, dupatta)
    return image


def _dupatta(colour: tuple[int, int, int]) -> Image.Image:
    image, draw = _canvas()
    _weave(image, SCARF, colour)
    border = (212, 175, 55)  # gold embroidery along both edges
    for y in range(160, 800, 22):
        for x in (255 + (y - 140) * 50 / 680, 545 + (y - 140) * 50 / 680):
            draw.ellipse(_pts([(x - 6, y - 6), (x + 6, y + 6)]), fill=border)
    _outline(draw, SCARF, colour)
    return image


def _kurta(colour: tuple[int, int, int]) -> Image.Image:
    image, draw = _canvas()
    _weave(image, KURTA, colour)
    _outline(draw, KURTA, colour)
    for y in (180, 220, 260):  # placket buttons
        draw.ellipse(_pts([(394, y), (406, y + 12)]), fill=_darker(colour, 0.4))
    return image


def picture(name: str, category: str | None, colour: str | None, seed: int = 0) -> bytes:
    """A PNG for a demo product, from its name, category and first colour."""
    tone = COLOURS.get((colour or "").casefold())
    lowered = name.casefold()
    if "dupatta" in lowered or category == "dupattas":
        image = _dupatta(tone or COLOURS["black"])
    elif "kurta" in lowered or category == "kurtas":
        image = _kurta(tone or COLOURS["beige"])
    elif "khaddar" in lowered:
        image = _suit(tone or COLOURS["maroon"], seed, printed=False)
    else:
        image = _suit(tone or COLOURS["sea green"], seed, printed=True)
    out = io.BytesIO()
    image.resize(SIZE, Image.Resampling.LANCZOS).save(out, "PNG", optimize=True)
    return out.getvalue()


ART_TEMPLATES = frozenset({"clothing"})  # the demo shops whose products have drawn pictures


def has_art(source: str, template: str, has_photo: bool) -> bool:
    """A demo product with no photo of its own gets a drawn one."""
    return not has_photo and source == "demo" and template in ART_TEMPLATES


@lru_cache(maxsize=64)
def cached(name: str, category: str | None, colour: str | None) -> bytes:
    return picture(name, category, colour, seed=len(name))


def photo_path(slug: str, code: str) -> str:
    """The stable photo link: the stored photo, or the drawn one for a demo product."""
    return f"/api/shops/{slug}/products/{code}/photo"
