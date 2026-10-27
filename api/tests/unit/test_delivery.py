"""Delivery rules: tidy addresses and when a pin is needed (33), pins from messages and maps
links, the COD risk score (34), the advance it asks for, and the shipping label (101)."""

from __future__ import annotations

import pytest

from chatfront.channels.base import Unsupported, check_sendable
from chatfront.channels.messaging import messenger_adapter
from chatfront.channels.models import Channel
from chatfront.channels.types import (
    Capability,
    OutLocationRequest,
    OutText,
    required_capability,
    to_record,
)
from chatfront.channels.whatsapp import _body as whatsapp_body
from chatfront.core.clock import utcnow
from chatfront.core.countries import get_pack
from chatfront.core.models import Tenant
from chatfront.delivery.address import Address, landmark, local_phone, phone_for_people, tidy
from chatfront.delivery.labels import LabelData, label_pdf
from chatfront.delivery.pins import from_message, from_text, pin_request
from chatfront.delivery.risk import History, RiskFacts, band, score
from chatfront.orders.models import Order, OrderItem
from chatfront.payments.due import cod_advance

PK = get_pack("PK")


class TestAddresses:
    @pytest.mark.parametrize(
        ("typed", "clean"),
        [
            ("H# 12, st# 4 , Gulberg III", "House 12, Street 4, Gulberg III"),
            ("makan no 5 gali 7 Ichra", "House 5 Street 7 Ichra"),
            ("h.no. 23-B, Blk C, Ph 6 DHA", "House 23-B, Block C, Phase 6 DHA"),
            ("Flat 3,, Block 7,Clifton.", "Flat 3, Block 7, Clifton"),
        ],
    )
    def test_short_forms_are_spelled_out(self, typed: str, clean: str) -> None:
        assert tidy(typed) == clean

    def test_landmarks_read_as_landmarks(self) -> None:
        assert landmark("Jamia masjid ke paas") == "near Jamia masjid"
        assert landmark("opp. PSO pump") == "opposite PSO pump"
        assert landmark("Barkat market") == "near Barkat market"
        assert landmark("  ") == ""

    def test_parts_make_one_line(self) -> None:
        address = Address(
            "x", house="H# 12", street="gali 4", area="Ichra", landmark="masjid ke paas"
        )
        assert address.clean() == "House 12, Street 4, Ichra, near masjid"
        assert not address.vague()

    @pytest.mark.parametrize(
        ("address", "vague"),
        [
            (Address("Gulberg"), True),
            (Address("Gulberg Lahore near the park"), False),  # a landmark is enough
            (Address("House 12, Street 4, Gulberg III"), False),
            (Address("main road wala ghar Lahore"), True),  # no number, no landmark
            (Address("x", area="Ichra"), True),
            (Address("x", area="Ichra", landmark="Bilal masjid"), False),
            (Address("x", house="12", street="4"), True),  # no area
        ],
    )
    def test_too_vague_for_a_rider(self, address: Address, vague: bool) -> None:
        assert address.vague() is vague

    def test_phone_numbers(self) -> None:
        assert local_phone("0300-1234567", PK) == ("+923001234567", True)
        assert local_phone("+92 300 1234567", PK) == ("+923001234567", True)
        assert local_phone("12345", PK) == ("12345", False)
        assert local_phone("+44 20 7946 0958", PK) == ("+44 20 7946 0958", False)
        assert phone_for_people("+923001234567", PK) == "0300 1234567"
        assert phone_for_people("+442079460958", PK) == "+442079460958"


class TestPins:
    def test_a_shared_location(self) -> None:
        payload = {"location": {"latitude": 31.5204, "longitude": 74.3587, "name": "Home"}}
        pin = from_message(payload, "whatsapp")
        assert pin is not None
        assert (pin.lat, pin.lng, pin.label, pin.source) == (31.5204, 74.3587, "Home", "whatsapp")
        assert pin.maps_url == "https://maps.google.com/?q=31.520400,74.358700"
        assert from_message({"location": {"latitude": 95, "longitude": 10}}, "web") is None
        assert from_message({"location": {"latitude": 0, "longitude": 0}}, "web") is None
        assert from_message({}, "web") is None

    @pytest.mark.parametrize(
        "text",
        [
            "yeh lo https://maps.google.com/?q=31.5204,74.3587",
            "https://www.google.com/maps/@31.5204,74.3587,17z",
            "https://www.google.com/maps/place/Home/@31.5204,74.3587,15z/data=x",
            "https://maps.google.com/maps?ll=31.5204,74.3587&z=16",
        ],
    )
    def test_maps_links_with_coordinates(self, text: str) -> None:
        pin = from_text(text)
        assert pin is not None
        assert (pin.lat, pin.lng, pin.source) == (31.5204, 74.3587, "link")

    def test_links_without_coordinates_and_plain_text(self) -> None:
        assert from_text("https://maps.app.goo.gl/AbCdEf123") is None
        assert from_text("31.5, 74.3 is where I am") is None
        assert from_text(None) is None

    def test_the_request_fits_the_channel(self) -> None:
        assert isinstance(pin_request(Channel.WHATSAPP, "en"), OutLocationRequest)
        assert isinstance(pin_request(Channel.WEBCHAT, "en"), OutLocationRequest)
        assert isinstance(pin_request(Channel.MESSENGER, "en"), OutText)

    def test_whatsapp_asks_with_its_own_button(self) -> None:
        request = OutLocationRequest("Send your pin")
        assert whatsapp_body(request)["interactive"] == {
            "type": "location_request_message",
            "body": {"text": "Send your pin"},
            "action": {"name": "send_location"},
        }
        assert to_record(request) == ("Send your pin", {"location_request": True})
        assert required_capability(request) == Capability.LOCATION
        with pytest.raises(Unsupported):
            check_sendable(messenger_adapter(None, "", ""), request, utcnow(), utcnow())  # type: ignore[arg-type]


