"""The owner side's pure logic: drafts checked against templates, changes resolved to exact
products, signed sessions, onboarding answers, and the cards the owner reads."""

from __future__ import annotations

import uuid
from datetime import timedelta
from decimal import Decimal

import pytest

from chatfront.catalog.drafts import (
    EMPTY_DRAFT,
    Change,
    DraftOption,
    DraftVariant,
    ProductDraft,
    complete_draft,
    name_from_words,
    price_from_words,
    to_input,
    to_minor,
)
from chatfront.catalog.models import Product
from chatfront.catalog.service import CatalogItem, ProductInput, VariantInput
from chatfront.core.models import Tenant
from chatfront.core.money import Minor
from chatfront.owner.actions import parse_button
from chatfront.owner.assistant import resolve_changes, typed_price_change
from chatfront.owner.cards import change_line, product_summary
from chatfront.owner.onboarding import merge, next_step, read_by_words
from chatfront.owner.sessions import NotSignedIn, console_link, issue, verify
from chatfront.templates import get_template

CLOTHING = get_template("clothing")
FOOD = get_template("food")
SHOP = Tenant(name="Gulmohar", slug="gulmohar", template="clothing", country="PK", profile={})


def draft(**fields: object) -> ProductDraft:
    return EMPTY_DRAFT.model_copy(update={"missing": [], **fields})


def item(name: str, code: str, price: int, floor: int | None = None) -> CatalogItem:
    product = Product(
        id=uuid.uuid4(), name=name, code=code, price=price, floor_price=floor, translations={}
    )
    return CatalogItem(product, [])


ITEMS = [
    item("Printed lawn 3-piece", "LAWN3", 485_000, 450_000),
    item("Lawn 2-piece plain", "LAWN2", 320_000),
    item("Linen kurta", "KRT01", 275_000),
]


class TestDrafts:
    def test_template_fields_and_options_are_enforced(self) -> None:
        read = draft(
            name="Khaddar suit",
            price=3950.0,
            attributes=[
                DraftOption(key="fabric", value="KHADDAR"),
                DraftOption(key="fabric_weight", value="heavy"),  # not a template field
                DraftOption(key="pieces", value="7"),  # not an allowed option
            ],
            variants=[
                DraftVariant(options=[DraftOption(key="size", value="m")], price=None, stock=5),
                DraftVariant(options=[DraftOption(key="size", value="L")], price=4100.0, stock=-1),
            ],
            tags=["Winter", "winter", " "],
        )
        product = to_input(read, CLOTHING, 2, source="test", image_media_id=None)
        assert product is not None
        assert product.price == 395_000
        assert product.attributes == {"fabric": "khaddar"}
        assert product.variants == (
            VariantInput({"size": "M"}, None, 5),
            VariantInput({"size": "L"}, Minor(410_000), None),
        )
        assert product.tags == ("winter",)

    def test_number_fields_keep_only_numbers(self) -> None:
        read = draft(
            name="Biryani",
            price=1800.0,
            attributes=[
                DraftOption(key="prep_minutes", value="about 45 minutes"),
                DraftOption(key="spice", value="Medium"),
            ],
        )
        product = to_input(read, FOOD, 2, source="test", image_media_id=None)
        assert product is not None
        assert product.attributes == {"prep_minutes": "45", "spice": "medium"}

    def test_one_variant_takes_the_total_stock(self) -> None:
        product = to_input(
            draft(name="Dupatta", price=2200.0, stock=8),
            CLOTHING,
            2,
            source="t",
            image_media_id=None,
        )
        assert product is not None
        assert product.variants == (VariantInput({}, None, 8),)

    def test_no_product_without_a_name_or_a_believable_price(self) -> None:
        assert (
            to_input(draft(name="", price=100.0), CLOTHING, 2, source="t", image_media_id=None)
            is None
        )
        assert (
            to_input(draft(name="X", price=None), CLOTHING, 2, source="t", image_media_id=None)
            is None
        )
        assert (
            to_input(draft(name="X", price=0.004), CLOTHING, 2, source="t", image_media_id=None)
            is None
        )
        assert to_minor(12.5, 2) == 1250
        assert to_minor(-5, 2) is None

    @pytest.mark.parametrize(
        ("words", "lang", "name", "price"),
        [
            ("Lawn 3-piece 4850", "ur-Latn", "Lawn 3-piece", Decimal(4850)),
            ("Lawn 3-piece Rs 4,850", "ur-Latn", "Lawn 3-piece", Decimal(4850)),
            ("blue kurta 2500 ki", "ur-Latn", "blue kurta", Decimal(2500)),
            ("نیلا کرتا 2500 روپے", "ur", "نیلا کرتا", Decimal(2500)),
            ("", "en", "", None),
        ],
    )
    def test_code_reads_a_typed_name_and_price(
        self, words: str, lang: str, name: str, price: Decimal | None
    ) -> None:
        assert price_from_words(words, lang) == price
        assert name_from_words(words, price, lang) == name
        filled = complete_draft(EMPTY_DRAFT, words, lang)
        assert filled.name == name
        assert filled.missing == [m for m, ok in (("name", name), ("price", price)) if not ok]

    def test_the_models_reading_wins_over_typed_numbers(self) -> None:
        filled = complete_draft(draft(name="Lawn suit", price=4850.0), "3 piece 5000", "en")
        assert (filled.name, filled.price) == ("Lawn suit", 4850.0)


