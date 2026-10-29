"""Reading what a shop already has, by code (features 85, 84, 2 and 87): sheet columns and
rows, Meta catalog items, Instagram captions, products seen in a video and a shelf count."""

from __future__ import annotations

import uuid
from decimal import Decimal
from typing import ClassVar

import pytest

from chatfront.catalog import instagram_import, meta_import, sheet_import, video_import
from chatfront.catalog.drafts import DraftOption
from chatfront.catalog.imports import from_meta
from chatfront.catalog.models import Product, Variant
from chatfront.catalog.service import CatalogItem
from chatfront.core.errors import AppError
from chatfront.stock import count
from chatfront.templates import get_template

CLOTHES = get_template("clothing")
# A catalog as Meta Commerce Manager exports it.
EXPORT = [
    [
        "id",
        "title",
        "description",
        "availability",
        "condition",
        "price",
        "image_link",
        "item_group_id",
        "color",
        "size",
        "fabric",
    ],
    [
        "L1",
        "Printed lawn 3-piece",
        "Unstitched",
        "in stock",
        "new",
        "4850.00 PKR",
        "x",
        "G1",
        "Rust",
        "",
        "lawn",
    ],
    [
        "L2",
        "Printed lawn 3-piece",
        "",
        "in stock",
        "new",
        "4850.00 PKR",
        "x",
        "G1",
        "Sea green",
        "",
        "lawn",
    ],
    ["K1", "Khaddar shawl", "Warm", "in stock", "new", "PKR 2,900", "x", "", "", "", "khaddar"],
    ["", "", "", "", "", "", "", "", "", "", ""],
    ["N1", "Velvet clutch", "", "in stock", "new", "", "x", "", "", "", ""],
]


def test_meta_photo_url_rejects_nonstandard_ports_and_userinfo() -> None:
    assert from_meta("https://lookaside.fbsbx.com/photo.jpg")
    assert from_meta("https://cdninstagram.com/photo.jpg")
    assert not from_meta("https://lookaside.fbsbx.com:8443/photo.jpg")
    assert not from_meta("https://user:pass@lookaside.fbsbx.com/photo.jpg")
    assert not from_meta("http://lookaside.fbsbx.com/photo.jpg")
    assert not from_meta("https://lookaside.fbsbx.com.evil.example/photo.jpg")


class TestSheets:
    def test_an_export_from_commerce_manager_reads_by_its_headers(self) -> None:
        columns = sheet_import.keyword_columns(EXPORT[0], CLOTHES)
        assert (columns.name, columns.price, columns.group) == ("title", "price", "item_group_id")
        assert columns.description == "description"
        assert {(c.key, c.header) for c in columns.fields} == {
            ("colour", "color"),
            ("size", "size"),
            ("fabric", "fabric"),
        }

    def test_a_shops_own_sheet_in_urdu(self) -> None:
        columns = sheet_import.keyword_columns(["نام", "قیمت", "تعداد", "رنگ"], CLOTHES)
        assert (columns.name, columns.price, columns.stock) == ("نام", "قیمت", "تعداد")
        assert [(c.key, c.header) for c in columns.fields] == [("colour", "رنگ")]

    def test_a_name_and_a_price_are_needed(self) -> None:
        guess = sheet_import.keyword_columns(["Item", "Qty"], CLOTHES)
        with pytest.raises(AppError) as caught:
            sheet_import.checked(guess, ["Item", "Qty"], CLOTHES)
        assert caught.value.code == "no_columns"
        made_up = sheet_import.ProductColumns(
            name="Item", price="Rate", fields=[sheet_import.FieldColumn(key="mood", header="Qty")]
        )
        with pytest.raises(AppError):
            sheet_import.checked(made_up, ["Item", "Qty"], CLOTHES)

    def test_rows_of_one_product_become_its_colours(self) -> None:
        columns = sheet_import.checked(
            sheet_import.keyword_columns(EXPORT[0], CLOTHES), EXPORT[0], CLOTHES
        )
        found = sheet_import.to_found(EXPORT, columns, uuid.uuid4())
        lawn, shawl, clutch = (one.draft for one in found)
        assert (lawn.name, lawn.price, lawn.description) == (
            "Printed lawn 3-piece",
            4850.0,
            "Unstitched",
        )
        assert lawn.attributes == [DraftOption(key="fabric", value="lawn")]
        assert [v.options for v in lawn.variants] == [
            [DraftOption(key="colour", value="Rust")],
            [DraftOption(key="colour", value="Sea green")],
        ]
        assert (shawl.price, shawl.variants, shawl.missing) == (2900.0, [], [])
        assert (clutch.price, clutch.missing) == (None, ["price"])
        assert {one.price_from for one in found} == {"sheet"}

    def test_a_different_price_and_stock_per_size(self) -> None:
        rows = [
            ["Name", "Price", "Size", "Stock"],
            ["Kurta", "2750", "M", "3"],
            ["Kurta", "2950", "XL", "1"],
            ["Shawl", "1800", "", "5"],
        ]
        columns = sheet_import.checked(
            sheet_import.keyword_columns(rows[0], CLOTHES), rows[0], CLOTHES
        )
        kurta, shawl = (one.draft for one in sheet_import.to_found(rows, columns, uuid.uuid4()))
        assert kurta.price == 2750.0
        assert [(v.price, v.stock) for v in kurta.variants] == [(None, 3), (2950.0, 1)]
        assert (shawl.stock, shawl.variants) == (5, [])


