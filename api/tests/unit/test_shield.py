"""Shield logic without a database: fake support links and code requests (40), card and ID
numbers masked (43), the fraud lists in the risk score (37), the trust page's reply time (45)
and the cleaning and signing of reviews (46)."""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta

import httpx
import pytest
import respx
from pydantic import SecretStr

from chatfront.channels.models import Direction
from chatfront.core.i18n import t
from chatfront.core.settings import get_settings
from chatfront.delivery.risk import History, RiskFacts, score
from chatfront.orders.models import Order
from chatfront.shield import phishing, privacy, reviews, trust
from chatfront.shield.fraud import Flags
from chatfront.shield.phishing import Link, Scan, Verdict, check

SAFE_BROWSING = "https://safebrowsing.googleapis.com/v4/threatMatches:find"


class TestLinks:
    @pytest.mark.parametrize(
        ("url", "verdict", "brand", "why"),
        [
            ("https://www.whatsapp.com/contact", Verdict.OFFICIAL, "whatsapp", None),
            ("https://business.facebook.com/x", Verdict.OFFICIAL, "facebook", None),
            ("wa.me/923001234567", Verdict.OFFICIAL, "whatsapp", None),
            ("http://whatsapp-support-team.xyz/verify", Verdict.FAKE, "whatsapp", "lookalike"),
            ("https://watsapp.com/appeal", Verdict.FAKE, "whatsapp", "lookalike"),
            ("https://faceb00k-login.com", Verdict.FAKE, "facebook", "lookalike"),
            ("https://meta-support.help/case", Verdict.FAKE, "meta", "lookalike"),
            ("https://jazzcash-refund.online", Verdict.FAKE, "jazzcash", "lookalike"),
            ("https://xn--80ak6aa92e.com", Verdict.RISKY, None, "punycode"),
            ("http://192.168.1.10/login", Verdict.RISKY, None, "ip"),
            ("https://bit.ly/3abc", Verdict.RISKY, None, "shortener"),
            ("https://cheap-deals.top/win", Verdict.RISKY, None, "throwaway"),
            ("https://metal-works.pk", Verdict.UNKNOWN, None, None),
            ("https://beta.example.com", Verdict.UNKNOWN, None, None),
        ],
    )
    def test_each_link_gets_its_verdict(
        self, url: str, verdict: Verdict, brand: str | None, why: str | None
    ) -> None:
        link = check(url)
        assert (link.verdict, link.brand, link.why) == (verdict, brand, why)

    def test_links_are_found_once_without_trailing_punctuation(self) -> None:
        text = "See https://bit.ly/x, then www.whatsapp.com. Also gulmohar.pk! https://bit.ly/x"
        assert phishing.links_in(text) == [
            "https://bit.ly/x",
            "www.whatsapp.com",
            "gulmohar.pk",
        ]
        assert phishing.full_links(text) == ["https://bit.ly/x", "www.whatsapp.com"]
        assert phishing.links_in(None) == []

    @pytest.mark.parametrize(
        ("text", "asks"),
        [
            ("WhatsApp support: send me the code you got", True),
            ("bhai jo code aaya hai woh bata do", True),
            ("mujhe OTP bhejo jaldi", True),
            ("براہ کرم کوڈ بھیجیں", True),
            ("कृपया ओटीपी भेजें", True),
            ("أرسل الرمز الآن", True),
            ("OTP nahi aaya abhi tak", False),
            ("discount code hai koi?", False),
            ("lawn suit ka price?", False),
        ],
    )
    def test_a_request_for_a_code_is_heard_in_any_language(self, text: str, asks: bool) -> None:
        assert phishing.says(text, phishing.CODE_WORDS) is asks

    def test_the_alarm_names_the_worst_sign(self) -> None:
        fake = Link("x", "watsapp.com", Verdict.FAKE, "whatsapp", "lookalike")
        own = Link("y", "whatsapp.com", Verdict.OFFICIAL, "whatsapp")
        short = Link("z", "bit.ly", Verdict.RISKY, None, "shortener")
        assert Scan([fake], asks_for_code=True).alarm == "fake_link"
        assert Scan([], asks_for_code=True).alarm == "asks_for_code"
        assert Scan([short], claims_support=True).alarm == "fake_support"
        assert Scan([own], claims_support=True).alarm is None  # the brand's own help page
        assert Scan([short]).alarm is None  # a shortener alone is only worth a warning

    def test_the_owner_reads_each_verdict(self) -> None:
        lines = [
            phishing.link_line(check(url), "en")
            for url in ("https://whatsapp.com", "https://watsapp.com", "https://bit.ly/x", "a.pk")
        ]
        assert lines == [
            t("shield.link_official", "en", host="whatsapp.com", brand="Whatsapp"),
            t("shield.link_fake", "en", host="watsapp.com", brand="Whatsapp"),
            t("shield.link_shortener", "en", host="bit.ly"),
            t("shield.link_unknown", "en", host="a.pk"),
        ]
        listed = Link("u", "evil.example", Verdict.FAKE, None, "listed")
        assert phishing.link_line(listed, "en") == t(
            "shield.link_listed", "en", host="evil.example"
        )


