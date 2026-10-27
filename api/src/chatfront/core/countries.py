"""Country packs, i18n/countries/<CC>.json: money, numbers, dates, phones and wallets by country.

Money is an integer number of the currency's minor units; how many there are comes from CLDR.
Formatting uses CLDR through Babel, the same data the web app reads through Intl.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import date
from functools import lru_cache
from pathlib import Path
from typing import Literal

from babel import Locale, UnknownLocaleError
from babel.dates import format_date as babel_format_date
from babel.numbers import (
    UnsupportedNumberingSystemError,
    get_currency_precision,
    get_decimal_symbol,
)

from chatfront.core.languages import Language
from chatfront.core.money import Minor
from chatfront.core.numbers import cldr_locale, format_number, to_digits
from chatfront.core.settings import get_settings

DEFAULT_COUNTRY = "PK"


@dataclass(frozen=True, slots=True)
class Symbol:
    symbol: str
    position: Literal["before", "after"]


@dataclass(frozen=True, slots=True)
class Phone:
    calling_code: str
    trunk_prefix: str
    national_lengths: tuple[int, ...]
    example: str


@dataclass(frozen=True, slots=True)
class Wallet:
    id: str
    name: str
    kind: Literal["wallet", "bank"]
    parser: str | None = None
    source: Literal["sms", "notification"] = "sms"


@dataclass(frozen=True, slots=True)
class CountryPack:
    code: str
    name: str
    currency: str
    symbol: Symbol
    show_minor: Literal["nonzero", "always", "never"]
    number_locale: str
    grouping: str
    date_style: Literal["short", "medium", "long"]
    timezone: str
    phone: Phone
    tax_label: str
    languages: tuple[str, ...]
    wallets: tuple[Wallet, ...]
    local_symbols: dict[str, Symbol] = field(default_factory=dict)

    @property
    def minor_digits(self) -> int:
        """Minor units per major unit, as a power of ten (2 for paisa, 0 for dong)."""
        return int(get_currency_precision(self.currency))

    def symbol_for(self, language: Language) -> Symbol:
        return self.local_symbols.get(language.tag, self.symbol)


def _pack(raw: dict[str, object]) -> CountryPack:
    money = raw["money"]
    phone = raw["phone"]
    assert isinstance(money, dict)
    assert isinstance(phone, dict)
    return CountryPack(
        code=str(raw["code"]),
        name=str(raw["name"]),
        currency=str(raw["currency"]),
        symbol=Symbol(money["symbol"], money["position"]),
        show_minor=money["show_minor"],
        number_locale=str(raw["number_locale"]),
        grouping=str(raw["grouping"]),
        date_style=raw["date_style"],  # type: ignore[arg-type]
        timezone=str(raw["timezone"]),
        phone=Phone(
            phone["calling_code"],
            phone["trunk_prefix"],
            tuple(phone["national_lengths"]),
            phone["example"],
        ),
        tax_label=str(raw["tax_label"]),
        languages=tuple(raw["languages"]),  # type: ignore[arg-type]
        wallets=tuple(Wallet(**item) for item in raw["wallets"]),  # type: ignore[attr-defined]
        local_symbols={
            tag: Symbol(item["symbol"], item["position"])
            for tag, item in money["local_symbols"].items()
        },
    )


@lru_cache
def load_packs(directory: Path | None = None) -> dict[str, CountryPack]:
    base = (directory or get_settings().i18n_dir) / "countries"
    packs = [
        _pack(json.loads(path.read_text(encoding="utf-8"))) for path in sorted(base.glob("*.json"))
    ]
    return {pack.code: pack for pack in packs}


def get_pack(code: str | None) -> CountryPack:
    """The pack for a country code; Pakistan when there is none for it."""
    packs = load_packs()
    return packs.get((code or "").upper(), packs[DEFAULT_COUNTRY])


def format_money(amount: Minor, pack: CountryPack, language: Language) -> str:
    """'Rs 4,850', '4,850 روپے', '৳ ১২,৩৪,৫৬৭', 'Rs 4,850.50': the pack's grouping and
    symbol, the reader's digits. Whole amounts drop the minor part unless the pack says not to."""
    digits = pack.minor_digits
    major, minor = divmod(abs(amount), 10**digits)
    number = format_number(major, language, locale=pack.number_locale)
    show = pack.show_minor == "always" or (pack.show_minor == "nonzero" and minor)
    if show and digits:
        decimal = _decimal_symbol(pack.number_locale, language.digits)
        number += decimal + to_digits(f"{minor:0{digits}d}", language.digits)
    sign = "-" if amount < 0 else ""
    symbol = pack.symbol_for(language)
    if symbol.position == "before":
        return f"{sign}{symbol.symbol} {number}"
    return f"{sign}{number} {symbol.symbol}"


def _decimal_symbol(locale: str, numbering_system: str) -> str:
    try:
        return get_decimal_symbol(cldr_locale(locale), numbering_system=numbering_system)
    except UnsupportedNumberingSystemError:
        return get_decimal_symbol(cldr_locale(locale), numbering_system="latn")


def date_locale(pack: CountryPack, language: Language) -> str:
    """The reader's language in the pack's country when CLDR has it (ur_PK), else the language."""
    regional = f"{cldr_locale(language.cldr)}_{pack.code}"
    try:
        Locale.parse(regional)
    except (UnknownLocaleError, ValueError):
        return cldr_locale(language.cldr)
    return regional


def format_day(day: date, pack: CountryPack, language: Language) -> str:
    text = babel_format_date(day, format=pack.date_style, locale=date_locale(pack, language))
    return to_digits(text, language.digits)
