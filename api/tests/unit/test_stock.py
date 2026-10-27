"""Restock, batches, expiry and the owner's panel without a database (features 117 to 120):
exact cost shares, selling order, price drift, bill and sheet lines turned into restock
operations, notice buttons, and the WhatsApp Flow message."""

from __future__ import annotations

import io
import json
import uuid
import zipfile
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import ClassVar

import httpx
import pytest
import respx
from openpyxl import Workbook

import chatfront.stock
from chatfront.books.reports import Totals, money_card
from chatfront.catalog.drafts import OwnerMessage, PurchaseLine
from chatfront.catalog.models import Product, Variant
from chatfront.catalog.service import CatalogItem
from chatfront.catalog.stock import REORDER, SNOOZE, low_button, parse_low
from chatfront.channels.base import AccountRef, check_sendable
from chatfront.channels.types import Capability, Kind, OutFlow, required_capability, to_record
from chatfront.channels.whatsapp import WhatsAppAdapter
from chatfront.core.errors import AppError
from chatfront.core.i18n import t
from chatfront.core.models import Tenant
from chatfront.owner.rights import ROLE_RIGHTS, Can
from chatfront.stock import expiry, notices, ops, prices, restock, sheets
from chatfront.stock.actions import whatsapp_link
from chatfront.stock.batches import expired, selling_order, share, unit_cost
from chatfront.stock.models import StockBatch
from chatfront.stock.profit import Sold

SHOP = Tenant(name="Gulmohar", slug="gulmohar", template="clothing", country="PK", profile={})
LANG = "en"
DAY = date(2026, 10, 27)
GRAPH = "https://graph.facebook.com/v23.0"


def batch(
    quantity: int,
    cost: int | None,
    *,
    received: date = DAY,
    expires: date | None = None,
    order: int = 0,
) -> StockBatch:
    return StockBatch(
        id=uuid.uuid4(),
        quantity=quantity,
        units_left=quantity,
        total_cost=cost,
        cost_left=cost,
        received_on=received,
        expires_on=expires,
        created_at=datetime(2026, 10, 27, 9, order, tzinfo=UTC),
    )


def lawn() -> CatalogItem:
    product = Product(
        id=uuid.uuid4(),
        code="LAWN1",
        name="Printed lawn 3-piece",
        description="",
        tags=[],
        attributes={},
        translations={},
        price=485_000,
    )
    variants = [
        Variant(id=uuid.uuid4(), options={"colour": colour}, active=True, stock=stock)
        for colour, stock in (("Sea green", 6), ("Rust", 4))
    ]
    return CatalogItem(product, variants)


def khaddar() -> CatalogItem:
    product = Product(
        id=uuid.uuid4(),
        code="KHAD1",
        name="Khaddar 2-piece, stitched",
        description="",
        tags=[],
        attributes={},
        translations={},
        price=395_000,
    )
    variants = [
        Variant(id=uuid.uuid4(), options={"size": size}, active=True, stock=3)
        for size in ("S", "M", "L")
    ]
    return CatalogItem(product, variants)


def line(item: str, quantity: int | None, **extra: object) -> PurchaseLine:
    return PurchaseLine(item=item, quantity=quantity, **extra)  # type: ignore[arg-type]


