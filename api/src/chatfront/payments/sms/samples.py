"""Wallet messages no parser could read, kept to add the format (feature 73).

A forwarded SMS that no parser reads is normally dropped and never stored. A shop that agrees
to help can instead keep the ones that look like a wallet or bank message: from a sender one
of its country's parsers trusts, or from a named sender (not a person's phone number) with an
amount in the country's currency. Only a shape of the text is kept: every digit becomes 9, an
id that mixes letters and digits becomes A and 9, an email becomes EMAIL and the words after
"from", "by" or "to" become NAME. That is enough to write a pattern, and it names no one.
Chatfront's own staff see the samples next to every parser, how often it read real money, and
whether it has passed on a real message yet.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import delete, func, select
from sqlalchemy.exc import IntegrityError

from chatfront.core.clock import utcnow
from chatfront.core.countries import CountryPack, get_pack
from chatfront.core.numbers import latin_digits
from chatfront.payments.models import SmsSample, WalletCredit
from chatfront.payments.sms.registry import PARSERS, parsers_for

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from chatfront.core.models import Tenant

SHARE = "share_unread_sms"  # the shop's profile: True once the owner agrees
KEPT_AT_MOST = 100  # unhandled samples per shop
TEXT_AT_MOST = 1000
NAME_WORDS_AT_MOST = 4
PHONE_DIGITS = 7  # a sender with this many digits is a person, not a wallet

_LEADS = frozenset({"from", "frm", "by", "to", "sender", "de", "dari", "kutoka", "mula", "na"})
_STOPS = frozenset(
    {
        "on", "at", "ref", "via", "with", "account", "acc", "a/c", "for", "your", "in", "has",
        "is", "was", "the", "trxid", "txn", "tid", "id", "balance", "bal", "and", "new", "pada",
        "tarehe", "em", "el", "le", "no", "number", "mobile", "wallet",
    }
)  # fmt: skip
_TRAILING = frozenset({"se", "sy", "say", "থেকে", "से", "سے", "کان", "څخه"})  # "Ali se"
ABBREVIATIONS = {  # how wallet messages write the money, besides the code and the symbol
    "PKR": ("Rs",),
    "INR": ("Rs",),
    "NPR": ("Rs",),
    "LKR": ("Rs",),
    "BDT": ("Tk", "Taka"),
    "KES": ("Ksh",),
    "GHS": ("GH₵", "GHC"),
    "PHP": ("PHP",),
    "IDR": ("Rp",),
    "BRL": ("R$",),
    "EGP": ("LE", "EGP"),
    "SAR": ("SR", "SAR"),
}
_EMAIL = re.compile(r"\S+@\S+\.\S+")
_MIXED = re.compile(r"\d[A-Za-z]")
_WORD = re.compile(r"[A-Za-z0-9]+")
_NAME_TOKEN = re.compile(r"^(?P<word>[^\W\d_][\w'.-]*?)(?P<tail>[.,;:]*)$")
SHAPE_FROM = 5  # a letters-and-digits id this long or longer is shaped


def _shape(match: re.Match[str]) -> str:
    """An id with a letter after a digit ("QJK7XYZ123") keeps only its shape; a currency
    stuck to an amount ("Ksh250") or a prefixed code ("ORD123") keeps its letters."""
    word = match.group(0)
    if len(word) >= SHAPE_FROM and _MIXED.search(word):
        return "".join("9" if c.isdigit() else "A" for c in word)
    return word


def _names(text: str) -> str:
    """Up to four words after "from", "by" or "to" become one NAME, until a number, a
    stop word or punctuation ends the name."""
    out: list[str] = []
    left = 0
    for token in text.split(" "):
        found = _NAME_TOKEN.match(token)
        if left and found and found["word"].lower() not in _STOPS:
            if out[-1] != "NAME":
                out.append("NAME")
            left -= 1
            if found["tail"]:
                out[-1], left = "NAME" + found["tail"], 0
            continue
        left = NAME_WORDS_AT_MOST if token.lower().strip(":") in _LEADS else 0
        if token.lower().strip(".,") in _TRAILING:
            _name_before(out)
        out.append(token)
    return " ".join(out)


def _name_before(out: list[str]) -> None:
    """The name in "Ali Raza se": capitalised words just before (one word where the script
    has no capitals)."""
    taken = 0
    while out and taken < NAME_WORDS_AT_MOST // 2:
        word = out[-1]
        found = _NAME_TOKEN.match(word)
        cased = word[:1].upper() != word[:1].lower()
        if not found or found["tail"] or word.lower() in _STOPS:
            break
        if cased and not word[:1].isupper():
            break
        out.pop()
        taken += 1
        if not cased:
            break
    if taken:
        out.append("NAME")


def redact(body: str) -> str:
    """The shape of a message: no digit, id, email or name survives."""
    text = " ".join(latin_digits(body).split())
    text = _EMAIL.sub("EMAIL", text)
    text = _names(text)
    text = _WORD.sub(_shape, text)
    return re.sub(r"\d", "9", text)[:TEXT_AT_MOST]


def _money_pattern(pack: CountryPack) -> re.Pattern[str]:
    marks = {
        pack.currency,
        pack.symbol.symbol,
        *(s.symbol for s in pack.local_symbols.values()),
        *ABBREVIATIONS.get(pack.currency, ()),
    }
    either = "|".join(re.escape(m) for m in sorted(marks, key=len, reverse=True))
    return re.compile(
        rf"(?<![A-Za-z])(?:{either})\.?\s?\d|\d\s?(?:{either})(?![A-Za-z])", re.IGNORECASE
    )


def money_like(sender: str, body: str, country: str) -> bool:
    """A wallet or bank message, not a person's text."""
    if any(parser.trusts(sender) for parser in parsers_for(country)):
        return True
    if sum(c.isdigit() for c in sender) >= PHONE_DIGITS:
        return False
    return bool(_money_pattern(get_pack(country)).search(latin_digits(body)))