class TestMetaCatalog:
    ITEMS: ClassVar[list[dict[str, object]]] = [
        {
            "retailer_id": "A-1",
            "name": "Silk scarf",
            "price": "Rs2,200.00",
            "color": "Red",
            "retailer_product_group_id": "A",
            "inventory": 4,
        },
        {
            "retailer_id": "A-2",
            "name": "Silk scarf",
            "price": "Rs2,400.00",
            "color": "Blue",
            "retailer_product_group_id": "A",
        },
        {"retailer_id": "B", "name": "Gift box", "price": "", "category": "Gifts"},
        {"retailer_id": "OURS1", "name": "Ours", "price": "Rs10.00"},
        {"retailer_id": "", "name": "No id"},
    ]

    def test_groups_become_products_and_ours_stay_out(self) -> None:
        groups = meta_import.groups_of(self.ITEMS, {"OURS1"})
        assert list(groups) == ["A", "B"]
        scarf = meta_import.draft_of(groups["A"])
        assert (scarf.name, scarf.price) == ("Silk scarf", 2200.0)
        assert [(v.options[0].value, v.price, v.stock) for v in scarf.variants] == [
            ("Red", None, 4),
            ("Blue", 2400.0, None),
        ]
        box = meta_import.draft_of(groups["B"])
        assert (box.price, box.category, box.variants, box.missing) == (
            None,
            "gifts",
            [],
            ["price"],
        )

    def test_photos_only_from_meta(self) -> None:
        assert from_meta("https://scontent.cdninstagram.com/v/a.jpg")
        assert from_meta("https://scontent-lhr8-1.xx.fbcdn.net/a.jpg")
        assert not from_meta("http://scontent.cdninstagram.com/a.jpg")
        assert not from_meta("https://fbcdn.net.evil.example/a.jpg")
        assert not from_meta("https://169.254.169.254/latest")
        assert not from_meta(None)