class TestCostShares:
    def test_shares_add_up_to_the_batch_cost_exactly(self) -> None:
        cost_left, units_left, taken = 100, 3, []
        for _ in range(3):
            part = share(cost_left, units_left, 1)
            assert part is not None
            taken.append(part)
            cost_left, units_left = cost_left - part, units_left - 1
        assert taken == [33, 33, 34]
        assert sum(taken) == 100

    def test_the_owners_example_costs(self) -> None:
        a = batch(20, 5_400_000)  # 54,000 for 20 suits
        assert share(a.cost_left, a.units_left, 10) == 2_700_000
        assert share(a.cost_left, a.units_left, 25) == 5_400_000  # all that is left
        assert unit_cost(a) == 270_000
        assert share(None, 20, 5) is None
        assert unit_cost(batch(20, None)) is None

    def test_first_in_first_out_without_dates(self) -> None:
        late = batch(5, 100, received=DAY, order=2)
        early = batch(5, 100, received=DAY - timedelta(days=3))
        same_day_first = batch(5, 100, received=DAY, order=1)
        ordered, fefo = selling_order([late, early, same_day_first])
        assert ordered == [early, same_day_first, late]
        assert not fefo

    def test_first_to_expire_goes_first_and_undated_last(self) -> None:
        old_far = batch(5, 100, received=DAY - timedelta(days=9), expires=DAY + timedelta(30))
        new_near = batch(5, 100, expires=DAY + timedelta(days=5))
        undated = batch(5, 100, received=DAY - timedelta(days=20))
        ordered, fefo = selling_order([old_far, undated, new_near])
        assert ordered == [new_near, old_far, undated]
        assert fefo

    def test_expired_means_past_the_date(self) -> None:
        assert not expired(batch(1, 1, expires=DAY), DAY)
        assert expired(batch(1, 1, expires=DAY - timedelta(days=1)), DAY)
        assert not expired(batch(1, 1), DAY)

    def test_profit_compares_like_with_like(self) -> None:
        sold = Sold(units=10, revenue=40_000, cost=21_000, unknown=4)
        assert sold.known_revenue == 24_000  # the 6 units whose cost is known
        assert sold.profit == 19_000
        assert Sold(units=2, revenue=500, cost=300).known_revenue == 500
        assert Sold(units=0, revenue=0, cost=0, unknown=0).known_revenue == 0


class TestPriceQuestion:
    def test_drift_of_two_percent_asks(self) -> None:
        assert prices.drifted(290_000, 270_000)
        assert prices.drifted(264_000, 270_000)
        assert not prices.drifted(271_000, 270_000)
        assert prices.drifted(1, 0)

    def test_buttons_round_trip(self) -> None:
        batch_id = uuid.uuid4()
        for choice in prices.CHOICES:
            assert prices.parse_button(prices.button_id(batch_id, choice)) == (batch_id, choice)
        assert prices.parse_button("pq:not-a-uuid:keep") is None
        assert prices.parse_button(f"pq:{batch_id}:maybe") is None
        assert prices.parse_button(None) is None
        assert expiry.parse_button(expiry.button_id(batch_id, "discard")) == (batch_id, "discard")
        assert expiry.parse_button("ex:x:later") is None
        assert expiry.parse_button(f"ex:{batch_id}:eat") is None
        assert parse_low(low_button(batch_id, SNOOZE)) == (batch_id, SNOOZE)
        assert parse_low(low_button(batch_id, REORDER)) == (batch_id, REORDER)
        assert parse_low("ls:nope:snooze") is None

    def test_titles_fit_whatsapp_buttons(self) -> None:
        for lang in ("en", "ur", "ur-Latn", "ar", "bn"):
            for key in (*prices.TITLES.values(), "expiry.discount", "expiry.discard"):
                assert len(t(key, lang)) <= 20
            assert len(t("expiry.later", lang)) <= 20


class TestExpiry:
    def test_the_shop_can_set_its_own_warning_period(self) -> None:
        assert expiry.warning_days(SHOP) == 7
        own = Tenant(name="S", slug="s", template="food", country="PK", profile={})
        own.profile = {"expiry_warning_days": 3}
        assert expiry.warning_days(own) == 3
        for bad in (0, True, "7", 400):
            own.profile = {"expiry_warning_days": bad}
            assert expiry.warning_days(own) == 7


