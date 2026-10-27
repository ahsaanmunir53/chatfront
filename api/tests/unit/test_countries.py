"""Country packs: money and number formats from CLDR, phones, languages, wallet parsers."""

from __future__ import annotations

import json
import re
from datetime import date
from pathlib import Path

import pytest
from babel.numbers import format_decimal

from chatfront.core.countries import date_locale, format_day, format_money, get_pack, load_packs
from chatfront.core.languages import load_registry
from chatfront.core.money import Minor
from chatfront.core.phone import normalise_phone
from chatfront.payments.sms import PARSERS, parse_sms, parsers_for

FIXTURES = Path(__file__).parents[1] / "fixtures" / "sms"
# Countries the hackathon rules exclude. No pack may exist for them.
EXCLUDED = {"BY", "CN", "CU", "IR", "KP", "RU", "SY", "UA"}


def test_pakistan_is_the_default() -> None:
    assert get_pack(None).code == "PK"
    assert get_pack("xx").code == "PK"
    assert get_pack("bd").code == "BD"


def test_no_pack_for_excluded_countries() -> None:
    packs = load_packs()
    assert not EXCLUDED & set(packs)
    assert len(packs) >= 20


@pytest.mark.parametrize("code", sorted(load_packs()))
def test_every_pack_is_consistent(code: str) -> None:
    pack = load_packs()[code]
    registry = load_registry()
    for tag in pack.languages:
        assert registry.resolve(tag) is not None, (code, tag)
    for tag in pack.local_symbols:
        assert tag in pack.languages, (code, tag)
    expected = {"3": "1,234,567", "3;2": "12,34,567"}[pack.grouping]
    grouped = format_decimal(1234567, locale=pack.number_locale.replace("-", "_"))
    assert re.sub(r"\D", ",", grouped) == expected
    for wallet in pack.wallets:
        if wallet.parser:
            assert wallet.parser in PARSERS, (code, wallet.parser)
            assert PARSERS[wallet.parser].country == code
            assert PARSERS[wallet.parser].source == wallet.source
    phone = pack.phone
    e164 = normalise_phone(
        phone.example,
        phone.calling_code,
        trunk_prefix=phone.trunk_prefix,
        national_lengths=phone.national_lengths,
    )
    assert e164.startswith("+" + phone.calling_code)
    assert len(e164) - 1 - len(phone.calling_code) in phone.national_lengths


@pytest.mark.parametrize(
    ("code", "tag", "amount", "expected"),
    [
        ("PK", "en", 485_000, "Rs 4,850"),
        ("PK", "ur-Latn", 485_050, "Rs 4,850.50"),
        ("PK", "ur", 485_000, "4,850 روپے"),
        ("PK", "sd", 10_000_000, "100,000 رپيا"),
        ("IN", "hi", 12_345_678_900, "₹ 12,34,56,789"),
        ("BD", "bn", 123_456_700, "৳ ১২,৩৪,৫৬৭"),
        ("BD", "en", 123_456_750, "৳ 12,34,567.50"),
        ("EG", "arz", 150_000, "١٬٥٠٠ ج.م"),
        ("ZA", "en", 150_050, "R 1\u00a0500,50"),
        ("SA", "en", 15_050, "SAR 150.50"),
        ("ID", "id", 15_000_000, "Rp 150.000"),
        ("BR", "pt-BR", 115_050, "R$ 1.150,50"),
        ("VN", "vi", 1_500_000, "1.500.000 ₫"),
        ("PK", "en", -50_000, "-Rs 500"),
    ],
)
def test_money(code: str, tag: str, amount: int, expected: str) -> None:
    language = load_registry().get(tag)
    assert format_money(Minor(amount), get_pack(code), language) == expected


def test_minor_units_come_from_cldr() -> None:
    assert get_pack("PK").minor_digits == 2
    assert get_pack("VN").minor_digits == 0


def test_dates_in_the_readers_language() -> None:
    registry = load_registry()
    pk = get_pack("PK")
    assert date_locale(pk, registry.get("ur")) == "ur_PK"
    assert date_locale(pk, registry.get("hi-Latn")) == "hi"
    assert format_day(date(2026, 11, 4), pk, registry.get("en")) == "04-Nov-2026"  # CLDR en_PK
    assert "২০২৬" in format_day(date(2026, 11, 4), get_pack("BD"), registry.get("bn"))


def _fixtures() -> list[tuple[str, dict[str, object]]]:
    cases = []
    for path in sorted(FIXTURES.glob("*.json")):
        data = json.loads(path.read_text(encoding="utf-8"))
        cases += [(data["parser"], sample) for sample in data["samples"]]
    return cases


def test_every_parser_has_fixtures_with_a_credit_and_a_non_credit() -> None:
    covered: dict[str, list[bool]] = {}
    for parser_id, sample in _fixtures():
        covered.setdefault(parser_id, []).append(sample["expect"] is not None)
    assert set(covered) == set(PARSERS)
    for parser_id, kinds in covered.items():
        assert True in kinds, parser_id
        assert False in kinds, parser_id


@pytest.mark.parametrize(("parser_id", "sample"), _fixtures())
def test_parser_on_fixture(parser_id: str, sample: dict[str, object]) -> None:
    parser = PARSERS[parser_id]
    assert parser.trusts(str(sample["sender"]))
    credit = parser.parse(str(sample["body"]))
    expected = sample["expect"]
    if expected is None:
        assert credit is None
        return
    assert credit is not None
    assert isinstance(expected, dict)
    assert (credit.amount, credit.transaction_id, credit.payer) == (
        expected["amount"],
        expected["tid"],
        expected["payer"],
    )
    assert credit.currency == parser.currency
    assert credit.verified is parser.verified


def test_a_parser_is_verified_only_with_a_real_sample() -> None:
    real = {parser_id for parser_id, sample in _fixtures() if sample["real"]}
    for parser in PARSERS.values():
        assert parser.verified == (parser.id in real), parser.id


def test_parse_sms_trusts_only_known_senders() -> None:
    body = "You have received Rs 1,000.00 from Ali Test. TID: 123456789012."
    credit = parse_sms("JazzCash", body, "pk")
    assert credit is not None
    assert (credit.parser, credit.amount, credit.verified) == ("pk.jazzcash", 100_000, False)
    assert parse_sms("+923001234567", body, "PK") is None  # a forged "JazzCash" text
    assert parse_sms("JazzCash", body, "BD") is None
    assert {p.id for p in parsers_for("PK")} == {"pk.jazzcash", "pk.easypaisa", "pk.bank"}