def sharing(tenant: Tenant) -> bool:
    return bool((tenant.profile or {}).get(SHARE))


def digest(country: str, sender: str, text: str) -> str:
    return hashlib.sha256(f"{country}\n{sender.lower()}\n{text}".encode()).hexdigest()


async def keep(session: AsyncSession, tenant: Tenant, sender: str, body: str) -> bool:
    """Keep the shape of a message no parser read, when the shop agreed and it looks like
    money. The same shape again counts once more, and reopens it if it was handled."""
    sender = sender.strip()[:40]
    if not sharing(tenant) or not money_like(sender, body, tenant.country):
        return False
    text = redact(body)
    key = digest(tenant.country, sender, text)
    now = utcnow()
    found = await session.scalar(select(SmsSample).where(SmsSample.text_hash == key))
    if found is not None:
        found.seen += 1
        found.last_seen_at = now
        found.handled_at = None
        return True
    waiting = await session.scalar(
        select(func.count()).select_from(SmsSample).where(SmsSample.handled_at.is_(None))
    )
    if (waiting or 0) >= KEPT_AT_MOST:
        return False
    try:
        async with session.begin_nested():
            session.add(
                SmsSample(
                    tenant_id=tenant.id,
                    country=tenant.country,
                    sender=sender,
                    text=text,
                    text_hash=key,
                    seen=1,
                    last_seen_at=now,
                )
            )
    except IntegrityError:  # the same shape, forwarded twice at once
        return True
    return True


async def stop_sharing(session: AsyncSession) -> int:
    """The owner said no: the shop's samples go."""
    result = await session.execute(delete(SmsSample))
    return int(getattr(result, "rowcount", 0) or 0)


@dataclass(frozen=True, slots=True)
class Format:
    parser: str
    provider: str
    country: str
    currency: str
    verified: bool
    credits: int
    last_seen: datetime | None


async def formats(session: AsyncSession) -> list[Format]:
    """Every parser, with the real money it read across shops (a system session)."""
    used = {
        parser: (count, last)
        for parser, count, last in (
            await session.execute(
                select(
                    WalletCredit.parser, func.count(), func.max(WalletCredit.received_at)
                ).group_by(WalletCredit.parser)
            )
        ).all()
    }
    return [
        Format(
            parser.id,
            parser.provider,
            parser.country,
            parser.currency,
            parser.verified,
            *used.get(parser.id, (0, None)),
        )
        for parser in sorted(PARSERS.values(), key=lambda p: (p.country, p.provider))
    ]
