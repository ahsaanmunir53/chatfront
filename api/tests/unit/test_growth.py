"""Data and growth without a database: questions read into the menu and answered (57), buyer
segments (59), share cards (116) and the MCP endpoint's protocol (66)."""

from __future__ import annotations

import io
from datetime import UTC, date, datetime, timedelta
from typing import Any

import pytest
from PIL import Image

from chatfront.catalog import share
from chatfront.catalog.models import Product
from chatfront.core.i18n import t
from chatfront.core.models import Customer, Tenant
from chatfront.crm.service import Buyer, Segment, segment, summary
from chatfront.insights.ask import DataAsk, Row, answer, days, plain, typed_ask
from chatfront.mcp import server
from chatfront.mcp.tools import TOOLS, Context

TODAY = date(2026, 10, 28)  # a Wednesday
SHOP = Tenant(name="Gulmohar Lawn House", country="PK", default_lang="en", slug="gulmohar")


class TestAskYourData:
    @pytest.mark.parametrize(
        ("period", "first", "last"),
        [
            ("today", date(2026, 10, 28), date(2026, 10, 28)),
            ("yesterday", date(2026, 10, 27), date(2026, 10, 27)),
            ("this_week", date(2026, 10, 26), date(2026, 10, 28)),
            ("last_week", date(2026, 10, 19), date(2026, 10, 25)),
            ("this_month", date(2026, 10, 1), date(2026, 10, 28)),
            ("last_month", date(2026, 9, 1), date(2026, 9, 30)),
            ("last_7_days", date(2026, 10, 22), date(2026, 10, 28)),
            ("last_30_days", date(2026, 9, 29), date(2026, 10, 28)),
            ("this_year", date(2026, 1, 1), date(2026, 10, 28)),
            ("all", None, date(2026, 10, 28)),
        ],
    )
    def test_each_period_is_a_span_of_days(
        self, period: str, first: date | None, last: date
    ) -> None:
        assert days(DataAsk(measure="sales", period=period), TODAY) == (first, last)  # type: ignore[arg-type]

    def test_dates_are_put_in_order_and_never_past_today(self) -> None:
        asked = DataAsk(measure="sales", period="dates", start="2026-11-30", end="2026-10-01")
        assert days(asked, TODAY) == (date(2026, 10, 1), TODAY)
        broken = DataAsk(measure="sales", period="dates", start="soon", end=None)
        assert days(broken, TODAY) == (None, TODAY)

    @pytest.mark.parametrize(
        ("words", "fields"),
        [
            (
                "what sold most last month?",
                {"measure": "units", "by": "product", "period": "last_month"},
            ),
            (
                "sab se zyada kya bika is hafte",
                {"measure": "units", "by": "product", "period": "this_week"},
            ),
            ("aaj kitni sale hui?", {"measure": "sales", "period": "today"}),
            ("kitne order aaye pichle hafte", {"measure": "orders", "period": "last_week"}),
            ("which city orders most? kitni sale", {"measure": "sales", "by": "city"}),
            ("munafa is mahine", {"measure": "profit", "period": "this_month"}),
            ("best customers", {"measure": "sales", "by": "customer"}),
            ("kam bika kya", {"measure": "units", "by": "product", "order": "least"}),
        ],
    )
    def test_common_questions_are_read_without_a_model(
        self, words: str, fields: dict[str, Any]
    ) -> None:
        assert typed_ask(words) == DataAsk.model_validate(fields)

    def test_anything_else_is_left_to_the_model(self) -> None:
        assert typed_ask("naya suit add karo 4500 ka") is None

    def test_code_keeps_the_question_to_what_it_can_answer(self) -> None:
        assert plain(DataAsk(measure="profit", by="city")).by == "none"
        assert plain(DataAsk(measure="average_order", by="product")).by == "none"
        assert plain(DataAsk(measure="units", top=500)).top == 20
        assert plain(DataAsk(measure="units", top=-3)).top == 1
        assert plain(DataAsk(measure="units", top=0)).top == 5  # nothing said

    def test_an_answer_is_written_by_code(self) -> None:
        ranked = DataAsk(measure="units", by="product", period="last_month", top=5)
        rows = [Row("Printed lawn", 14.0), Row("Dupatta", 3.0)]
        lines = answer(SHOP, ranked, rows, TODAY, "en").splitlines()
        period = t("ask.between", "en", start="2026-09-01", end="2026-09-30")
        assert lines == [
            t("ask.head_units", "en", period=period),
            "1. Printed lawn: " + t("ask.count_units", "en", count=14),
            "2. Dupatta: " + t("ask.count_units", "en", count=3),
        ]
        total = answer(
            SHOP, DataAsk(measure="sales", period="today"), [Row(None, 485000)], TODAY, "en"
        )
        assert (
            total
            == t("ask.head_sales", "en", period=t("ask.on_day", "en", day="2026-10-28"))
            + " Rs 4,850"
        )
        empty = answer(SHOP, DataAsk(measure="orders", product="kurta"), [], TODAY, "en")
        assert empty.splitlines()[1] == t("ask.none", "en")
        assert t("ask.only_product", "en", product="kurta") in empty

    def test_days_and_channels_are_named(self) -> None:
        by_day = DataAsk(measure="orders", by="day", period="this_week")
        rows = [Row(datetime(2026, 10, 26), 2), Row(datetime(2026, 10, 27), 1)]
        assert answer(SHOP, by_day, rows, TODAY, "en").splitlines()[1].startswith("- 2026-10-26: ")
        by_channel = DataAsk(measure="orders", by="channel")
        rows = [Row("whatsapp", 5), Row("shop_page", 2), Row(None, 1)]
        lines = answer(SHOP, by_channel, rows, TODAY, "en").splitlines()
        assert lines[1].startswith("1. WhatsApp: ")
        assert lines[2].startswith(f"2. {t('ask.source_shop_page', 'en')}: ")
        assert lines[3].startswith(f"3. {t('ask.unknown', 'en')}: ")


