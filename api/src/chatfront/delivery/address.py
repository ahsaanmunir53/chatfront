"""Addresses a rider can find (feature 33).

The order agent splits what the customer wrote into house, street, area and landmark; code
tidies the words riders know ("H#", "gali 4", "masjid ke paas") into one line and decides
whether it is enough. An address with no area, or with neither a number nor a landmark, needs a
location pin before the order can be placed.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from chatfront.core.countries import CountryPack
from chatfront.core.errors import AppError
from chatfront.core.phone import normalise_phone

_MAX_LINE = 300

# Short forms and Roman Urdu words, as a rider would write them on the parcel.
_WORDS: tuple[tuple[re.Pattern[str], str], ...] = tuple(
    (re.compile(pattern, re.IGNORECASE), replacement)
    for pattern, replacement in (
        (
            r"\b(?:h\s*#|h\.\s*no\.?|h\s+no\.?|house\s*(?:no\.?|number|#)"
            r"|makan\s*(?:no\.?|number|#)?|ghar\s*(?:no\.?|number|#))\s*(?=\w)",
            "House ",
        ),
        (
            r"\b(?:st\s*#|st\.|street\s*(?:no\.?|number|#)|gali\s*(?:no\.?|number|#)?)\s*(?=\w)",
            "Street ",
        ),
        (r"\b(?:blk\.?|block\s*(?:no\.?|#))\s*(?=\w)", "Block "),
        (r"\bph\.?\s*(?=\d)", "Phase "),
        (r"\bsec\.?\s*(?=\d)", "Sector "),
        (r"\bnr\.?\s+", "near "),
        (r"\bopp\.?\s+", "opposite "),
    )
)
# "Jamia masjid ke paas" is "near Jamia masjid".
_NEAR_AFTER = re.compile(
    r"^(?P<place>.+?)\s+(?:ke|kay|k)\s+(?:paas|pas|pass|qareeb|kareeb|nazdeek|nazdik)\b\.?$",
    re.IGNORECASE,
)
_LANDMARK_START = re.compile(
    r"^(?:near|opposite|behind|next to|in front of|beside|close to|facing)\b", re.IGNORECASE
)
_LANDMARK_ANYWHERE = re.compile(
    r"\b(?:near|opposite|behind|next to|in front of|beside|ke paas|ke pas|ke qareeb|"
    r"ke nazdeek|ke samne|ke peeche)\b|کے پاس|کے قریب|کے سامنے",
    re.IGNORECASE,
)
_SPACES = re.compile(r"\s+")
_COMMAS = re.compile(r"\s*,[\s,]*")
_MIN_WORDS = 4  # with no parts given, fewer words than this is not an address


def tidy(text: str) -> str:
    """One clean line: known short forms spelled out, spaces and commas evened."""
    line = _SPACES.sub(" ", text).strip(" ,.")
    for pattern, replacement in _WORDS:
        line = pattern.sub(replacement, line)
    return _COMMAS.sub(", ", _SPACES.sub(" ", line)).strip(" ,")


def landmark(text: str) -> str:
    """A landmark that reads as one: "near Jamia Masjid", "opposite the PSO pump"."""
    place = tidy(text)
    if not place:
        return ""
    if match := _NEAR_AFTER.match(place):
        return f"near {match['place']}"
    return place if _LANDMARK_START.match(place) else f"near {place}"


@dataclass(frozen=True, slots=True)
class Address:
    """What the customer wrote, and the parts the agent read in it."""

    line: str
    house: str = ""
    street: str = ""
    area: str = ""
    landmark: str = ""

    @property
    def has_parts(self) -> bool:
        return any((self.house, self.street, self.area, self.landmark))

    def clean(self) -> str:
        """House, street, area and landmark in that order; the tidied line when the agent
        gave no parts."""
        if not self.has_parts:
            return tidy(self.line)[:_MAX_LINE]
        parts = [tidy(self.house), tidy(self.street), tidy(self.area), landmark(self.landmark)]
        return ", ".join(part for part in parts if part)[:_MAX_LINE]

    def vague(self) -> bool:
        """Too little for a rider: no area, or neither a number nor a landmark."""
        if not self.has_parts:
            line = tidy(self.line)
            enough = len(line.split()) >= _MIN_WORDS
            return not (enough and (_has_number(line) or bool(_LANDMARK_ANYWHERE.search(line))))
        located = _has_number(f"{self.house} {self.street}") or bool(self.landmark.strip())
        return not (self.area.strip() and located)


def _has_number(text: str) -> bool:
    return any(char.isdigit() for char in text)


def local_phone(raw: str, pack: CountryPack) -> tuple[str, bool]:
    """The number in E.164 and True when it is a number of the shop's country; else the
    digits as typed and False."""
    typed = raw.strip()[:20]
    phone = pack.phone
    try:
        e164 = normalise_phone(
            typed,
            phone.calling_code,
            trunk_prefix=phone.trunk_prefix,
            national_lengths=phone.national_lengths,
        )
    except AppError:
        return typed, False
    national = e164.removeprefix(f"+{phone.calling_code}")
    valid = e164 != national and len(national) in phone.national_lengths
    return (e164, True) if valid else (typed, False)


def phone_for_people(e164: str, pack: CountryPack) -> str:
    """'+923001234567' as people of the country write it: '0300 1234567', grouped like the
    pack's example number. Other numbers stay as they are."""
    phone = pack.phone
    prefix = f"+{phone.calling_code}"
    if not e164.startswith(prefix):
        return e164
    local = phone.trunk_prefix + e164.removeprefix(prefix)
    groups = [len(part) for part in phone.example.split()]
    if sum(groups) != len(local):
        return local
    parts, at = [], 0
    for size in groups:
        parts.append(local[at : at + size])
        at += size
    return " ".join(parts)