class TestRestockLines:
    def test_purchase_words(self) -> None:
        assert restock.is_purchase("supplier ka challan")
        assert restock.is_purchase("نیا اسٹاک")
        assert restock.is_purchase("Restock!")
        assert not restock.is_purchase("naya maal aaya")  # a new design, not a bill
        assert not restock.is_purchase("bijli ka bill")

    def test_dates_and_costs(self) -> None:
        assert restock.parse_expiry("2026-12-12") == date(2026, 12, 12)
        assert restock.parse_expiry("2026-02-30") is None
        assert restock.parse_expiry("12 Dec") is None
        assert restock.parse_expiry(None) is None
        assert restock.line_cost(line("x", 20, total_cost=54000), 2) == 5_400_000
        assert restock.line_cost(line("x", 20, unit_cost=2700), 2) == 5_400_000
        assert restock.line_cost(line("x", None, unit_cost=2700), 2) is None
        assert restock.line_cost(line("x", 20), 2) is None

    def test_variants_are_named_by_whole_words(self) -> None:
        suits = khaddar()
        assert restock.pick_variant(suits, "khaddar stitched M 5") is suits.variants[1]
        assert restock.pick_variant(suits, "khaddar stitched") is None  # no "S" inside a word
        one = lawn()
        one.variants[1].active = False
        assert restock.pick_variant(one, "lawn") is one.variants[0]

    def test_lines_become_operations_or_questions(self) -> None:
        items = [lawn(), khaddar()]
        found, problems = restock.resolve_lines(
            [
                line("printed lawn rust", 20, unit_cost=2700, expiry="2027-01-31"),
                line("printed lawn", 5),
                line("khaddar L", None),
                line("silk saree", 3),
                line("khaddar", 4),
            ],
            items,
            2,
        )
        assert found == [
            {
                "op": "restock",
                "product_id": str(items[0].product.id),
                "variant_id": str(items[0].variants[1].id),
                "name": "Printed lawn 3-piece (Rust)",
                "before": 4,
                "quantity": 20,
                "total_cost": 5_400_000,
                "expires_on": "2027-01-31",
            }
        ]
        assert [key for key, _ in problems] == [
            "stock.which_variant",
            "stock.need_quantity",
            "stock.not_in_shop",
            "stock.which_variant",
        ]
        assert problems[0][1]["options"] == "Sea green, Rust"
        text = restock.card_text(SHOP, found, "Al-Noor Textiles", LANG)
        assert text.splitlines() == [
            "Add this stock?",
            "- Printed lawn 3-piece (Rust): 4 + 20 = 24 (cost Rs 54,000) (expires 2027-01-31)",
            "Rs 54,000 goes in the books as stock bought.",
            "From: Al-Noor Textiles",
        ]
        assert ops.line(SHOP, found[0], LANG) == text.splitlines()[1][2:]

    def test_too_many_pieces_is_a_question(self) -> None:
        _, problems = restock.resolve_lines([line("printed lawn rust", 2_000_000)], [lawn()], 2)
        assert problems == [("stock.need_quantity", {"item": "printed lawn rust"})]

    def test_supplier_numbers_must_be_local(self) -> None:
        assert restock.supplier_phone(SHOP, "0300 1234567") == "+923001234567"
        assert restock.supplier_phone(SHOP, "12345") is None
        assert restock.supplier_phone(SHOP, None) is None

    def test_the_owner_message_can_carry_stock(self) -> None:
        said = OwnerMessage.model_validate(
            {
                "intent": "restock",
                "product": None,
                "changes": [],
                "note": "",
                "restock": [{"item": "blue lawn", "quantity": 30}],
                "supplier": "Al-Noor",
            }
        )
        assert said.restock[0].quantity == 30


class TestStockOps:
    def test_rights_per_operation(self) -> None:
        assert ops.right_for({"op": "restock"}) == Can.STOCK
        assert ops.right_for({"op": "expiry"}) == Can.STOCK
        assert ops.right_for({"op": "price_answer"}) == Can.CATALOG
        assert ops.rights_for([{"op": "restock"}, {"op": "price_answer"}]) == {
            Can.STOCK,
            Can.CATALOG,
        }
        assert ROLE_RIGHTS["stock"] == frozenset({Can.ORDERS, Can.CATALOG, Can.STOCK})

    def test_card_lines(self) -> None:
        when = {"op": "expiry", "name": "Eggs", "old": None, "new": "2026-11-02"}
        assert ops.line(SHOP, when, LANG) == "Expiry of Eggs (newest stock): not set → 2026-11-02"
        later = {"op": "price_answer", "choice": "later", "name": "Eggs", "old": 33_000}
        assert ops.line(SHOP, later | {"new": 35_000}, LANG) == (
            "Eggs: Rs 330 → Rs 350, once the older stock has sold"
        )
        now = later | {"choice": "now", "new": 35_000}
        assert ops.line(SHOP, now, LANG) == "Eggs: Rs 330 → Rs 350, from now"