def buyer(kept: int, spent: int, last_days: int | None) -> Buyer:
    now = datetime.now(UTC)
    last = now - timedelta(days=last_days) if last_days is not None else None
    return Buyer(
        customer=Customer(),
        orders=kept,
        kept=kept,
        delivered=kept,
        returned=0,
        spent=spent,
        first=last,
        last=last,
        owes=0,
        open_chat=False,
        segment=segment(kept, last, now),
    )


class TestCustomers:
    @pytest.mark.parametrize(
        ("kept", "last_days", "found"),
        [
            (0, None, Segment.NONE),
            (1, 3, Segment.NEW),
            (2, 3, Segment.REPEAT),
            (5, 3, Segment.LOYAL),
            (7, 61, Segment.QUIET),
        ],
    )
    def test_each_buyer_has_a_segment(
        self, kept: int, last_days: int | None, found: Segment
    ) -> None:
        assert buyer(kept, 0, last_days).segment == found

    def test_the_summary_counts_buyers_who_came_back(self) -> None:
        found = summary(
            [
                buyer(1, 1_000, 2),
                buyer(3, 9_000, 5),
                buyer(2, 2_000, 90),
                buyer(0, 0, None),
            ]
        )
        assert (found.buyers, found.came_back, found.quiet) == (3, 2, 1)
        assert (found.average_spent, found.repeat_rate) == (4_000, 67)
        assert summary([]).repeat_rate is None


