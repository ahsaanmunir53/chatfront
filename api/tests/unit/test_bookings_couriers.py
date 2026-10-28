"""Rules behind bookings, custom orders and couriers, without a database: the free times of a
day (feature 18), the taps that book or answer a price (17, 18), the courier's words and sheet
(31, 32), the rider's photo (35), the unboxing advice (36) and the sketch code draws (17)."""

from __future__ import annotations

import io
import uuid
from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo

import pytest
from PIL import Image

from chatfront.bookings import tools as booking_tools
from chatfront.bookings.hours import DEFAULT_DAYS, Hours, free_times, hours_of, parse_span
from chatfront.bookings.service import day_of, minutes_of
from chatfront.catalog.models import Product
from chatfront.core.errors import AppError
from chatfront.delivery import settlements
from chatfront.delivery.couriers import Stage, parcel_of, stage_of
from chatfront.delivery.models import Shipment
from chatfront.delivery.proof import ProofReading, matches
from chatfront.media.mockups import Kind, colour_of, sketch
from chatfront.orders import custom
from chatfront.orders.models import Order
from chatfront.orders.unboxing import Advice, Unboxing, advise

KARACHI = ZoneInfo("Asia/Karachi")
MONDAY = date(2026, 11, 2)


def open_hours(span: str = "11:00-20:00", seats: int = 1) -> Hours:
    return Hours({day: parse_span(span) for day in DEFAULT_DAYS}, seats)


def at(hour: int, minute: int = 0, day: date = MONDAY) -> datetime:
    return datetime.combine(day, time(hour, minute), KARACHI)


class TestHours:
    def test_spans(self) -> None:
        assert parse_span("11:00-20:00") == (time(11), time(20))
        assert parse_span(" 9:30 - 13:00 ") == (time(9, 30), time(13))
        assert parse_span("00:00-24:00") == (time(0), time(23, 59))
        assert parse_span("") is None

    @pytest.mark.parametrize("bad", ["9 to 5", "20:00-11:00", "25:00-26:00", "11-20"])
    def test_bad_spans(self, bad: str) -> None:
        with pytest.raises(AppError):
            parse_span(bad)

    def test_a_shop_without_hours_opens_monday_to_saturday(self) -> None:
        hours = hours_of(None)
        assert hours.days["mon"] == (time(11), time(20))
        assert hours.days["sun"] is None
        assert hours.seats == 1
        assert hours_of({"hours": {"seats": 99}}).seats == 20

    def test_times_fit_before_closing(self) -> None:
        early = at(0)  # long before the shop opens
        times = free_times(open_hours(), MONDAY, 60, [], KARACHI, early)
        local = [t.astimezone(KARACHI) for t in times]
        assert local[0] == at(11)
        assert local[-1] == at(19)  # an hour's service ends at closing
        assert all(t.tzinfo == ZoneInfo("UTC") for t in times)
        assert len(local) == 17  # every half hour from 11:00 to 19:00

    def test_a_time_too_soon_is_not_offered(self) -> None:
        times = free_times(open_hours(), MONDAY, 30, [], KARACHI, at(14, 10))
        assert times[0].astimezone(KARACHI) == at(15, 30)  # an hour's notice, on the half hour

    def test_taken_times_count_against_the_seats(self) -> None:
        taken = [(at(12), at(13))]
        one = free_times(open_hours(), MONDAY, 60, taken, KARACHI, at(0))
        two = free_times(open_hours(seats=2), MONDAY, 60, taken, KARACHI, at(0))
        local = {t.astimezone(KARACHI) for t in one}
        assert at(12) not in local
        assert at(11, 30) not in local  # 11:30 to 12:30 overlaps
        assert at(11) in local
        assert at(13) in local
        assert at(12) in {t.astimezone(KARACHI) for t in two}

    def test_a_closed_day_has_no_times(self) -> None:
        sunday = MONDAY - timedelta(days=1)
        assert free_times(hours_of(None), sunday, 30, [], KARACHI, at(0, day=sunday)) == []


class TestDays:
    def test_words_for_days(self) -> None:
        today = MONDAY
        assert day_of("today", today) == today
        assert day_of("kal", today) == today + timedelta(days=1)
        assert day_of("Friday", today) == date(2026, 11, 6)
        assert day_of("monday", today) == today + timedelta(days=7)  # the next one
        assert day_of("2026-11-20", today) == date(2026, 11, 20)

    @pytest.mark.parametrize("words", ["someday", "2026-10-01", "2027-01-30"])
    def test_days_code_will_not_take(self, words: str) -> None:
        with pytest.raises(AppError):
            day_of(words, MONDAY)

    def test_a_service_has_a_length(self) -> None:
        assert minutes_of(Product(attributes={"duration_minutes": "45"})) == 45
        assert minutes_of(Product(attributes={"duration_minutes": "two hours"})) is None
        assert minutes_of(Product(attributes={"duration_minutes": "2"})) is None
        assert minutes_of(Product(attributes={})) is None


class TestTaps:
    def test_a_time_tapped(self) -> None:
        product = uuid.uuid4()
        moment = datetime(2026, 11, 3, 6, 0, tzinfo=UTC)
        tap = f"bk:{product}:{int(moment.timestamp())}"
        assert booking_tools.parse_button(tap) == (product, moment)
        assert booking_tools.parse_button("bk:nope:1") is None
        assert booking_tools.parse_button("cq:x") is None
        assert booking_tools.parse_button(None) is None

    def test_a_time_picked_in_the_form(self) -> None:
        product = uuid.uuid4()
        moment = datetime(2026, 11, 3, 6, 0, tzinfo=UTC)
        token = f"flow:book:{product}"
        picked = {"slot": str(int(moment.timestamp()))}
        assert booking_tools.parse_flow(token, picked) == (product, moment)
        assert booking_tools.parse_flow(token, {}) is None
        assert booking_tools.parse_flow("flow:product:x", picked) is None
        assert booking_tools.parse_flow(None, picked) is None

    def test_a_price_answered(self) -> None:
        request = uuid.uuid4()
        assert custom.parse_button(f"cq:{request}:yes") == (request, True)
        assert custom.parse_button(f"cq:{request}:no") == (request, False)
        assert custom.parse_button("cq:nope") is None
        assert custom.parse_button("bk:1:2") is None