def facts(**changes: object) -> RiskFacts:
    values: dict[str, object] = {
        "history": History(),
        "verified_buyer": False,
        "phone_proven": True,
        "phone_valid": True,
        "has_pin": False,
        "total": 100_000,
        "typical_total": None,
    } | changes
    return RiskFacts(**values)  # type: ignore[arg-type]


class TestRisk:
    def test_a_first_order_on_whatsapp_is_low(self) -> None:
        risk = score(facts())
        assert (risk.score, risk.band) == (15, "low")
        assert [r["code"] for r in risk.reasons] == ["first_order"]

    def test_a_buyer_who_returned_an_order_is_high(self) -> None:
        risk = score(facts(history=History(returned=1), phone_proven=False))
        assert (risk.score, risk.band, risk.high) == (60, "high", True)
        assert {"code": "returned_before", "points": 35, "count": 1} in risk.reasons

    def test_good_history_and_a_pin_bring_it_down_but_not_below_nothing(self) -> None:
        risk = score(facts(history=History(delivered=5), has_pin=True, verified_buyer=True))
        assert risk.score == 0
        assert [r["code"] for r in risk.reasons] == [
            "delivered_before",
            "verified_buyer",
            "pin_shared",
        ]

    def test_each_sign_adds_up_and_stops_at_a_hundred(self) -> None:
        many = facts(
            history=History(returned=3, cancelled=5, open_cod=4),
            phone_proven=False,
            phone_valid=False,
            total=900_000,
            typical_total=200_000,
        )
        risk = score(many)
        assert risk.score == 100
        assert {r["code"] for r in risk.reasons} == {
            "returned_before",
            "cancelled_before",
            "first_order",
            "open_cod_orders",
            "phone_unproven",
            "phone_invalid",
            "big_order",
        }

    def test_bands(self) -> None:
        assert [band(n) for n in (0, 29, 30, 59, 60, 100)] == [
            "low",
            "low",
            "medium",
            "medium",
            "high",
            "high",
        ]

    def test_a_risky_order_asks_for_an_advance_where_the_shop_takes_transfers(self) -> None:
        grocery = Tenant(template="grocery", country="PK", profile={})  # cod, credit, transfer
        assert cod_advance(grocery, 300_000, 15_000) == 0
        assert cod_advance(grocery, 300_000, 15_000, risky=True) == 15_000
        off = Tenant(
            template="grocery", country="PK", profile={"autonomy": {"risk_advance": "never"}}
        )
        assert cod_advance(off, 300_000, 15_000, risky=True) == 0
        cod_only = Tenant(template="grocery", country="PK", profile={"payment_methods": ["cod"]})
        assert cod_advance(cod_only, 300_000, 15_000, risky=True) == 0


class TestLabels:
    def test_a_label_in_several_scripts(self) -> None:
        tenant = Tenant(name="Gulmohar Lawn House", country="PK", profile={"city": "Lahore"})
        order = Order(
            code="AB12CD",
            total=499_900,
            paid=0,
            payment_method="cod",
            address={
                "name": "عائشہ خان",
                "phone": "+923001234567",
                "address": "House 12, Street 4, Gulberg III",
                "city": "Lahore",
                "pin": {"lat": 31.5204, "lng": 74.3587},
            },
        )
        items = [
            OrderItem(name=f"Printed lawn {n}", options={"colour": "Rust"}, quantity=1)
            for n in range(8)
        ] + [OrderItem(name="لان سوٹ", options={}, quantity=2)]
        pdf = label_pdf(
            LabelData(
                tenant, order, items, 499_900, "https://example.test/t/ABCDEFGHJK", "TCS", "77"
            )
        )
        assert pdf.startswith(b"%PDF-")
        assert len(pdf) > 5_000
        paid = label_pdf(LabelData(tenant, order, items[:1], 0, "https://example.test/t/X"))
        assert paid.startswith(b"%PDF-")
