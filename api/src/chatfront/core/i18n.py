"""One string catalog for the API and the web app: i18n/{en,ur,ur-Latn}.json at the repo root."""

from __future__ import annotations

import json
import re
from enum import StrEnum
from functools import lru_cache
from pathlib import Path

from chatfront.core.settings import get_settings


class Lang(StrEnum):
    EN = "en"
    UR = "ur"
    ROMAN_UR = "ur-Latn"

    @property
    def is_rtl(self) -> bool:
        return self is Lang.UR


_ARABIC_SCRIPT = re.compile("[؀-ۿݐ-ݿﭐ-﷿ﹰ-﻿]")
_ROMAN_URDU_WORDS = frozenset(
    [
        "hai",
        "hain",
        "nahi",
        "nahin",
        "kya",
        "kia",
        "kitne",
        "kitna",
        "kitni",
        "chahiye",
        "chahye",
        "bhai",
        "bhi",
        "aap",
        "ap",
        "mujhe",
        "mera",
        "meri",
        "yeh",
        "ye",
        "woh",
        "wo",
        "kar",
        "karo",
        "karein",
        "kab",
        "kaise",
        "kahan",
        "acha",
        "achha",
        "theek",
        "thik",
        "bhej",
        "bhejo",
        "bhejein",
        "paise",
        "rupay",
        "rupees",
        "wala",
        "wali",
        "waly",
        "hoga",
        "hogi",
        "ho",
        "sakta",
        "dena",
    ]
)


@lru_cache
def load_catalog(directory: Path | None = None) -> dict[Lang, dict[str, str]]:
    base = directory or get_settings().i18n_dir
    catalog: dict[Lang, dict[str, str]] = {}
    for lang in Lang:
        path = base / f"{lang.value}.json"
        catalog[lang] = json.loads(path.read_text(encoding="utf-8"))
    return catalog


def t(key: str, lang: Lang | str = Lang.EN, **values: object) -> str:
    """Translate a key; English is the fallback when a language lacks it."""
    catalog = load_catalog()
    lang = Lang(lang)
    template = catalog[lang].get(key) or catalog[Lang.EN].get(key)
    if template is None:
        raise KeyError(f"missing i18n key: {key}")
    return template.format(**values) if values else template


def detect_lang(text: str) -> Lang:
    """Urdu script, Roman Urdu or English, from the text of one message."""
    if _ARABIC_SCRIPT.search(text):
        return Lang.UR
    words = set(re.findall(r"[a-z]+", text.lower()))
    if len(words & _ROMAN_URDU_WORDS) >= 1:
        return Lang.ROMAN_UR
    return Lang.EN
