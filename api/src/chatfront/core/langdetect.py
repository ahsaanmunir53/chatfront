"""Which language and script a message is in, and whether it asks for another language.

Deterministic and fast: the script decides first (Urdu script, Devanagari, Bengali, Latin...),
then letters only some languages write (Pashto ټ, Sindhi ڪ), then common words. Romanised and
code-mixed writing (Roman Urdu, Hinglish, Banglish, Arabizi, Taglish, Pidgin) is detected as a
language of its own. Hints live in i18n/detect.json.
"""

from __future__ import annotations

import json
import re
import unicodedata
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

from chatfront.core.languages import Registry, load_registry
from chatfront.core.settings import get_settings

_SCRIPT_RANGES = (
    (0x0600, 0x06FF, "Arab"),
    (0x0750, 0x077F, "Arab"),
    (0x08A0, 0x08FF, "Arab"),
    (0xFB50, 0xFDFF, "Arab"),
    (0xFE70, 0xFEFF, "Arab"),
    (0x0900, 0x097F, "Deva"),
    (0x0980, 0x09FF, "Beng"),
    (0x0A00, 0x0A7F, "Guru"),
    (0x0A80, 0x0AFF, "Gujr"),
    (0x0B80, 0x0BFF, "Taml"),
    (0x0C00, 0x0C7F, "Telu"),
    (0x0C80, 0x0CFF, "Knda"),
    (0x0D00, 0x0D7F, "Mlym"),
    (0x0D80, 0x0DFF, "Sinh"),
    (0x0E00, 0x0E7F, "Thai"),
    (0x1200, 0x137F, "Ethi"),
)
# Scripts written by one language Chatfront knows.
_SCRIPT_LANGUAGE = {
    "Beng": "bn",
    "Guru": "pa-Guru",
    "Gujr": "gu",
    "Taml": "ta",
    "Telu": "te",
    "Knda": "kn",
    "Mlym": "ml",
    "Sinh": "si",
    "Thai": "th",
    "Ethi": "am",
}
_NOISE = re.compile(r"\[[^\]]*\]|https?://\S+|www\.\S+|\S+@\S+|@\w+|#\w+")
# Arabizi writes ع as 3, ح as 7, ء as 2, خ as 5, ق as 9, inside words.
_ARABIZI = re.compile(r"^(?:[a-z]+[23579][a-z]*|[23579][a-z]{2,})$")
_UNITS = re.compile(r"^\d+(?:pc|pcs|kg|g|gb|tb|mb|ml|l|k|din|day|days|x|m|cm|mm|ft|hr|hrs|min)$")
_ARABIC_FAMILY = ("ar", "arz", "afb")
_LATIN_FAMILIES = (
    "hindustani-Latn",
    "bn-Latn",
    "ar-Latn",
    "en",
    "pcm",
    "fil",
    "sw",
    "ha",
    "yo",
    "ig",
    "so",
    "zu",
    "tr",
    "fr",
    "id",
    "ms",
    "vi",
    "es-419",
    "pt-BR",
)


@dataclass(frozen=True, slots=True)
class Detection:
    tag: str
    script: str
    confident: bool


def script_of(char: str) -> str | None:
    code = ord(char)
    if not char.isalpha():
        return None
    if code < 0x0250 or 0x1E00 <= code <= 0x1EFF:
        return "Latn"
    for start, end, script in _SCRIPT_RANGES:
        if start <= code <= end:
            return script
    return None


def _clean(text: str) -> str:
    return _NOISE.sub(" ", text)


def _is_word_char(char: str) -> bool:
    # Letters, digits and the vowel signs of Indic and Arabic scripts (categories L, N, M).
    return unicodedata.category(char)[0] in "LNM"


def _words(text: str) -> list[str]:
    """Lowercase words; an apostrophe inside a word ("c'est", "o'clock") stays in it."""
    words: list[str] = []
    current: list[str] = []
    for index, char in enumerate(text.replace("’", "'")):
        inside = char == "'" and current and _is_word_char(text[index + 1 : index + 2] or " ")
        if _is_word_char(char) or inside:
            current.append(char)
        elif current:
            words.append("".join(current).lower())
            current = []
    if current:
        words.append("".join(current).lower())
    return words


def _first(candidates: Sequence[str], allowed: Sequence[str]) -> str | None:
    return next((tag for tag in candidates if tag in allowed), None)


