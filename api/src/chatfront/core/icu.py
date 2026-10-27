"""ICU MessageFormat for the string catalog: {arguments}, number, plural and select.

The web app formats the same catalog with intl-messageformat; i18n/icu-cases.json holds the cases
both sides must agree on. Quoting follows ICU: '' is an apostrophe, and an apostrophe starts a
quoted literal only before { or } (or # inside a plural), so "shop's" needs no escaping.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from functools import lru_cache

from babel import Locale

from chatfront.core.languages import Language
from chatfront.core.numbers import cldr_locale, format_number


class MessageSyntaxError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class Arg:
    name: str
    number: bool = False


@dataclass(frozen=True, slots=True)
class Choice:
    name: str
    kind: str  # "plural" or "select"
    options: dict[str, tuple[Node, ...]]
    offset: int = 0


class Pound:
    """The # inside a plural: the number, formatted for the reader."""


POUND = Pound()
Node = str | Arg | Choice | Pound


class _Parser:
    def __init__(self, text: str) -> None:
        self.text = text
        self.i = 0

    def fail(self, problem: str) -> MessageSyntaxError:
        return MessageSyntaxError(f"{problem} at {self.i} in {self.text!r}")

    def peek(self) -> str:
        return self.text[self.i] if self.i < len(self.text) else ""

    def skip_space(self) -> None:
        while self.peek().isspace():
            self.i += 1

    def expect(self, char: str) -> None:
        self.skip_space()
        if self.peek() != char:
            raise self.fail(f"expected {char!r}")
        self.i += 1

    def word(self) -> str:
        self.skip_space()
        start = self.i
        while self.peek() and not self.peek().isspace() and self.peek() not in "{},":
            self.i += 1
        if start == self.i:
            raise self.fail("expected a name")
        return self.text[start : self.i]

    def message(self, depth: int, in_plural: bool) -> tuple[Node, ...]:
        nodes: list[Node] = []
        literal: list[str] = []

        def flush() -> None:
            if literal:
                nodes.append("".join(literal))
                literal.clear()

        while (char := self.peek()) != "":
            if char == "'":
                literal.append(self.apostrophe(in_plural))
            elif char == "{":
                flush()
                nodes.append(self.argument())
            elif char == "}":
                if depth == 0:
                    raise self.fail("unmatched '}'")
                break
            elif char == "#" and in_plural:
                flush()
                nodes.append(POUND)
                self.i += 1
            else:
                literal.append(char)
                self.i += 1
        flush()
        return tuple(nodes)

    def apostrophe(self, in_plural: bool) -> str:
        after = self.text[self.i + 1 : self.i + 2]
        if after == "'":
            self.i += 2
            return "'"
        if not (after in ("{", "}") or (after == "#" and in_plural)):
            self.i += 1
            return "'"
        self.i += 1
        quoted: list[str] = []
        while self.i < len(self.text):
            char = self.text[self.i]
            if char == "'":
                if self.text[self.i + 1 : self.i + 2] == "'":
                    quoted.append("'")
                    self.i += 2
                    continue
                self.i += 1
                break
            quoted.append(char)
            self.i += 1
        return "".join(quoted)

    def argument(self) -> Node:
        self.i += 1  # past "{"
        name = self.word()
        self.skip_space()
        if self.peek() == "}":
            self.i += 1
            return Arg(name)
        self.expect(",")
        kind = self.word()
        if kind == "number":
            self.expect("}")
            return Arg(name, number=True)
        if kind not in ("plural", "select"):
            raise self.fail(f"unsupported argument type {kind!r}")
        self.expect(",")
        return self.choice(name, kind)

    def choice(self, name: str, kind: str) -> Choice:
        offset = 0
        options: dict[str, tuple[Node, ...]] = {}
        while True:
            self.skip_space()
            if self.peek() == "}":
                self.i += 1
                break
            selector = self.word()
            if kind == "plural" and selector.startswith("offset:"):
                offset = int(selector.removeprefix("offset:"))
                continue
            self.expect("{")
            options[selector] = self.message(depth=1, in_plural=kind == "plural")
            self.expect("}")
        if "other" not in options:
            raise self.fail(f"{kind} needs an 'other' option")
        return Choice(name, kind, options, offset)


@lru_cache(maxsize=1024)
def parse(message: str) -> tuple[Node, ...]:
    return _Parser(message).message(depth=0, in_plural=False)


@lru_cache(maxsize=64)
def _plural_rules(locale: str) -> Locale:
    return Locale.parse(cldr_locale(locale))


def _render(
    nodes: tuple[Node, ...], language: Language, values: dict[str, object], pound: Decimal | None
) -> str:
    out: list[str] = []
    for node in nodes:
        if isinstance(node, str):
            out.append(node)
        elif isinstance(node, Pound):
            assert pound is not None
            out.append(format_number(pound, language))
        elif isinstance(node, Arg):
            value = values[node.name]
            out.append(format_number(_number(value), language) if node.number else str(value))
        else:
            out.append(_render_choice(node, language, values, pound))
    return "".join(out)


def _number(value: object) -> Decimal:
    if isinstance(value, bool) or not isinstance(value, int | Decimal | str):
        raise TypeError(f"not a number: {value!r}")
    return Decimal(value)


def _render_choice(
    node: Choice, language: Language, values: dict[str, object], pound: Decimal | None
) -> str:
    value = values[node.name]
    if node.kind == "select":
        branch = node.options.get(str(value), node.options["other"])
        return _render(branch, language, values, pound)
    number = _number(value)
    exact = node.options.get(f"={number.normalize():f}")
    if exact is None:
        keyword = _plural_rules(language.cldr).plural_form(number - node.offset)
        exact = node.options.get(keyword, node.options["other"])
    return _render(exact, language, values, number - node.offset)


def format_message(message: str, language: Language, values: dict[str, object]) -> str:
    """Format one catalog message for a reader of this language."""
    return _render(parse(message), language, values, None)