class TestSheets:
    HEADER: ClassVar[list[str]] = ["Item", "Colour", "Qty", "Rate", "Expiry", "Supplier"]

    def test_csv_with_semicolons(self) -> None:
        data = b"Item;Qty;Rate\nPrinted lawn rust;20;2700\n;;\n"
        assert sheets.read_rows(data) == [
            ["Item", "Qty", "Rate"],
            ["Printed lawn rust", "20", "2700"],
        ]

    def test_excel_first_sheet(self) -> None:
        book = Workbook()
        sheet = book.active
        assert sheet is not None
        sheet.append(self.HEADER)
        sheet.append(["Printed lawn", "Rust", 20.0, 2700, datetime(2027, 1, 31), "Al-Noor"])
        out = io.BytesIO()
        book.save(out)
        rows = sheets.read_rows(out.getvalue())
        assert rows[1] == ["Printed lawn", "Rust", "20", "2700", "2027-01-31", "Al-Noor"]

    def test_bad_sheets(self) -> None:
        with pytest.raises(AppError, match="2 MB"):
            sheets.read_rows(b"x" * (sheets.MAX_BYTES + 1))
        with pytest.raises(AppError, match="header row"):
            sheets.read_rows(b"Item,Qty\n")
        with pytest.raises(AppError, match="Excel"):
            sheets.read_rows(_empty_zip())

    def test_headers_matched_by_their_usual_names(self) -> None:
        columns = sheets.keyword_columns(["Article", "Size", "Pcs", "Cost price", "Total", "Exp"])
        assert columns.model_dump() == {
            "item": "Article",
            "option": "Size",
            "quantity": "Pcs",
            "unit_cost": "Cost price",
            "total_cost": "Total",
            "expiry": "Exp",
            "supplier": None,
        }
        with pytest.raises(AppError, match="quantity"):
            sheets.checked(sheets.keyword_columns(["Item", "Rate"]), ["Item", "Rate"])
        made_up = sheets.Columns(item="Item", quantity="Pieces")
        with pytest.raises(AppError):
            sheets.checked(made_up, ["Item", "Qty"])

    def test_rows_become_lines(self) -> None:
        rows = [
            self.HEADER,
            ["Printed lawn", "Rust", "20", "2,700", "31/01/2027", "Al-Noor"],
            ["Printed lawn", "Sea green", "4.5", "", "31/02/2027", ""],
            ["", "", "3", "", "", ""],
        ]
        columns = sheets.keyword_columns(self.HEADER)
        lines, supplier = sheets.to_lines(rows, columns)
        assert supplier == "Al-Noor"
        assert [(x.item, x.quantity, x.unit_cost, x.expiry) for x in lines] == [
            ("Printed lawn Rust", 20, 2700.0, "2027-01-31"),
            ("Printed lawn Sea green", None, None, None),
        ]
        preview = sheets.preview(lines, [lawn()], 2)
        assert preview[0]["name"] == "Printed lawn 3-piece (Rust)"
        assert preview[0]["total_cost"] == 5_400_000
        assert preview[1]["problem"] == "stock.need_quantity"


def _empty_zip() -> bytes:
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w") as archive:
        archive.writestr("hello.txt", "not a workbook")
    return out.getvalue()


class TestNotices:
    def test_one_line_for_templates(self) -> None:
        assert (
            notices.one_line("Low stock:\n\n  Rust   lawn\tleft 2")
            == "Low stock:; Rust lawn left 2"
        )
        assert len(notices.one_line("x" * 5000)) == notices.TEMPLATE_PARAM_MAX

    def test_reorder_link_opens_the_owners_whatsapp(self) -> None:
        assert whatsapp_link("+923001234567", "Salam, 10 pieces") == (
            "https://wa.me/923001234567?text=Salam%2C%2010%20pieces"
        )

    def test_books_card_shows_goods_profit(self) -> None:
        # 18 of 20 pieces have a cost: Rs 54,000 of the sales, against Rs 45,000 of cost.
        goods = Sold(20, 6_000_000, 4_500_000, 2)
        sums = Totals(sales=6_000_000, write_offs=500_000, goods=goods)
        card = money_card(SHOP, "today", sums, LANG)
        assert t("books.line_goods_profit", LANG, amount="Rs 4,000") in card
        assert t("books.line_write_offs", LANG, amount="Rs 5,000") in card
        assert t("books.line_cost_unknown", LANG, count=2) in card