class TestShareCards:
    def test_the_card_is_a_wide_png_with_or_without_a_photo(self) -> None:
        product = Product(name="Printed lawn 3-piece", price=485_000, code="AB12CD", description="")
        photo = io.BytesIO()
        Image.new("RGB", (300, 500), "teal").save(photo, "JPEG")
        for given in (photo.getvalue(), None, b"not a picture"):
            card = Image.open(io.BytesIO(share.card_png(SHOP, product, given)))
            assert (card.format, card.size) == ("PNG", (1200, 630))

    @pytest.mark.parametrize(
        ("lang", "name"),
        [
            ("ur-Latn", "Basmati chawal super kernel extra long grain wala 5 kg ka bag"),
            ("ur", "سپر کرنل باسمتی چاول لمبا دانہ پانچ کلو کا تھیلا خاص پیکنگ میں"),
        ],
    )
    def test_long_words_stay_inside_the_card(self, lang: str, name: str) -> None:
        shop = Tenant(
            name="Barkat Kiryana Store and General Merchants of Lahore",
            country="PK",
            default_lang=lang,
            slug="barkat",
        )
        product = Product(name=name, price=95_000, code="RC05", description="")
        card = Image.open(io.BytesIO(share.card_png(shop, product, None)))
        margin = share.PAD // 2
        for edge in (
            card.crop((share.SIZE[0] - margin, 0, share.SIZE[0], share.SIZE[1])),
            card.crop((share.PHOTO, 0, share.PHOTO + margin, share.SIZE[1])),
        ):
            assert edge.getcolors() == [(edge.width * edge.height, share.INK)]

    def test_the_page_carries_escaped_preview_tags_and_sends_people_on(self) -> None:
        product = Product(name='Kurta "<b>"', price=275_000, code="KR01", description="")
        html = share.page(
            SHOP,
            product,
            share_url="https://api.example/s/gulmohar/p/KR01",
            card_url="https://api.example/card.png",
            shop_url="https://web.example/s/gulmohar#p-KR01</script>",
        )
        assert '<meta property="og:image" content="https://api.example/card.png" />' in html
        assert '<meta name="twitter:card" content="summary_large_image" />' in html
        assert "<b>" not in html
        assert "&quot;&lt;b&gt;&quot;" in html
        assert "\\u003c/script\\u003e" in html  # the redirect cannot close its script
        assert t("share.description", "en", price="Rs 2,750", shop=SHOP.name) in html


class TestMcpProtocol:
    ctx = Context(services=None, tenant=SHOP)  # type: ignore[arg-type]

    async def test_initialize_agrees_on_a_version_and_lists_tools(self) -> None:
        hello = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {"protocolVersion": "2025-03-26"},
        }
        reply = await server.handle(self.ctx, hello)
        assert reply is not None
        assert reply["result"]["protocolVersion"] == "2025-03-26"
        assert reply["result"]["capabilities"] == {"tools": {"listChanged": False}}
        newer = {**hello, "params": {"protocolVersion": "2099-01-01"}}
        assert (await server.handle(self.ctx, newer))["result"]["protocolVersion"] == "2025-06-18"  # type: ignore[index]
        listed = await server.handle(self.ctx, {"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
        assert listed is not None
        names = [tool["name"] for tool in listed["result"]["tools"]]
        assert names == list(TOOLS)
        assert all(tool["inputSchema"]["type"] == "object" for tool in listed["result"]["tools"])

    async def test_notifications_get_no_answer_and_errors_are_json_rpc(self) -> None:
        note = {"jsonrpc": "2.0", "method": "notifications/initialized"}
        assert await server.handle(self.ctx, note) is None
        assert await server.handle_body(self.ctx, [note]) is None
        unknown = await server.handle(
            self.ctx, {"jsonrpc": "2.0", "id": 3, "method": "resources/list"}
        )
        assert unknown is not None
        assert unknown["error"]["code"] == server.NO_METHOD
        bad = await server.handle(self.ctx, {"id": 4, "method": "ping"})
        assert bad is not None
        assert bad["error"]["code"] == server.INVALID_REQUEST
        no_tool = {"jsonrpc": "2.0", "id": 5, "method": "tools/call", "params": {"name": "nope"}}
        assert (await server.handle(self.ctx, no_tool))["error"]["code"] == server.INVALID_PARAMS  # type: ignore[index]
        wrong = {
            "jsonrpc": "2.0",
            "id": 6,
            "method": "tools/call",
            "params": {"name": "get_product", "arguments": {"code": ""}},
        }
        assert (await server.handle(self.ctx, wrong))["error"]["code"] == server.INVALID_PARAMS  # type: ignore[index]
        ping = {"jsonrpc": "2.0", "id": 7, "method": "ping"}
        assert await server.handle_body(self.ctx, [ping, note]) == [
            {"jsonrpc": "2.0", "id": 7, "result": {}}
        ]
        too_many = await server.handle_body(self.ctx, [ping] * 21)
        assert isinstance(too_many, dict)
        assert too_many["error"]["code"] == server.INVALID_REQUEST
