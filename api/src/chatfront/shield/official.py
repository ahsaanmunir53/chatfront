"""Is this number or account really the shop's? (feature 41)

A fake shop copies a real one's name and photos and takes orders on another number. The trust
page lists the shop's official channels; this check lets anyone paste the number or account
that wrote to them and hear whether it is one of them. Only channels connected through
Meta's official APIs count, so a number the shop does not hold cannot pass. Code compares;
nothing is guessed.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass

from chatfront.channels.models import Channel, ChannelAccount
from chatfront.core.countries import CountryPack
from chatfront.delivery.address import local_phone

MAX_LENGTH = 120
_LINK_PREFIX = re.compile(
    r"^(https?://)?(www\.)?"
    r"(instagram\.com/|ig\.me/m/|facebook\.com/|fb\.com/|m\.me/|wa\.me/|api\.whatsapp\.com/send\?phone=)",
    re.IGNORECASE,
)
_DIGITS = re.compile(r"\D")


@dataclass(frozen=True, slots=True)
class Found:
    channel: str
    display: str | None


def _handle(value: str) -> str:
    """'@Gulmohar.Lawn', 'instagram.com/gulmohar.lawn/' and 'gulmohar.lawn' are one handle."""
    plain = _LINK_PREFIX.sub("", value.strip())
    return plain.split("?")[0].strip("/ ").lstrip("@").lower()


def _number(value: str, pack: CountryPack) -> str | None:
    """The digits of a phone number in international form, if the value reads as one."""
    plain = re.split(r"[?&]", _LINK_PREFIX.sub("", value.strip()))[0]
    if sum(char.isdigit() for char in plain) < 8 or re.search(r"[A-Za-z]", plain):
        return None
    e164, valid = local_phone(plain, pack)
    return _DIGITS.sub("", e164 if valid else plain)


def check(accounts: Sequence[ChannelAccount], value: str, pack: CountryPack) -> Found | None:
    """The shop's channel this value names, or None when it is not one of the shop's."""
    typed = value.strip()[:MAX_LENGTH]
    if not typed:
        return None
    number = _number(typed, pack)
    handle = _handle(typed)
    for account in accounts:
        known = (account.public_handle or "").strip()
        if not known:
            continue
        if account.channel == Channel.WHATSAPP:
            if number and _DIGITS.sub("", known) == number:
                return Found(account.channel, account.display)
        elif handle and _handle(known) == handle:
            return Found(account.channel, account.display)
    return None