class TestCouriers:
    @pytest.mark.parametrize(
        ("words", "stage"),
        [
            ("Delivered", Stage.DELIVERED),
            ("Delivery Complete", Stage.DELIVERED),
            ("Returned to shipper", Stage.RETURNED),
            ("Return in process", Stage.RETURNED),
            ("Refused by customer", Stage.RETURNED),
            ("Out for delivery", Stage.ON_THE_WAY),
            ("Booked", Stage.ON_THE_WAY),
            ("", Stage.ON_THE_WAY),
        ],
    )
    def test_the_courier_words(self, words: str, stage: Stage) -> None:
        assert stage_of(words) == stage

    def test_a_parcel_from_an_order(self) -> None:
        order = Order(
            code="ORD1",
            address={
                "name": "Bilal",
                "phone": "+923001112223",
                "city": "Lahore",
                "address": "House 12, Street 4, Gulberg III",
            },
        )
        parcel = parcel_of(order, 63_000, 2, "Sugar x2", 2)
        assert (parcel.phone, parcel.city, parcel.cod) == ("03001112223", "Lahore", 63_000)
        assert parcel.address == "House 12, Street 4, Gulberg III"


class TestSheets:
    def test_columns_found_by_their_usual_names(self) -> None:
        data = (
            b"Sr,CN #,Order Ref,Current Status,Invoice Amount,Service Charges,Net Payable\n"
            b'1,tc123,ord9,Delivered,"4,850",150,"4,700"\n'
            b",,,,,,\n"
        )
        [row] = settlements.read(data, 2)
        assert (row.tracking, row.order, row.status) == ("TC123", "ORD9", "Delivered")
        assert (row.cod, row.charges, row.net) == (485_000, 15_000, 470_000)

    def test_a_sheet_without_a_parcel_column_is_refused(self) -> None:
        with pytest.raises(AppError) as caught:
            settlements.read(b"Colour,Size\nred,M\n", 2)
        assert caught.value.code == "no_tracking_column"

    def test_the_same_row_is_one_key(self) -> None:
        row = settlements.Row("T1", "", "Delivered", 100, 10, 90)
        assert settlements.row_key(row) == settlements.row_key(row)
        assert settlements.row_key(row) != settlements.row_key(
            settlements.Row("T1", "", "Delivered", 100, 10, 80)
        )

    def test_short_is_what_did_not_arrive_after_charges(self) -> None:
        shipment = Shipment(cod_amount=63_000, courier_charge=None)
        assert (
            settlements.short_by(shipment, settlements.Row("T", "", "", None, 15_000, 48_000)) == 0
        )
        assert (
            settlements.short_by(shipment, settlements.Row("T", "", "", None, 15_000, 40_000))
            == 8_000
        )
        assert settlements.short_by(shipment, settlements.Row("T", "", "", None, None, None)) == 0


class TestProofAndUnboxing:
    def test_a_photo_matches_only_on_the_whole_code(self) -> None:
        seen = ProofReading(parcel_visible=True, codes_seen=["ord-9a2b"])
        assert matches(seen, "ORD9A2B", None)
        assert matches(ProofReading(parcel_visible=True, codes_seen=["CX1001"]), "X", "cx 1001")
        assert not matches(ProofReading(parcel_visible=True, codes_seen=["ORD9"]), "ORD9A2B", None)
        assert not matches(ProofReading(parcel_visible=False), "ORD9A2B", "T1")

    def test_only_an_unbroken_video_of_a_sealed_parcel_counts(self) -> None:
        good = Unboxing(seal_intact_at_start=True, filmed_without_cut=True, damage_seen=True)
        assert advise(good, "damaged") == Advice.SUPPORTS
        assert advise(good.model_copy(update={"damage_seen": False}), "damaged") == Advice.AGAINST
        cut = good.model_copy(update={"filmed_without_cut": False})
        assert advise(cut, "damaged") == Advice.UNCLEAR
        assert advise(good.model_copy(update={"seal_intact_at_start": None}), "damaged") == (
            Advice.UNCLEAR
        )

    def test_a_wrong_item(self) -> None:
        sealed = Unboxing(seal_intact_at_start=True, filmed_without_cut=True)
        other = sealed.model_copy(update={"item_matches_catalog": False})
        same = sealed.model_copy(update={"item_matches_catalog": True})
        assert advise(other, "wrong_item") == Advice.SUPPORTS
        assert advise(same, "wrong_item") == Advice.AGAINST
        assert advise(sealed, "wrong_item") == Advice.UNCLEAR
        assert advise(same, "changed_mind") == Advice.UNCLEAR


class TestSketches:
    @pytest.mark.parametrize("kind", list(Kind))
    def test_each_kind_is_a_square_picture(self, kind: Kind) -> None:
        made = Image.open(
            io.BytesIO(sketch(kind, "Happy Birthday Ali", ["pink", "white"], ["2 pounds"]))
        )
        assert made.size == (1080, 1080)

    def test_colours_customers_name(self) -> None:
        assert colour_of("Gulabi") == colour_of("pink")
        assert colour_of("gold") == colour_of("golden")
        assert colour_of("teal-ish") == colour_of(None)
