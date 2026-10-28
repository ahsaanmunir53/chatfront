"""Fake "Meta or WhatsApp support" links, and requests for a one-time code (feature 40).

Code reads every link in a message. A host that names WhatsApp, Facebook, Instagram, Meta or
a wallet, or spells one with a letter changed or digits for letters, but is not that brand's
own domain is fake. Hosts written in punycode, bare IP addresses, link shorteners and
throwaway domains are risky. When a Google Safe Browsing key is set, the links are also
checked against Google's lists of phishing and malware sites. Code also looks for the words
of the usual scams: "your account will be blocked", "WhatsApp support", "send me the code".

A message with a fake link, one that asks for a code, or a support claim with a link that is
not the brand's own raises the alarm: the assistant does not take part, the sender gets a
short line saying the shop never asks for codes or sends login links, and the owner is told
what was found. The owner can also send any link to Chatfront and hear what it is.
"""

from __future__ import annotations

import ipaddress
import logging
import re
import unicodedata
from collections.abc import Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from urllib.parse import urlsplit

import httpx

from chatfront.core.errors import AppError
from chatfront.core.http import request
from chatfront.core.i18n import t
from chatfront.core.settings import Settings

log = logging.getLogger(__name__)

# Each brand scammers pose as, and the domains that really are it.
BRANDS: dict[str, tuple[str, ...]] = {
    "whatsapp": ("whatsapp.com", "whatsapp.net", "wa.me"),
    "facebook": ("facebook.com", "fb.com", "fb.me", "fbcdn.net", "messenger.com", "m.me"),
    "instagram": ("instagram.com", "ig.me", "cdninstagram.com"),
    "meta": ("meta.com", "meta.ai", "metastatus.com"),
    "jazzcash": ("jazzcash.com.pk",),
    "easypaisa": ("easypaisa.com.pk",),
}
SHORTENERS = frozenset(
    (
        *("bit.ly", "tinyurl.com", "t.ly", "cutt.ly", "is.gd", "shorturl.at", "rb.gy"),
        *("tiny.cc", "s.id", "goo.gl", "ow.ly", "t.co", "rebrand.ly", "v.gd"),
    )
)
RISKY_ENDINGS = (".xyz", ".top", ".tk", ".ml", ".ga", ".cf", ".gq", ".click", ".support", ".icu")
_LOOKS_LIKE = str.maketrans({"0": "o", "1": "l", "3": "e", "4": "a", "5": "s", "7": "t", "-": ""})
_TLDS = (
    "com|net|org|info|biz|pk|in|bd|ae|sa|eg|co|io|me|app|online|site|link|live|shop|store"
    "|xyz|top|tk|ml|ga|cf|gq|click|support|icu|ly|gd|cc|id|at|help|page|services|center|website|vip"
)
# A link with its scheme or www, or a bare domain under a common ending, with any path.
_LINK = re.compile(
    rf"(?:https?://|www\.)[^\s<>\"']+"
    rf"|\b[a-z0-9][a-z0-9-]*(?:\.[a-z0-9-]+)*\.(?:{_TLDS})\b(?:/[^\s<>\"']*)?",
    re.IGNORECASE,
)
# Short brand names that are also ordinary words ("metal", "beta"): only a whole label, or
# the name run into a support word, counts as posing.
STRICT = {"meta": ("support", "help", "verify", "business", "login", "security", "team")}
# The usual words of the scams, in the languages shops chat in.
SUPPORT_WORDS = (
    *("whatsapp support", "meta support", "facebook support", "instagram support"),
    *("support team", "help center team", "account will be blocked", "account will be"),
    *("suspended", "disabled", "verify your account", "copyright violation", "blue tick"),
    *("account band", "block ho jayega", "band ho jayega", "اکاؤنٹ بند", "بلاک ہو جائے گا"),
    *("अकाउंट बंद", "ব্লক হয়ে যাবে", "سيتم حظر", "تعطيل حسابك"),
)
# Asking for a one-time code, or telling of someone who did; a bare mention ("OTP nahi
# aaya") is not enough. Matched at the start of a word, so "code bhej" covers "code bhejo".
CODE_WORDS = (
    *("send me the code", "send the code", "send me the otp", "send the otp", "send otp"),
    *("share the code", "share the otp", "share otp", "tell me the code", "verification code"),
    *("one time password", "one-time password", "6 digit code", "6-digit code"),
    *("six digit code", "asked for the code", "asked for a code", "asking for the code"),
    *("asked for otp", "asking for otp", "code bhej", "code bata", "code send kar"),
    *("code share", "jo code aaya", "code mang", "code maang", "otp bhej", "otp bata"),
    *("otp send", "otp share", "otp mang", "otp maang", "pin code bhej"),
    *("کوڈ بھیج", "کوڈ بتا", "کوڈ مانگ", "او ٹی پی بھیج", "او ٹی پی بتا"),
    *("कोड भेज", "कोड बता", "ओटीपी भेज", "ओटीपी बता"),
    *("কোড পাঠান", "ওটিপি পাঠান", "ওটিপি দিন"),
    *("رمز التحقق", "ارسل الكود", "أرسل الرمز", "ارسل الرمز"),
)
SAFE_BROWSING_TYPES = (
    "MALWARE",
    "SOCIAL_ENGINEERING",
    "UNWANTED_SOFTWARE",
    "POTENTIALLY_HARMFUL_APPLICATION",
)
MAX_LINKS = 10