class TestInstagramCaptions:
    def test_the_price_is_the_number_next_to_a_price_word(self) -> None:
        caption = "🌸 Printed lawn 3-piece #lawn @gulmohar\nRs. 4,850 only\nOrder: 0300-1234567"
        assert instagram_import.caption_price(caption) == Decimal(4850)
        assert instagram_import.caption_price("Dupatta 2200/-") == Decimal(2200)
        assert instagram_import.caption_price("Call 03001234567") is None
        reading = instagram_import.caption_reading(caption, "en")
        assert reading.is_product
        assert reading.product is not None
        assert (reading.product.name, reading.product.price) == ("Printed lawn 3-piece", 4850.0)
        assert reading.price_seen == "caption"

    def test_a_post_without_a_price_is_not_taken_for_a_product(self) -> None:
        assert not instagram_import.caption_reading("Eid Mubarak to all! #eid", "en").is_product
        assert not instagram_import.caption_reading("Rs 500", "en").is_product

    def test_a_reels_picture_is_its_cover(self) -> None:
        assert instagram_import.picture_of({"media_type": "VIDEO", "thumbnail_url": "t"}) == "t"
        assert instagram_import.picture_of({"media_type": "IMAGE", "media_url": "m"}) == "m"
        assert instagram_import.picture_of({"media_type": "CAROUSEL_ALBUM"}) is None


def seen(
    name: str, colour: str | None, second: float, price: float | None = None
) -> video_import.SeenProduct:
    return video_import.SeenProduct(
        name=name,
        description=f"A {name.lower()}",
        category="suits",
        tags=[],
        attributes=[],
        colour=colour,
        size=None,
        at_second=second,
        price=price,
        price_seen="said" if price else None,
    )


class TestVideo:
    def test_the_same_design_in_other_colours_is_one_product(self) -> None:
        groups = video_import.grouped(
            [
                seen("Lawn suit", "Rust", 1.0),
                seen("Velvet shawl", None, 3.0, 3500),
                seen("lawn  suit", "Teal", 5.5),
            ]
        )
        assert [[one.at_second for one in group] for group in groups] == [[1.0, 5.5], [3.0]]
        lawn, lawn_from = video_import.draft_of(groups[0])
        assert (lawn.price, lawn_from, lawn.missing) == (None, None, ["price"])
        assert [v.options[0].value for v in lawn.variants] == ["Rust", "Teal"]
        shawl, shawl_from = video_import.draft_of(groups[1])
        assert (shawl.price, shawl_from, shawl.variants) == (3500, "said", [])


def _item(name: str, options: list[dict[str, str]], stock: int | None = 4) -> CatalogItem:
    product = Product(
        id=uuid.uuid4(), code=name[:5].upper(), name=name, price=100, tags=[], translations={}
    )
    variants = [
        Variant(
            id=uuid.uuid4(), product_id=product.id, options=o, stock=stock, active=True, position=i
        )
        for i, o in enumerate(options)
    ]
    return CatalogItem(product, variants)


class TestShelfCount:
    ITEMS: ClassVar[list[CatalogItem]] = [
        _item("Printed lawn 3-piece", [{"colour": "Rust"}, {"colour": "Sea green"}]),
        _item("Linen kurta", [{}]),
    ]

    def test_counts_match_variants_and_add_up(self) -> None:
        lines = [
            count.CountLine(item="Printed lawn 3-piece Rust", count=2),
            count.CountLine(item="printed lawn 3 piece rust", count=1),
            count.CountLine(item="Linen kurta", count=7),
            count.CountLine(item="Linen kurta", count=None),
            count.CountLine(item="Velvet clutch", count=1),
            count.CountLine(item="Printed lawn 3-piece", count=1),
        ]
        ops, problems = count.resolve_counts(lines, self.ITEMS)
        assert [(op["name"], op["before"], op["counted"]) for op in ops] == [
            ("Printed lawn 3-piece (Rust)", 4, 3),
            ("Linen kurta", 4, 7),
        ]
        assert [p[0] for p in problems] == [
            "stock.need_count",
            "stock.not_in_shop",
            "stock.which_variant",
        ]
        text = count.card_text(ops, "en")
        assert "3" in text
        assert "7" in text
        assert count.card_line({**ops[0], "counted": 4}, "en") != count.card_line(ops[0], "en")

    def test_count_words(self) -> None:
        assert count.is_count("stock count kar do")
        assert count.is_count("shelf ki ginti")
        assert not count.is_count("new stock aya hai")