class TestFlows:
    def test_a_flow_opens_with_its_data(self) -> None:
        flow = OutFlow("Change it here:", "FLOW1", "Edit", "product:abc", "EDIT_PRODUCT", {"a": 1})
        assert required_capability(flow) == Capability.FLOW
        assert to_record(flow) == ("Change it here:", {"flow": {"token": "product:abc"}})
        adapter = WhatsAppAdapter(httpx.AsyncClient(), "https://graph.facebook.com/", "v23.0")
        now = datetime(2026, 10, 27, 12, tzinfo=UTC)
        check_sendable(adapter, flow, now - timedelta(hours=1), now)

    @respx.mock
    async def test_the_flow_message_body(self) -> None:
        route = respx.post(f"{GRAPH}/PN1/messages").mock(
            return_value=httpx.Response(200, json={"messages": [{"id": "wamid.f"}]})
        )
        adapter = WhatsAppAdapter(httpx.AsyncClient(), "https://graph.facebook.com/", "v23.0")
        cta = "Edit the product now, please"
        flow = OutFlow("Change it:", "FLOW1", cta, "product:1", "S", {"x": "1"})
        await adapter.send(AccountRef("PN1", "tok"), "923001234567", flow)
        body = json.loads(route.calls[0].request.content)
        assert body["interactive"]["type"] == "flow"
        params = body["interactive"]["action"]["parameters"]
        assert params["flow_id"] == "FLOW1"
        assert params["flow_token"] == "product:1"
        assert params["flow_cta"] == cta[:20]
        assert params["flow_action_payload"] == {"screen": "S", "data": {"x": "1"}}

    def test_a_filled_flow_comes_back_as_a_button(self) -> None:
        adapter = WhatsAppAdapter(httpx.AsyncClient(), "https://graph.facebook.com/", "v23.0")
        filled = {"flow_token": "product:1", "price": "5000", "add_stock": "10"}
        replies = [
            {"type": "nfm_reply", "nfm_reply": {"response_json": json.dumps(filled)}},
            {"type": "nfm_reply", "nfm_reply": {"response_json": "not json"}},
            {"type": "nfm_reply", "nfm_reply": {"response_json": "[1]"}},
        ]
        payload = {
            "object": "whatsapp_business_account",
            "entry": [
                {
                    "changes": [
                        {
                            "field": "messages",
                            "value": {
                                "metadata": {"phone_number_id": "PN1"},
                                "contacts": [{"wa_id": "923001234567"}],
                                "messages": [
                                    {
                                        "from": "923001234567",
                                        "id": f"wamid.{n}",
                                        "timestamp": "1793000000",
                                        "type": "interactive",
                                        "interactive": reply,
                                    }
                                    for n, reply in enumerate(replies)
                                ],
                            },
                        }
                    ]
                }
            ],
        }
        inbound, _ = adapter.parse(payload)
        assert [m.kind for m in inbound] == [Kind.BUTTON] * 3
        assert inbound[0].payload["button_id"] == "flow:product:1"
        assert inbound[0].payload["flow"]["add_stock"] == "10"
        assert inbound[1].payload["flow"] == {}
        assert inbound[2].payload["flow"] == {}

    def test_the_published_flow_is_valid_json_with_one_screen(self) -> None:
        path = Path(chatfront.stock.__file__).parent / "flows" / "edit_product.json"
        flow = json.loads(path.read_text())
        assert [screen["id"] for screen in flow["screens"]] == ["EDIT_PRODUCT"]
        assert flow["screens"][0]["terminal"] is True