class Verdict(StrEnum):
    OFFICIAL = "official"  # the brand's own domain
    FAKE = "fake"  # poses as a brand, or Google lists it
    RISKY = "risky"  # hidden or throwaway: punycode, an IP address, a shortener
    UNKNOWN = "unknown"  # nothing known against it


@dataclass(frozen=True, slots=True)
class Link:
    url: str
    host: str
    verdict: Verdict
    brand: str | None = None  # the brand it is, or poses as
    why: str | None = None  # lookalike, listed, punycode, ip, shortener, throwaway


@dataclass(frozen=True, slots=True)
class Scan:
    links: list[Link] = field(default_factory=list)
    asks_for_code: bool = False
    claims_support: bool = False

    @property
    def alarm(self) -> str | None:
        """Why the message is a scam, or None: a fake link, a code request, or a support
        claim with a link that is not the brand's own."""
        if any(link.verdict == Verdict.FAKE for link in self.links):
            return "fake_link"
        if self.asks_for_code:
            return "asks_for_code"
        if self.claims_support and any(link.verdict != Verdict.OFFICIAL for link in self.links):
            return "fake_support"
        return None


def _plain(text: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", text).casefold().split())


def full_links(text: str | None) -> list[str]:
    """Links written with their scheme or www, as a pasted or forwarded link is."""
    return [link for link in links_in(text) if link.lower().startswith(("http", "www."))]


def link_line(link: Link, lang: str) -> str:
    """One link's verdict, as the owner reads it."""
    brand = (link.brand or "").capitalize()
    if link.verdict == Verdict.OFFICIAL:
        return t("shield.link_official", lang, host=link.host, brand=brand)
    if link.verdict == Verdict.FAKE:
        key = "shield.link_listed" if link.why == "listed" else "shield.link_fake"
        return t(key, lang, host=link.host, brand=brand or "—")
    if link.verdict == Verdict.RISKY:
        return t(f"shield.link_{link.why}", lang, host=link.host)
    return t("shield.link_unknown", lang, host=link.host)


def links_in(text: str | None) -> list[str]:
    return list(dict.fromkeys(m.group(0).rstrip(".,;:!?)") for m in _LINK.finditer(text or "")))[
        :MAX_LINKS
    ]


def host_of(url: str) -> str:
    parts = urlsplit(url if "://" in url else f"http://{url}")
    return (parts.hostname or "").rstrip(".").lower()


def _is_ip(host: str) -> bool:
    try:
        ipaddress.ip_address(host)
    except ValueError:
        return False
    return True


def _one_off(word: str, brand: str) -> bool:
    """The brand with one letter changed, added or missing."""
    if abs(len(word) - len(brand)) > 1 or word == brand:
        return False
    if len(word) == len(brand):
        return sum(a != b for a, b in zip(word, brand, strict=True)) == 1
    short, long = sorted((word, brand), key=len)
    return any(long[:i] + long[i + 1 :] == short for i in range(len(long)))


def official(host: str) -> str | None:
    """The brand whose own domain this is."""
    for brand, domains in BRANDS.items():
        if any(host == d or host.endswith("." + d) for d in domains):
            return brand
    return None


def _poses(brand: str, flat: str, labels: list[str]) -> bool:
    if brand in STRICT:
        joined = tuple(brand + word for word in STRICT[brand])
        return any(label == brand or label.startswith(joined) for label in labels)
    return brand in flat or any(_one_off(label, brand) for label in labels)


def posing_as(host: str) -> str | None:
    """The brand a host names or imitates without being its domain."""
    flat = host.translate(_LOOKS_LIKE)
    labels = [label.translate(_LOOKS_LIKE) for label in host.split(".")]
    return next((brand for brand in BRANDS if _poses(brand, flat, labels)), None)


def check(url: str) -> Link:
    """Code's verdict on one link."""
    host = host_of(url)
    if not host:
        return Link(url, host, Verdict.UNKNOWN)
    if brand := official(host):
        return Link(url, host, Verdict.OFFICIAL, brand)
    if host.startswith("xn--") or ".xn--" in host:
        posing = posing_as(host)
        return Link(url, host, Verdict.FAKE if posing else Verdict.RISKY, posing, "punycode")
    if brand := posing_as(host):
        return Link(url, host, Verdict.FAKE, brand, "lookalike")
    if _is_ip(host):
        return Link(url, host, Verdict.RISKY, None, "ip")
    if host in SHORTENERS:
        return Link(url, host, Verdict.RISKY, None, "shortener")
    if host.endswith(RISKY_ENDINGS):
        return Link(url, host, Verdict.RISKY, None, "throwaway")
    return Link(url, host, Verdict.UNKNOWN)


def says(text: str, words: Sequence[str]) -> bool:
    plain = _plain(text)
    return any(re.search(rf"(?<!\w){re.escape(word)}", plain) for word in words)


async def listed(http: httpx.AsyncClient, settings: Settings, urls: Sequence[str]) -> set[str]:
    """The links Google Safe Browsing lists; nothing without a key or when it cannot answer."""
    key = settings.safe_browsing_api_key
    if key is None or not urls:
        return set()
    body = {
        "client": {"clientId": "chatfront", "clientVersion": "1.0"},
        "threatInfo": {
            "threatTypes": list(SAFE_BROWSING_TYPES),
            "platformTypes": ["ANY_PLATFORM"],
            "threatEntryTypes": ["URL"],
            "threatEntries": [{"url": url} for url in urls],
        },
    }
    try:
        response = await request(
            http,
            "POST",
            settings.safe_browsing_url,
            service="safe_browsing",
            json=body,
            params={"key": key.get_secret_value()},
            retries=1,
        )
    except AppError as exc:
        log.warning("Safe Browsing did not answer: %s", exc.code)
        return set()
    matches = response.json().get("matches") or []
    return {str((m.get("threat") or {}).get("url")) for m in matches if isinstance(m, dict)}


async def scan(http: httpx.AsyncClient, settings: Settings, text: str | None) -> Scan:
    """Every link in a message checked by code and, with a key, by Safe Browsing; and the
    scam words."""
    if not text:
        return Scan()
    found = [check(url) for url in links_in(text)]
    unofficial = [link.url for link in found if link.verdict != Verdict.OFFICIAL]
    on_list = await listed(http, settings, unofficial)
    found = [
        Link(link.url, link.host, Verdict.FAKE, link.brand, "listed")
        if link.url in on_list
        else link
        for link in found
    ]
    return Scan(found, says(text, CODE_WORDS), says(text, SUPPORT_WORDS))
