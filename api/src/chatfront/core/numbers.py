"""Numbers as people write and read them.

Writing: CLDR grouping and separators, in the reader's own digits. Reading: any digits, any
grouping (1,00,000 or 100,000), k/lakh/crore, and number words (i18n/numbers.json).
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from decimal import Decimal
from functools import lru_cache
from pathlib import Path

from babel.numbers import UnsupportedNumberingSystemError, format_decimal

from chatfront.core.languages import Language
from chatfront.core.settings import get_settings

# The ten digits of every numbering system Chatfront reads or writes.
DIGITS: dict[str, str] = {
    "latn": "0123456789",
    "arab": "٠١٢٣٤٥٦٧٨٩",  # Arabic-Indic
    "arabext": "۰۱۲۳۴۵۶۷۸۹",  # Extended Arabic-Indic: Urdu, Persian, Pashto, Sindhi
    "beng": "০১২৩৪৫৬৭৮৯",
    "deva": "०१२३४५६७८९",
    "guru": "੦੧੨੩੪੫੬੭੮੯",
    "gujr": "૦૧૨૩૪૫૬૭૮૯",
    "taml": "௦௧௨௩௪௫௬௭௮௯",
    "telu": "౦౧౨౩౪౫౬౭౮౯",
    "knda": "೦೧೨೩೪೫೬೭೮೯",
    "mlym": "൦൧൨൩൪൫൬൭൮൯",
    "thai": "๐๑๒๓๔๕๖๗๘๙",
}


def to_digits(text: str, system: str) -> str:
    """Write the Latin digits in text in another numbering system."""
    return text.translate(str.maketrans(DIGITS["latn"], DIGITS[system]))


def cldr_locale(code: str) -> str:
    """Babel spells locales with underscores; the registry and browsers use hyphens."""
    return code.replace("-", "_")


def format_number(value: int | Decimal, language: Language, locale: str | None = None) -> str:
    """Group and punctuate by CLDR (the language's locale unless one is given), then write the
    digits the reader uses. Intl.NumberFormat with -u-nu-<digits> gives the same in the browser."""
    code = cldr_locale(locale or language.cldr)
    try:
        text = format_decimal(value, locale=code, numbering_system=language.digits)
    except UnsupportedNumberingSystemError:
        text = format_decimal(value, locale=code, numbering_system="latn")
    return to_digits(text, language.digits)


# -- reading numbers -------------------------------------------------------------------------

_TO_LATIN = str.maketrans(
    "".join(digits for system, digits in DIGITS.items() if system != "latn"),
    DIGITS["latn"] * (len(DIGITS) - 1),
)
_ARABIC_SEPARATORS = str.maketrans({"٫": ".", "٬": ","})


def latin_digits(text: str) -> str:
    """Every digit in Latin form: '٤٥٠٠', '۴۵۰۰', '৪৫০০' and '४५००' all become '4500'."""
    return text.translate(_TO_LATIN).translate(_ARABIC_SEPARATORS)


@dataclass(frozen=True, slots=True)
class _Table:
    units: dict[str, Decimal]
    scales: dict[str, Decimal]
    doubles: dict[str, Decimal]
    fractions: dict[str, Decimal]
    modifiers: dict[str, Decimal]
    joiners: frozenset[str]
    ambiguous: frozenset[str]

    def kind(self, word: str) -> str | None:
        for kind in ("units", "scales", "doubles", "fractions", "modifiers"):
            if word in getattr(self, kind):
                return kind
        return None

    def value(self, word: str) -> Decimal:
        for kind in ("units", "scales", "doubles", "fractions", "modifiers"):
            table: dict[str, Decimal] = getattr(self, kind)
            if word in table:
                return table[word]
        raise KeyError(word)


_EMPTY = _Table({}, {}, {}, {}, {}, frozenset(), frozenset())


@dataclass(frozen=True, slots=True)
class _Words:
    tables: dict[str, _Table]
    languages: dict[str, str]
    suffixes: dict[str, Decimal]

    def table(self, language: str | None) -> _Table:
        name = self.languages.get(language or "")
        return self.tables[name] if name else _EMPTY


def _decimals(raw: dict[str, float | int]) -> dict[str, Decimal]:
    return {word: Decimal(str(value)) for word, value in raw.items()}


@lru_cache
def _load_words(directory: Path | None = None) -> _Words:
    base = directory or get_settings().i18n_dir
    raw = json.loads((base / "numbers.json").read_text(encoding="utf-8"))
    tables = {
        name: _Table(
            units=_decimals(table["units"]),
            scales=_decimals(table["scales"]),
            doubles=_decimals(table["doubles"]),
            fractions=_decimals(table["fractions"]),
            modifiers=_decimals(table["modifiers"]),
            joiners=frozenset(table["joiners"]),
            ambiguous=frozenset(table["ambiguous"]),
        )
        for name, table in raw["tables"].items()
    }
    return _Words(tables, raw["languages"], _decimals(raw["suffixes"]))


_TOKEN = re.compile(r"(?P<number>\d+(?:[.,]\d+)*)(?P<suffix>[a-z]{1,4}\b)?|(?P<word>[^\s\d]+)")
_EDGE_PUNCTUATION = ".,!?;:()[]{}\"'“”‘’،؟؛।…-/"


@dataclass(slots=True)
class _Token:
    kind: str  # "digits", one of the _Table kinds, "joiner" or "other"
    value: Decimal = Decimal(0)


def _digits_value(raw: str, decimal: str) -> Decimal:
    """'4,850.50' -> 4850.50 and '1,00,000' -> 100000; with decimal=',' '1.234,5' -> 1234.5."""
    grouping = "," if decimal == "." else "."
    if raw.count(decimal) > 1:  # '1.234.567' written with dots for thousands
        raw, grouping = raw.replace(decimal, ""), ""
    cleaned = raw.replace(grouping, "") if grouping else raw
    return Decimal(cleaned.replace(decimal, "."))


def _tokens(text: str, table: _Table, suffixes: dict[str, Decimal], decimal: str) -> list[_Token]:
    tokens: list[_Token] = []
    for match in _TOKEN.finditer(latin_digits(text).lower()):
        if match.group("number"):
            value = _digits_value(match.group("number"), decimal)
            suffix = match.group("suffix")
            if suffix in suffixes:
                tokens.append(_Token("digits", value))
                tokens.append(_Token("scales", suffixes[suffix]))
            else:
                tokens.append(_Token("digits", value))
                if suffix:
                    tokens.append(_Token("other"))
            continue
        tokens.extend(_word_tokens(match.group("word"), table, suffixes))
    return _drop_lonely_ambiguous(tokens)


def _word_tokens(raw: str, table: _Table, suffixes: dict[str, Decimal]) -> list[_Token]:
    word = raw.strip(_EDGE_PUNCTUATION)
    if word in table.joiners:
        return [_Token("joiner")]
    kind = table.kind(word)
    if kind:
        ambiguous = "ambiguous" if word in table.ambiguous else ""
        return [_Token(kind + ambiguous, table.value(word))]
    if word in suffixes:
        return [_Token("scales", suffixes[word])]
    for joiner in table.joiners:  # Arabic writes 'and' joined on: 'وخمسمية'
        rest = word.removeprefix(joiner)
        if rest != word and (kind := table.kind(rest)):
            return [_Token("joiner"), _Token(kind, table.value(rest))]
    return [_Token("other")]


def _drop_lonely_ambiguous(tokens: list[_Token]) -> list[_Token]:
    """'do' is two only next to another number ('do sau', 'sawa do'); alone it means 'give'."""
    numeric = {"digits", "units", "scales", "doubles", "fractions", "modifiers"}
    kept: list[_Token] = []
    for index, token in enumerate(tokens):
        if not token.kind.endswith("ambiguous"):
            kept.append(token)
            continue
        neighbours = tokens[max(index - 1, 0) : index] + tokens[index + 1 : index + 2]
        near = any(n.kind in numeric or n.kind.endswith("ambiguous") for n in neighbours)
        kind = token.kind.removesuffix("ambiguous") if near else "other"
        kept.append(_Token(kind, token.value))
    return kept


@dataclass(slots=True)
class _Reading:
    total: Decimal = Decimal(0)
    current: Decimal = Decimal(0)
    modifier: Decimal = Decimal(0)
    last_scale: Decimal = Decimal(0)
    last: str = ""
    started: bool = False

    def value(self) -> Decimal:
        value = self.total + self.current
        return value.quantize(Decimal(1)) if value == value.to_integral_value() else value


def _read(tokens: list[_Token]) -> list[Decimal]:
    found: list[Decimal] = []
    reading = _Reading()

    def finish() -> None:
        nonlocal reading
        if reading.started:
            found.append(reading.value())
        reading = _Reading()

    for token in tokens:
        if token.kind in ("other",) or (token.kind == "joiner" and not reading.started):
            finish()
            continue
        if token.kind == "joiner":
            reading.last = "joiner"
            continue
        if token.kind == "digits" and reading.last == "digits":
            finish()  # '2 3' is two numbers
        _apply(reading, token)
    finish()
    return found


def _apply(reading: _Reading, token: _Token) -> None:
    after_scale = reading.last == "scales" or (reading.last == "joiner" and reading.last_scale)
    if token.kind == "modifiers":
        reading.modifier = token.value
    elif token.kind == "fractions" and after_scale and reading.last_scale >= 1000:
        reading.total += token.value * reading.last_scale  # 'ألف ونص' = 1500
    elif token.kind in ("digits", "units", "fractions"):
        reading.current += token.value + reading.modifier
        reading.modifier = Decimal(0)
    elif token.kind == "doubles":
        reading.total += token.value
        reading.last_scale = token.value / 2
    elif token.kind == "scales":
        count = reading.current or (1 + reading.modifier)
        reading.modifier = Decimal(0)
        if token.value >= 1000:
            reading.total += count * token.value
            reading.current = Decimal(0)
        else:
            reading.current = count * token.value
        reading.last_scale = token.value
    reading.started = reading.started or token.kind != "modifiers"
    reading.last = token.kind


def find_numbers(text: str, language: str | None = None, *, decimal: str = ".") -> list[Decimal]:
    """Every number in a message, however it is written: '4500', '٤٥٠٠', '4,500', '1,00,000',
    '1.5k', '2 lakh', 'do sau', 'dedh hazaar', 'saadhe teen sau', 'ڈھائی ہزار', 'ألف ونص'.

    Words are read in the given language (a registry tag); digits and k/lakh/crore always."""
    words = _load_words()
    return _read(_tokens(text, words.table(language), words.suffixes, decimal))


def parse_number(text: str, language: str | None = None, *, decimal: str = ".") -> Decimal | None:
    numbers = find_numbers(text, language, decimal=decimal)
    return numbers[0] if numbers else None