class TestSafeBrowsing:
    async def test_without_a_key_nothing_is_asked(self) -> None:
        async with httpx.AsyncClient() as http:
            scan = await phishing.scan(http, get_settings(), "https://shop-deals.example/x")
        assert [link.verdict for link in scan.links] == [Verdict.UNKNOWN]

    @respx.mock
    async def test_a_listed_link_is_fake_and_official_ones_are_not_sent(self) -> None:
        route = respx.post(SAFE_BROWSING).respond(
            json={"matches": [{"threat": {"url": "https://prize-claim.example/win"}}]}
        )
        settings = get_settings().model_copy(update={"safe_browsing_api_key": SecretStr("k")})
        text = "https://prize-claim.example/win and https://whatsapp.com"
        async with httpx.AsyncClient() as http:
            scan = await phishing.scan(http, settings, text)
        assert [(link.verdict, link.why) for link in scan.links] == [
            (Verdict.FAKE, "listed"),
            (Verdict.OFFICIAL, None),
        ]
        assert scan.alarm == "fake_link"
        sent = route.calls.last.request
        assert sent.url.params["key"] == "k"
        assert b"whatsapp.com" not in sent.content

    @respx.mock
    async def test_when_google_does_not_answer_code_still_decides(self) -> None:
        respx.post(SAFE_BROWSING).respond(503)
        settings = get_settings().model_copy(update={"safe_browsing_api_key": SecretStr("k")})
        async with httpx.AsyncClient() as http:
            scan = await phishing.scan(http, settings, "https://watsapp.com")
        assert scan.alarm == "fake_link"


class TestMasking:
    @pytest.mark.parametrize(
        ("text", "out", "kinds"),
        [
            ("Mera CNIC 35202-1234567-1 hai", "Mera CNIC *****-*******-1 hai", ["cnic"]),
            ("cnic 3520212345671", "cnic *****-*******-1", ["cnic"]),
            ("card 4111 1111 1111 1111 pe", "card **** **** **** 1111 pe", ["card"]),
            ("4111111111111111", "**** **** **** 1111", ["card"]),
            ("4111 1111 1111 1112", "4111 1111 1111 1112", []),  # fails the Luhn check
            ("order 3520212345671", "order 3520212345671", []),  # 13 digits, nothing says CNIC
            ("0300 1234567 pe call karo", "0300 1234567 pe call karo", []),
        ],
    )
    def test_card_and_id_numbers_are_masked(self, text: str, out: str, kinds: list[str]) -> None:
        assert privacy.masked(text) == (out, kinds)

    def test_nothing_to_mask(self) -> None:
        assert privacy.masked(None) == (None, [])
        assert privacy.masked("") == ("", [])

    def test_luhn(self) -> None:
        assert privacy.luhn("79927398713")
        assert not privacy.luhn("79927398710")


def facts(fraud: Flags) -> RiskFacts:
    return RiskFacts(
        history=History(),
        verified_buyer=False,
        phone_proven=True,
        phone_valid=True,
        has_pin=False,
        total=100_000,
        typical_total=None,
        fraud=fraud,
    )


class TestFraudInRisk:
    def test_a_number_this_shop_reported_is_high(self) -> None:
        risk = score(facts(Flags(reported_here=True)))
        assert risk.high
        assert {"code": "reported_here", "points": 60} in risk.reasons

    @pytest.mark.parametrize(("shops", "points"), [(1, 40), (2, 50), (5, 60)])
    def test_the_shared_list_counts_more_with_each_shop(self, shops: int, points: int) -> None:
        risk = score(facts(Flags(listed_by=shops)))
        assert {"code": "on_fraud_list", "points": points, "count": shops} in risk.reasons

    def test_a_clean_number_has_no_fraud_reason(self) -> None:
        codes = {r["code"] for r in score(facts(Flags())).reasons}
        assert not codes & {"reported_here", "on_fraud_list"}


class TestTrust:
    def test_first_reply_is_timed_once_per_wait(self) -> None:
        start = datetime(2026, 10, 28, 10, 0)
        a, b = uuid.uuid4(), uuid.uuid4()

        def at(minutes: int) -> datetime:
            return start + timedelta(minutes=minutes)

        rows: list[tuple[object, str, datetime]] = [
            (a, Direction.IN, at(0)),
            (a, Direction.IN, at(1)),  # still the same wait
            (a, Direction.OUT, at(3)),
            (a, Direction.OUT, at(4)),  # a second reply is not a first reply
            (b, Direction.OUT, at(5)),  # the shop wrote first: nothing to time
            (b, Direction.IN, at(6)),
            (b, Direction.OUT, at(16)),
            (a, Direction.IN, at(20)),  # waiting still, no reply yet
        ]
        assert trust.first_replies(rows) == [3.0, 10.0]


class TestReviews:
    def test_numbers_links_and_cards_are_left_out(self) -> None:
        text = "Zabardast! Call 0300 1234567, see https://x.example or card 4111111111111111"
        assert reviews.clean(text) == "Zabardast! Call …, see … or card **** **** **** 1111"
        assert reviews.clean("  bohat   acha  ") == "bohat acha"
        assert len(reviews.clean("a" * 900)) == reviews.MAX_TEXT

    def test_the_link_is_signed_for_one_order(self) -> None:
        order, other = Order(id=uuid.uuid4()), Order(id=uuid.uuid4())
        mark = reviews.signature("secret", order.id)
        assert reviews.signed("secret", order, mark)
        assert not reviews.signed("secret", other, mark)
        assert not reviews.signed("another", order, mark)

    def test_only_the_first_name_shows(self) -> None:
        assert reviews.first_name("Ayesha Khan") == "Ayesha"
        assert reviews.first_name("  ") is None
        assert reviews.first_name(None) is None