class Detector:
    def __init__(self, hints: dict[str, Any], registry: Registry) -> None:
        self.registry = registry
        self.letters = {tag: frozenset(chars) for tag, chars in hints["letters"].items()}
        self.words = {tag: frozenset(words) for tag, words in hints["words"].items()}
        self.weights: dict[str, float] = hints["weights"]
        self.diacritics = {tag: frozenset(chars) for tag, chars in hints["diacritics"].items()}
        self.names = {name: tag for tag, names in hints["names"].items() for name in names}
        self.romanised: dict[str, str] = hints["romanised"]
        self.script_words = frozenset(hints["script_words"])
        self.switch_verbs = frozenset(hints["switch_verbs"])
        self.switch_marks = frozenset(hints["switch_marks"])

    # -- detection ---------------------------------------------------------------------------

    def detect(
        self, text: str, *, previous: str | None = None, likely: Sequence[str] = ()
    ) -> Detection | None:
        """The language of one message, or None when it has no letters (an emoji, a number).

        previous is the conversation's language so far and likely the shop's country languages
        in order; both only break ties. confident is False when the evidence is thin."""
        clean = _clean(text)
        scripts = Counter(s for s in map(script_of, clean) if s)
        if not scripts:
            return None
        script = scripts.most_common(1)[0][0]
        words = _words(clean)
        if script == "Arab":
            tag, confident = self._arabic_script(clean, words, previous, likely)
        elif script == "Deva":
            tag, confident = self._best(("hi", "mr", "ne"), words, default="hi")
        elif script == "Latn":
            tag, confident = self._latin(clean, words, previous, likely)
        elif script in _SCRIPT_LANGUAGE:
            tag, confident = _SCRIPT_LANGUAGE[script], True
        else:  # pragma: no cover - every script in _SCRIPT_RANGES is handled above
            return None
        return Detection(tag, script, confident)

    def _count(self, tag: str, words: Sequence[str]) -> int:
        vocabulary = self.words.get(tag, frozenset())
        return sum(word in vocabulary for word in words)

    def _best(self, tags: Sequence[str], words: Sequence[str], default: str) -> tuple[str, bool]:
        scores = {tag: self._count(tag, words) for tag in tags}
        best = max(scores, key=lambda tag: scores[tag])
        if scores[best] == 0:
            return default, False
        return best, scores[best] >= 1

    def _tie(self, tags: Sequence[str], previous: str | None, likely: Sequence[str]) -> str:
        if previous is not None and previous in tags:
            return previous
        return _first(likely, tags) or tags[0]

    def _arabic_script(
        self, text: str, words: Sequence[str], previous: str | None, likely: Sequence[str]
    ) -> tuple[str, bool]:
        letters = {tag: sum(ch in chars for ch in text) for tag, chars in self.letters.items()}
        if letters["skr"]:
            return "skr", True
        if letters["ps"] and letters["ps"] >= letters["sd"]:
            return "ps", True
        if letters["sd"]:
            return "sd", True
        word = {tag: self._count(tag, words) for tag in ("ur", "pa-Arab", *_ARABIC_FAMILY)}
        arabic = letters["ar"] + sum(word[tag] for tag in _ARABIC_FAMILY)
        urdu = letters["ur"] + word["ur"] + word["pa-Arab"]
        if arabic > urdu:
            if word["arz"] or word["afb"]:
                return ("arz" if word["arz"] >= word["afb"] else "afb"), True
            return "ar", arabic >= 2
        if urdu > arabic:
            # Punjabi words never appear in Urdu, while Punjabi borrows Urdu ones: a tie is Punjabi.
            if word["pa-Arab"] and word["pa-Arab"] >= word["ur"]:
                return "pa-Arab", word["pa-Arab"] >= 2
            return "ur", word["ur"] >= 1
        family = ("ur", "pa-Arab", "ps", "sd", "skr", *_ARABIC_FAMILY)
        return self._tie(family, previous, likely), False

    def _latin(
        self, text: str, words: Sequence[str], previous: str | None, likely: Sequence[str]
    ) -> tuple[str, bool]:
        scores = {
            tag: self._count(tag, words) * self.weights.get(tag, 1.0) for tag in _LATIN_FAMILIES
        }
        scores["ar-Latn"] += self.weights["ar-Latn"] * sum(
            bool(_ARABIZI.match(w)) and not _UNITS.match(w) for w in words
        )
        lowered = text.lower()
        for tag, chars in self.diacritics.items():
            scores[tag] += sum(ch in chars for ch in lowered)
        ranked = sorted(scores, key=lambda tag: scores[tag], reverse=True)
        best, second = ranked[0], ranked[1]
        latin = tuple(
            tag for tag, language in self.registry.languages.items() if language.script == "Latn"
        )
        if scores[best] == 0:
            return self._tie(latin, previous, (*likely, "en")), False
        confident = scores[best] >= 2 and scores[best] >= 1.5 * scores[second]
        if best == "hindustani-Latn":
            return self._hindustani(words, previous, likely), confident
        if best == "fil" and scores["en"] >= 2:
            return "fil-x-taglish", confident
        return best, confident

    def _hindustani(self, words: Sequence[str], previous: str | None, likely: Sequence[str]) -> str:
        """Roman Urdu or Hinglish: the same words, told apart by a few telling ones."""
        urdu, hindi = self._count("ur-Latn", words), self._count("hi-Latn", words)
        if urdu != hindi:
            return "ur-Latn" if urdu > hindi else "hi-Latn"
        return self._tie(("ur-Latn", "hi-Latn"), previous, likely)

    # -- switching ---------------------------------------------------------------------------

    def requested_language(self, text: str) -> str | None:
        """The language a message asks for: "English mein baat karo", "اردو میں لکھیں",
        "reply in Bangla", or just "Urdu please". None when it asks for nothing."""
        words = _words(_clean(text))
        if not words or len(words) > 10:
            return None
        found = self._named_language(words)
        if found is None:
            return None
        tag, after = found
        asked = (
            len(words) <= 3
            or bool(self.switch_verbs & set(words))
            or bool(self.switch_marks & set(words[after : after + 2]))
        )
        if not asked:
            return None
        if tag in self.romanised and not self.script_words & set(words):
            written_in = self.detect(text)
            if written_in and written_in.tag in self.romanised.values():
                return self.romanised[tag]  # asked in Roman Urdu, Hinglish or Banglish
        return tag

    def _named_language(self, words: Sequence[str]) -> tuple[str, int] | None:
        """The first language named, and the index of the word after its name."""
        for at in range(len(words)):
            for size in (2, 1):
                name = " ".join(words[at : at + size])
                if name in self.names:
                    return self.names[name], at + size
        return None


@lru_cache
def load_detector(directory: Path | None = None) -> Detector:
    base = directory or get_settings().i18n_dir
    hints = json.loads((base / "detect.json").read_text(encoding="utf-8"))
    return Detector(hints, load_registry(directory))