class TestChanges:
    def test_changes_become_exact_operations(self) -> None:
        changes = [
            Change(
                kind="price",
                product="lawn 3 piece",
                value_number=2500,
                policy_key=None,
                value_text=None,
            ),
            Change(
                kind="unavailable",
                product="linen kurta",
                value_number=None,
                policy_key=None,
                value_text=None,
            ),
            Change(
                kind="policy",
                product=None,
                value_number=None,
                policy_key="delivery",
                value_text=" Two days in Lahore ",
            ),
        ]
        ops, problems = resolve_changes(changes, ITEMS, 2)
        assert problems == []
        assert ops[0] == {
            "op": "price",
            "product_id": str(ITEMS[0].product.id),
            "name": "Printed lawn 3-piece",
            "old": 485_000,
            "new": 250_000,
        }
        assert ops[1]["op"] == "unavailable"
        assert ops[2] == {"op": "policy", "key": "delivery", "new": "Two days in Lahore"}

    def test_problems_are_told_not_guessed(self) -> None:
        changes = [
            Change(
                kind="price", product="lawn", value_number=3000, policy_key=None, value_text=None
            ),
            Change(
                kind="price", product="karahi", value_number=3000, policy_key=None, value_text=None
            ),
            Change(
                kind="price",
                product="linen kurta",
                value_number=None,
                policy_key=None,
                value_text=None,
            ),
            Change(
                kind="floor_price",
                product="linen kurta",
                value_number=9000,
                policy_key=None,
                value_text=None,
            ),
            Change(
                kind="policy", product=None, value_number=None, policy_key="hours", value_text=" "
            ),
            Change(kind="price", product=None, value_number=10, policy_key=None, value_text=None),
        ]
        ops, problems = resolve_changes(changes, ITEMS, 2)
        assert ops == []
        assert [key for key, _ in problems] == [
            "owner.which",
            "owner.not_found",
            "owner.need_amount",
            "owner.floor_above_price",
        ]
        assert "LAWN3" in problems[0][1]["options"]

    def test_code_reads_a_typed_price_change(self) -> None:
        ops = typed_price_change("linen kurta 2900", ITEMS, "ur-Latn", 2)
        assert [(op["name"], op["new"]) for op in ops] == [("Linen kurta", 290_000)]
        assert typed_price_change("linen kurta", ITEMS, "en", 2) == []
        assert typed_price_change("linen kurta 2750", ITEMS, "en", 2) == []  # no change
        assert typed_price_change("lawn 3000", ITEMS, "en", 2) == []  # which lawn?


