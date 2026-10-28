"""Rules behind privacy, scams and the books ahead, without a database: the official-number
check (feature 41), the copy link (44), the call guardian's verdict (39), the STIX patterns
(47), the tax year and zakat (62), the weeks of the forecast (58), staff pay (60), the seasons
(55) and the coach's lines (65)."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

import pytest

from chatfront.books import forecast, staff_pay, year
from chatfront.channels.models import ChannelAccount
from chatfront.channels.types import OutButtons, OutText
from chatfront.core.countries import get_pack
from chatfront.core.errors import AppError
from chatfront.core.i18n import t
from chatfront.core.models import Tenant
from chatfront.insights import coach, seasons
from chatfront.shield import data_rights, guardian, official, stix
from chatfront.shield.guardian import CallReading, Verdict
from chatfront.shield.models import ScamKind, ScamReport

PK = get_pack("PK")
NOW = datetime(2026, 10, 28, 10, 0, tzinfo=UTC)


ACCOUNTS = (
    ChannelAccount(channel="whatsapp", public_handle="923000000000", display="+92 300 ***0000"),
    ChannelAccount(channel="instagram", public_handle="gulmohar.lawn", display="@gulmohar.lawn"),
    ChannelAccount(channel="webchat", public_handle=None, display=None),
)


class TestOfficial:
    @pytest.mark.parametrize(
        ("value", "channel"),
        [
            ("03000000000", "whatsapp"),
            ("0092 300 0000000", "whatsapp"),
            ("api.whatsapp.com/send?phone=923000000000&text=hi", "whatsapp"),
            ("IG.ME/m/gulmohar.lawn", "instagram"),
            ("gulmohar_lawn", None),
            ("03000000001", None),
            ("   ", None),
        ],
    )
    def test_values(self, value: str, channel: str | None) -> None:
        found = official.check(ACCOUNTS, value, PK)
        assert (found.channel if found else None) == channel


class TestCopyLink:
    def test_a_token_works_for_a_day_for_one_customer(self) -> None:
        import uuid

        shop, person = uuid.uuid4(), uuid.uuid4()
        token = data_rights.copy_token("secret", shop, person, NOW)
        assert data_rights.read_token("secret", token, NOW) == (shop, person)
        assert data_rights.read_token("other", token, NOW) is None
        later = NOW + data_rights.COPY_FOR + timedelta(minutes=1)
        assert data_rights.read_token("secret", token, later) is None
        assert data_rights.read_token("secret", token[:-2] + "xx", NOW) is None
        assert data_rights.read_token("secret", "a.b", NOW) is None


class TestGuardian:
    @pytest.mark.parametrize(
        ("reading", "verdict"),
        [
            (CallReading(asks_for_code=True), Verdict.SCAM),
            (CallReading(asks_for_pin_or_password=True, posing_as="family"), Verdict.SCAM),
            (CallReading(posing_as="fia", threat_or_hurry=True), Verdict.SCAM),
            (CallReading(posing_as="courier", asks_for_money=True), Verdict.SCAM),
            (CallReading(posing_as="bank"), Verdict.SUSPICIOUS),
            (CallReading(posing_as="family", asks_for_money=True), Verdict.SUSPICIOUS),
            (CallReading(posing_as="family"), Verdict.NO_SIGNS),
            (CallReading(), Verdict.NO_SIGNS),
        ],
    )
    def test_code_decides(self, reading: CallReading, verdict: Verdict) -> None:
        assert guardian.judge(reading) == verdict

    def test_the_reply_offers_a_report_only_for_a_suspect(self) -> None:
        scam = CallReading(posing_as="jazzcash", asks_for_code=True, numbers=["0301 2345678"])
        [card] = guardian.reply(scam, "en")
        assert isinstance(card, OutButtons)
        assert card.buttons[0].id == "sr:phone:jazzcash:0301 2345678"
        assert t("guardian.rule_code", "en") in card.text
        [plain] = guardian.reply(CallReading(numbers=["0301 2345678"]), "en")
        assert isinstance(plain, OutText)

    def test_buttons_and_kinds(self) -> None:
        assert guardian.parse_button("sr:wallet:easypaisa:03451234567") == (
            ScamKind.WALLET,
            "easypaisa",
            "03451234567",
        )
        assert guardian.parse_button("sr:bad:x:y") is None
        assert guardian.parse_button("sr:phone:x:") is None
        assert guardian.parse_button("ap:1:ok") is None
        assert guardian.kind_of("+92 301-2345678") == ScamKind.PHONE
        assert guardian.kind_of("PK36SCBL0000001123456702") == ScamKind.WALLET


class TestStix:
    def report(self, kind: ScamKind, value: str, posing: str = "jazzcash") -> ScamReport:
        return ScamReport(
            kind=kind,
            value=value,
            posing_as=posing,
            source="voice",
            created_at=NOW,
            reviewed_at=NOW,
        )

    def test_patterns(self) -> None:
        assert stix.pattern(self.report(ScamKind.URL, "http://x.test/a'b")) == (
            "[url:value = 'http://x.test/a\\'b']"
        )
        wallet = stix.pattern(self.report(ScamKind.WALLET, "03451234567"))
        assert "account_type = 'jazzcash'" in wallet

    def test_one_indicator_however_many_shops_report_it(self) -> None:
        same = [self.report(ScamKind.PHONE, "+923012345678") for _ in range(3)]
        bundle = stix.bundle(same, NOW)
        assert [o["type"] for o in bundle["objects"]] == ["identity", "indicator"]
        assert bundle["objects"][1]["created"] == "2026-10-28T10:00:00.000Z"
        assert stix.bundle(same, NOW)["id"] == bundle["id"]


class TestYearAndZakat:
    def test_tax_years(self) -> None:
        pk = Tenant(country="PK")
        assert year.year_of(pk, date(2026, 10, 28)) == (date(2026, 7, 1), date(2027, 6, 30))
        assert year.year_of(pk, date(2026, 3, 1)) == (date(2025, 7, 1), date(2026, 6, 30))
        assert year.year_of(Tenant(country="AE"), date(2026, 3, 1)) == (
            date(2026, 1, 1),
            date(2026, 12, 31),
        )
        assert year.starting(pk, 2025) == (date(2025, 7, 1), date(2026, 6, 30))

    def test_zakat_is_a_fortieth_above_the_nisab(self) -> None:
        found = year.zakat(400_000, 300_000, 5, 100_000, 200_000, 500_000)
        assert (found.net, found.due, found.stock_without_cost) == (600_000, 15_000, 5)
        assert year.zakat(100, 0, 0, 0, 0, 500_000).due == 0
        assert year.zakat(100, 0, 0, 0, 1_000, 0).due == 0  # no nisab given, nothing worked out
        assert year.zakat(100, 0, 0, 0, 1_000, 1).net == 0  # debts above assets
        with pytest.raises(AppError):
            year.zakat(-1, 0, 0, 0, 0, 1)


class TestForecast:
    def test_a_fixed_cost_on_the_31st_falls_on_the_last_day(self) -> None:
        rent = forecast.Fixed("rent", 100, 31)
        assert forecast._due_in((rent,), date(2027, 2, 22)) == (rent,)  # 28 February
        assert forecast._due_in((rent,), date(2027, 2, 15)) == ()

    def test_a_short_week_and_its_words(self) -> None:
        tenant = Tenant(country="PK", currency="PKR")
        week = forecast.Week(
            date(2026, 11, 2), 10, 200_000, (forecast.Fixed("rent", 150_000, 5),), -50_000
        )
        assert week.short
        text = forecast.short_text(tenant, week, "en")
        assert "02 Nov" in text
        assert "Rs 500" in text
        assert forecast.Forecast(0, 0, (), None, None, (week,)).first_short == week

    def test_the_usual_week_counts_from_the_first_entry(self) -> None:
        assert forecast._usual([0, 0, 10, 20], 2) == 15
        assert forecast._usual([], 0) == 0


class TestStaffPay:
    def test_months_and_pay(self) -> None:
        assert staff_pay.month_of("2026-10") == date(2026, 10, 1)
        with pytest.raises(AppError):
            staff_pay.month_of("Oct")
        with pytest.raises(AppError):
            staff_pay.month_of("2026-13")
        pay = staff_pay.Pay(None, "Ali", 100, 500, 1, 2_000, 100, None)  # type: ignore[arg-type]
        assert pay.due == 200


class TestSeasons:
    def test_month_windows(self) -> None:
        weddings = seasons._month_window("weddings", [11, 12, 1, 2], 2026)
        assert (weddings.starts, weddings.ends) == (date(2026, 11, 1), date(2027, 2, 28))
        lawn = seasons._month_window("summer_lawn", [3, 4, 5], 2027)
        assert (lawn.starts, lawn.ends) == (date(2027, 3, 1), date(2027, 5, 31))

    def test_seasons_ahead_with_last_years(self) -> None:
        found = {w.key: (w, last) for w, last, _ in seasons.windows("PK", date(2026, 12, 25))}
        assert {"weddings", "winter", "ramadan"} <= set(found)
        ramadan, last = found["ramadan"]
        assert ramadan.starts == date(2027, 2, 8) - timedelta(days=21)
        assert last is not None
        assert last.starts == date(2026, 2, 19) - timedelta(days=21)
        assert "independence_pk" not in found
        assert seasons.windows("XX", date(2026, 12, 25)) == []


class TestCoach:
    def test_lines_from_facts(self) -> None:
        facts = coach.Facts(
            week=date(2026, 10, 26),
            falling=(coach.Moved("lawn", 10.0, 5),),
            rising=(coach.Moved("dupattas", 4.0, 8),),
            slow=("Kurta",),
            slow_count=1,
            returned=2,
            delivered=6,
            waiting_chats=1,
        )
        lines = coach.lines(facts, "en")
        assert lines[0] == t("coach.falling", "en", category="lawn", change=50)
        assert lines[1] == t("coach.rising", "en", category="dupattas", change=100)
        assert len(lines) == 5
        assert coach.Moved("x", 0, 3).change == 0
