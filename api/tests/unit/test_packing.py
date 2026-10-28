"""Packing and books without a database: the packing check's verdict (102), the pick list and
its slips (103), and how delivery money is shared over products (51)."""

from __future__ import annotations

import uuid
from datetime import date

import pytest

from chatfront.channels.types import OutButtons, OutText
from chatfront.core.clock import utcnow
from chatfront.core.i18n import t
from chatfront.core.models import Tenant
from chatfront.delivery import packing, picking
from chatfront.delivery.labels import ShopPdf
from chatfront.delivery.models import PackingCheck, Shipment, Verdict
from chatfront.delivery.packing import Checked, PackingReading, SeenLine
from chatfront.orders.models import Order, OrderItem
from chatfront.stock.profit import Delivery, Sold, TrueProfit, parcel, split


def item(name: str, quantity: int, **options: str) -> OrderItem:
    return OrderItem(name=name, quantity=quantity, options=options, unit_price=100, line_total=100)


ITEMS = [item("Printed lawn 3-piece", 2, colour="Rust"), item("Dupatta", 1)]


def reading(
    *lines: tuple[str, int | None, bool], extra: list[str] | None = None, clear: bool = True
) -> PackingReading:
    return PackingReading(
        lines=[SeenLine(line=line, seen=seen, sure=sure) for line, seen, sure in lines],
        extra=extra or [],
        clear=clear,
        note="",
    )


class TestPackingCheck:
    def test_a_photo_for_a_packed_order_is_known_by_its_words(self) -> None:
        assert packing.is_check("pack 49GAM3")
        assert packing.is_check("پیک ہو گیا")
        assert packing.is_check("পার্সেল তৈরি")
        assert not packing.is_check("naya suit")
        assert packing.codes_in("pack 49gam3, and AB12CD!") == ["49GAM3", "AB12CD"]
        assert packing.codes_in("order 1234567") == []

    def test_every_piece_and_nothing_else_is_a_match(self) -> None:
        outcome = packing.compare(ITEMS, reading(("L1", 2, True), ("l2", 1, True)))
        assert outcome.verdict == Verdict.MATCH
        assert [line["name"] for line in outcome.lines] == [
            "Printed lawn 3-piece (Rust)",
            "Dupatta",
        ]

    @pytest.mark.parametrize(
        "found",
        [
            reading(("L1", 1, True), ("L2", 1, True)),  # one missing
            reading(("L1", 3, True), ("L2", 1, True)),  # one too many
            reading(("L1", 2, True), ("L2", 1, True), extra=["a blue scarf"]),
            reading(("L1", 1, True), ("L2", None, False)),  # a clear miss beats a doubt
        ],
    )
    def test_anything_clearly_wrong_is_a_mismatch(self, found: PackingReading) -> None:
        assert packing.compare(ITEMS, found).verdict == Verdict.MISMATCH

    @pytest.mark.parametrize(
        "found",
        [
            reading(("L1", 2, True), ("L2", 1, False)),
            reading(("L1", 2, True), ("L2", None, True)),
            reading(("L1", 2, True)),  # a line the model left out
            reading(("L1", 2, True), ("L2", 1, True), clear=False),
        ],
    )
    def test_anything_not_seen_for_sure_is_unclear(self, found: PackingReading) -> None:
        assert packing.compare(ITEMS, found).verdict == Verdict.UNCLEAR

    def test_extra_things_are_trimmed_and_capped(self) -> None:
        found = reading(("L1", 2, True), ("L2", 1, True), extra=["  a  scarf ", "", *"abcdefg"])
        outcome = packing.compare(ITEMS, found)
        assert outcome.extra == ["a scarf", "a", "b", "c", "d"]

    def _checked(
        self, found: PackingReading, *, asked: bool = False, ready: bool = True
    ) -> Checked:
        outcome = packing.compare(ITEMS, found)
        check = PackingCheck(verdict=outcome.verdict, lines=outcome.lines, extra=outcome.extra)
        return Checked(check, "AB12CD", asked, ready)

    def test_a_match_gets_mark_packed(self) -> None:
        sent = packing.reply(self._checked(reading(("L1", 2, True), ("L2", 1, True))), "en")
        assert isinstance(sent, OutButtons)
        assert sent.text == t("packing.match", "en", code="AB12CD", count=3)
        assert sent.buttons[0].id == "pk:AB12CD"

    def test_a_mismatch_says_what_to_fix_and_offers_no_button(self) -> None:
        found = reading(("L1", 1, True), ("L2", 2, True), extra=["a scarf"])
        sent = packing.reply(self._checked(found), "en")
        assert isinstance(sent, OutText)
        assert sent.text.splitlines() == [
            t("packing.mismatch", "en", code="AB12CD"),
            "- " + t("packing.missing", "en", name="Printed lawn 3-piece (Rust)", count=1),
            "- " + t("packing.too_many", "en", name="Dupatta", count=1),
            "- " + t("packing.extra", "en", item="a scarf"),
            t("packing.fix", "en"),
        ]

    def test_an_unclear_check_waits_for_the_owner_or_lets_them_decide(self) -> None:
        unclear = reading(("L1", 2, True), ("L2", 1, False))
        asked = packing.reply(self._checked(unclear, asked=True), "en")
        assert isinstance(asked, OutText)
        assert asked.text.splitlines()[-1] == t("packing.asked_owner", "en")
        assert "- " + t("packing.not_seen", "en", name="Dupatta") in asked.text
        assert isinstance(packing.reply(self._checked(unclear), "en"), OutButtons)
        done = packing.reply(
            self._checked(reading(("L1", 2, True), ("L2", 1, True)), ready=False), "en"
        )
        assert isinstance(done, OutText)  # packed already: nothing to tap