class TestCards:
    def test_product_summary_in_the_owners_language(self) -> None:
        product = ProductInput(
            name="Khaddar suit",
            price=Minor(395_000),
            source="t",
            attributes={"fabric": "khaddar"},
            variants=(VariantInput({"size": "S"}, stock=2), VariantInput({"size": "M"}, stock=3)),
        )
        assert product_summary(SHOP, product, "ur-Latn").splitlines() == [
            "Khaddar suit: Rs 3,950",
            "Kapra: khaddar",
            "Size: S, M",
            "5 stock mein",
        ]
        assert product_summary(SHOP, product, "ur").splitlines()[0] == "Khaddar suit: 3,950 روپے"

    def test_change_lines(self) -> None:
        price = {"op": "price", "name": "Kurta", "old": 275_000, "new": 290_000}
        assert change_line(SHOP, price, "en") == "Kurta: Rs 2,750 → Rs 2,900"
        floor = {"op": "floor_price", "name": "Kurta", "old": None, "new": 250_000}
        assert change_line(SHOP, floor, "en") == "Kurta, lowest price: not set → Rs 2,500"
        policy = {"op": "policy", "key": "hours", "new": "10 to 8"}
        assert change_line(SHOP, policy, "ur-Latn") == "Dukaan ke auqaat: 10 to 8"


class TestSessions:
    def test_issue_and_verify(self) -> None:
        staff, tenant = uuid.uuid4(), uuid.uuid4()
        token = issue(staff, tenant, "key")
        session = verify(token, "key")
        assert (session.staff_id, session.tenant_id) == (staff, tenant)
        assert console_link("https://web.test/", token) == f"https://web.test/owner#t={token}"

    @pytest.mark.parametrize("bad", ["", "abc", "abc.def", "x.y.z"])
    def test_garbage_is_refused(self, bad: str) -> None:
        with pytest.raises(NotSignedIn):
            verify(bad, "key")

    def test_tampered_other_key_and_expired_are_refused(self) -> None:
        token = issue(uuid.uuid4(), uuid.uuid4(), "key")
        body, signature = token.split(".")
        with pytest.raises(NotSignedIn):
            verify(f"{body}x.{signature}", "key")
        with pytest.raises(NotSignedIn):
            verify(token, "other-key")
        with pytest.raises(NotSignedIn):
            verify(issue(uuid.uuid4(), uuid.uuid4(), "key", ttl=timedelta(seconds=-1)), "key")

    def test_button_ids(self) -> None:
        action_id = uuid.uuid4()
        assert parse_button(f"pa:{action_id}:yes") == (action_id, "yes")
        assert parse_button(f"pa:{action_id}:type:food") == (action_id, "type:food")
        assert parse_button("pa:not-a-uuid:yes") is None
        assert parse_button("other") is None
        assert parse_button(None) is None


class TestOnboardingAnswers:
    def test_words_are_read_without_a_model(self) -> None:
        assert read_by_words("shop_name", "Noor Fabrics") == {"shop_name": "Noor Fabrics"}
        assert (
            read_by_words("delivery", "lawn suits, poore Pakistan mein")["business_type"]
            == "clothing"
        )
        assert read_by_words("payment_methods", "JazzCash aur cash on delivery")[
            "payment_methods"
        ] == [
            "jazzcash",
            "cod",
        ]
        assert read_by_words("products", "bas")["done"] is True
        assert read_by_words("products", "बस") == {"done": True}
        assert "business_type" not in read_by_words("city", "kapre aur mobile")  # two kinds: ask

    def test_answers_merge_and_steps_follow(self) -> None:
        answers = merge(
            {},
            {"shop_name": " Noor ", "business_type": "toys", "payment_methods": ["cod", "bitcoin"]},
        )
        assert answers == {"shop_name": "Noor", "payment_methods": ["cod"]}
        state = {"answers": answers}
        assert next_step(state) == "business_type"
        state["answers"] = merge(
            answers, {"business_type": "clothing", "city": "Multan", "delivery": "3 days"}
        )
        assert next_step(state) == "products"
        state["products_done"] = True
        assert next_step(state) is None
