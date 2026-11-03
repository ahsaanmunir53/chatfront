"""The store in the chat: what "menu" brings, how a product card reads, and the drawn demo
pictures; and the picture card on each channel."""

from __future__ import annotations

import uuid

import pytest

from chatfront.catalog import demo_art
from chatfront.catalog.models import Product, Variant
from chatfront.catalog.service import CatalogItem
from chatfront.channels.messaging import messenger_adapter
from chatfront.channels.types import OutImage, OutList, OutText, to_record
from chatfront.channels.whatsapp import _body
from chatfront.core.i18n import t
from chatfront.core.models import Tenant
from chatfront.orders import browse

SHOP = Tenant(slug="gulmohar", name="Gulmohar", template="clothing", country="PK", currency="PKR")


def item(name: str, category: str | None, *colours: str, source: str = "demo") -> CatalogItem:
    product = Product(
        id=uuid.uuid4(), code=name[:3].upper(), name=name, category=category, price=485_000,
        source=source, image_media_id=None,
    )  # fmt: skip
    variants = [
        Variant(product_id=product.id, options={"colour": c}, price=None, active=True, stock=5)
        for c in colours
    ]
    return CatalogItem(product=product, variants=variants)


@pytest.mark.parametrize(
    ("text", "asks"),
    [
        ("menu", True),
        ("Catalog?", True),
        ("sab dikhao", True),
        ("lawn suit hai?", False),
        ("", False),
    ],
)
def test_who_asks_for_the_catalog(text: str, asks: bool) -> None:
    assert browse.wants_catalog(text) is asks


def test_categories_then_cards() -> None:
    items = [
        item("Printed lawn", "suits", "Rust", "Sea green"),
        item("Dupatta", "dupattas", "Black"),
    ]
    [menu] = browse.categories(items, "en")
    assert isinstance(menu, OutList)
    assert [(r.id, r.title) for r in menu.rows] == [
        ("cat:dupattas", "Dupattas"),
        ("cat:suits", "Suits"),
    ]
    assert browse.parse("cat:suits") == "suits"
    assert browse.parse("ap:1:ok") is None
    first, hint = browse.category_cards("https://api.test", SHOP, items, "suits", "en")
    assert isinstance(first, OutImage)
    assert first.url == "https://api.test/api/shops/gulmohar/products/PRI/photo"
    assert "Colour: Rust, Sea green" in first.caption
    assert "Code: PRI" in first.caption
    assert isinstance(hint, OutText)
    assert isinstance(browse.category_cards("", SHOP, items, "hats", "en")[0], OutList)


def test_a_product_without_a_picture_is_a_text_card() -> None:
    own = item("Own suit", "suits", "Rust", source="console")
    assert isinstance(browse.card("https://api.test", SHOP, own, "en"), OutText)
    assert browse.categories([], "en") == [OutText(t("browse.empty", "en"))]


def test_drawn_pictures() -> None:
    png = demo_art.picture("Printed lawn 3-piece", "suits", "Rust")
    assert png.startswith(b"\x89PNG")
    assert demo_art.cached("Linen kurta", "kurtas", None).startswith(b"\x89PNG")
    assert demo_art.has_art("demo", "clothing", has_photo=False)
    assert not demo_art.has_art("demo", "clothing", has_photo=True)
    assert not demo_art.has_art("console", "clothing", has_photo=False)


def test_the_picture_card_on_each_channel() -> None:
    card = OutImage("https://x.test/p.png", "Printed lawn\nRs 4,850\nCode: PRI", "Printed lawn")
    assert _body(card) == {
        "type": "image",
        "image": {"link": "https://x.test/p.png", "caption": card.caption},
    }
    element = messenger_adapter(None, "https://graph.test", "v23.0")._message(card)  # type: ignore[arg-type]
    shown = element["attachment"]["payload"]["elements"][0]
    assert (shown["title"], shown["image_url"]) == ("Printed lawn", "https://x.test/p.png")
    assert shown["subtitle"] == "Rs 4,850 · Code: PRI"
    assert to_record(card) == (card.caption, {"image": {"url": card.url, "title": "Printed lawn"}})


def test_greeting_and_the_order_button() -> None:
    assert browse.greets("Assalam o alaikum")
    assert browse.greets("hi!")
    assert not browse.greets("hi, lawn suit hai?")
    [door] = browse.welcome(SHOP, "en")
    assert [b.id for b in door.buttons] == [browse.MENU_BUTTON, browse.ORDER_BUTTON]
    card = browse.card("https://api.test", SHOP, item("Printed lawn", "suits", "Rust"), "en")
    assert isinstance(card, OutImage)
    assert [b.id for b in card.buttons] == ["buy:PRI"]
    assert browse.bought("buy:PRI") == "PRI"
    assert browse.bought("cat:suits") is None
    interactive = _body(card)["interactive"]
    assert interactive["header"] == {"type": "image", "image": {"link": card.url}}
    assert interactive["action"]["buttons"][0]["reply"]["id"] == "buy:PRI"


def test_the_owner_admin_menu() -> None:
    from chatfront.owner.rights import Can
    from chatfront.stock import panel

    assert panel.wants_admin("Menu")
    assert not panel.wants_admin("menu dikhao kal ka")
    [menu] = panel.admin_menu(SHOP, frozenset(Can), "en")
    assert isinstance(menu, OutList)
    assert [r.id for r in menu.rows][:3] == ["adm:orders", "adm:pending_payments", "adm:today"]
    [few] = panel.admin_menu(SHOP, frozenset({Can.ORDERS}), "en")
    assert isinstance(few, OutList)
    assert [r.id for r in few.rows] == ["adm:orders"]
    assert panel.admin_choice("adm:today") == "today"
    assert panel.admin_choice("adm:nope") is None


def test_a_list_the_assistant_writes_comes_with_its_pictures() -> None:
    from chatfront.channels.models import Message

    lawn, khaddar, kurta = (
        item("Printed lawn", "suits", "Rust"),
        item("Khaddar", "suits"),
        item("Kurta", "kurtas"),
    )
    text = "1. Khaddar (KHA) - Rs 3,950\n2. Printed lawn (PRI) - Rs 4,850"
    shown = browse.mentioned("https://x", SHOP, [lawn, khaddar, kurta], text, "en")
    assert [p.title for p in shown] == ["Khaddar", "Printed lawn"]  # in the order named
    before = Message(direction="out", payload={"image": {"title": "Khaddar"}})
    again = browse.mentioned("https://x", SHOP, [lawn, khaddar], text, "en", history=[before])
    assert [p.title for p in again] == ["Printed lawn"]  # not the one already shown
    assert browse.mentioned("https://x", SHOP, [lawn], "PRIX is not a code", "en") == []