def order(code: str, **fields: object) -> Order:
    values: dict[str, object] = {
        "id": uuid.uuid4(),
        "code": code,
        "status": "paid",
        "total": 0,
        "paid": 0,
    }
    return Order(**(values | fields))


class TestPickList:
    def test_asking_for_the_list(self) -> None:
        assert picking.is_ask("Pick List please")
        assert picking.is_ask("aaj kya pack karna hai?")
        assert picking.is_ask("پیکنگ لسٹ بھیجو")
        assert not picking.is_ask("pack AB12CD")

    def test_a_line_names_its_orders(self) -> None:
        line = picking.PickLine("Sugar (1 kg)", 3, {"AB12CD": 2, "XY34ZW": 1})
        assert line.orders_text() == "AB12CD \N{MULTIPLICATION SIGN}2, XY34ZW"

    def test_the_list_as_a_message(self) -> None:
        empty = picking.PickList(date(2026, 10, 28), [], [], [])
        assert picking.text(empty, "en") == t("pick.none", "en")
        lines = [picking.PickLine(f"Item {n:02}", 1, {"AB12CD": 1}) for n in range(32)]
        lines[0].short = 1
        picks = picking.PickList(date(2026, 10, 28), lines, [], [("XY34ZW", date(2026, 11, 2))])
        sent = picking.text(picks, "en").splitlines()
        assert sent[0] == t("pick.title", "en", orders=0, pieces=32)
        assert sent[1].endswith(t("pick.short", "en", count=1))
        assert sent[-3] == t("pick.more", "en", count=2)
        assert sent[-2] == t("pick.waiting", "en", count=1, orders="XY34ZW (2026-11-02)")
        assert sent[-1] == t("pick.slips_hint", "en")

    def test_the_list_and_slips_print_in_any_script(self) -> None:
        tenant = Tenant(name="بركت كريانہ", country="PK")
        cod = order("AB12CD", payment_method="cod", total=50_000, notes="Ring the bell")
        cod.address = {"name": "Bilal", "city": "Lahore"}
        many = [item(f"چینی {n}", n) for n in range(1, 71)]
        slips = [
            picking.Slip(cod, [item("Sugar", 2, size="1 kg")], 50_000),
            picking.Slip(order("XY34ZW"), many, 0),
        ]
        lines = [picking.PickLine("Sugar (1 kg)", 2, {"AB12CD": 2}, short=1)]
        picks = picking.PickList(date(2026, 10, 28), lines, slips, [("QQ11ZZ", date(2026, 11, 2))])
        pdf = picking.pdf_bytes(tenant, picks)
        assert pdf.startswith(b"%PDF-")
        assert (
            pdf.count(b"/Type /Page") - pdf.count(b"/Type /Pages") == 3
        )  # the long slip moves on, and runs on


def test_a_line_after_urdu_names_its_own_font() -> None:
    """Labels and slips: a line drawn after one in a fallback font must not print fontless."""
    pdf = ShopPdf((105.0, 148.0), 5)
    pdf.set_compression(False)
    pdf.add_page()
    for text in ("چینی", "1 x Sugar", "Lahore"):
        pdf.text_line(text, 10)
    page = bytes(pdf.output())
    # Sans is named once among the resources and again before each line (it was 2 before).
    assert page.count(b"/F1 ") >= 4


class TestDeliveryMoney:
    @pytest.mark.parametrize(
        ("amount", "weights", "shares"),
        [
            (100, [1, 1, 1], [34, 33, 33]),
            (250, [3_000, 1_000], [188, 62]),
            (7, [0, 0], [4, 3]),
            (0, [5, 5], [0, 0]),
            (9, [], []),
        ],
    )
    def test_a_split_adds_up_to_the_whole(
        self, amount: int, weights: list[int], shares: list[int]
    ) -> None:
        assert split(amount, weights) == shares
        assert sum(split(amount, weights)) == (amount if weights else 0)

    def _parcel(self, status: str, out: int | None, back: int | None = None) -> Delivery:
        shipment = Shipment(courier_charge=out, return_charge=back, dispatched_at=utcnow())
        return parcel(order("AB12CD", status=status, delivery_fee=20_000), shipment)

    def test_a_kept_order_pays_its_courier_from_its_delivery_charge(self) -> None:
        assert self._parcel("delivered", 25_000) == Delivery(fees=20_000, courier=25_000)
        assert self._parcel("delivered", 25_000).cost == 5_000

    def test_a_parcel_that_came_back_costs_both_ways(self) -> None:
        assert self._parcel("returned", 25_000, 15_000) == Delivery(returns=40_000)
        assert self._parcel("cancelled", 25_000) == Delivery(returns=25_000)
        assert self._parcel("delivered", 25_000, 10_000).returns == 10_000  # a customer's return

    def test_an_unknown_charge_is_counted_apart_and_its_fee_left_out(self) -> None:
        assert self._parcel("delivered", None) == Delivery(unknown=1)
        assert self._parcel("returned", None, 5_000) == Delivery(returns=5_000, unknown=1)

    def test_true_profit_takes_off_delivery_and_returns(self) -> None:
        row = TrueProfit(
            product=None,  # type: ignore[arg-type]
            sold=Sold(units=2, revenue=100_000, cost=60_000),
            delivery=Delivery(fees=20_000, courier=25_000, returns=10_000),
        )
        assert row.profit == 100_000 - 60_000 - (25_000 + 10_000 - 20_000)
