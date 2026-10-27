"""Finding products from what a customer says or shows (features 11 and 89).

A model describes a photo ("sea green printed lawn three piece") or reads a handwritten list
("chawal 5 kilo"); code decides which products those words mean. Every word is compared with
the product's name, its names in other languages, tags, category, attributes, options and
description, with the name counting most.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Iterable, Sequence
from dataclasses import dataclass

from chatfront.catalog.service import CatalogItem, find_products

# Words that say what someone wants rather than what it is.
_FILLER = frozenset(
    (
        "a",
        "an",
        "the",
        "and",
        "or",
        "of",
        "for",
        "with",
        "in",
        "on",
        "to",
        "is",
        "it",
        "this",
        "that",
        "these",
        "those",
        "i",
        "me",
        "my",
        "want",
        "need",
        "please",
        "show",
        "any",
        "some",
        "do",
        "you",
        "have",
        "has",
        "got",
        "buy",
        "order",
        "price",
        "how",
        "much",
        "kitna",
        "kitne",
        "kitni",
        "ka",
        "ki",
        "ke",
        "ko",
        "hai",
        "hain",
        "chahiye",
        "chahye",
        "chaiye",
        "wala",
        "wali",
        "wale",
        "yeh",
        "ye",
        "woh",
        "wo",
        "koi",
        "kuch",
        "mujhe",
        "muje",
        "dikhao",
        "bhejo",
        "de",
        "dein",
        "do",
        "aur",
        "ya",
        "bhi",
        "یہ",
        "وہ",
        "والا",
        "والی",
        "والے",
        "چاہیے",
        "چاہئے",
        "کا",
        "کی",
        "کے",
        "کو",
        "ہے",
        "ہیں",
        "اور",
        "یا",
        "بھی",
        "مجھے",
        "دکھائیں",
    )
)
_WORD = re.compile(r"[^\W_]+", re.UNICODE)
_NAME_WEIGHT = 0.6
_OTHER_WEIGHT = 0.4


def words(text: str) -> list[str]:
    folded = unicodedata.normalize("NFKC", text).casefold()
    return [w for w in _WORD.findall(folded) if w not in _FILLER]


_REPEATS = re.compile(r"(.)\1+")


def _squash(word: str) -> str:
    """Roman Urdu spells one sound many ways: daal, dal; cheeni, chini; atta, aata."""
    if not word.isascii():
        return word
    return _REPEATS.sub(r"\1", word.replace("ee", "i").replace("oo", "u"))


def _close(a: str, b: str) -> bool:
    """Same word, allowing a plural or a short spelling difference (kurta, kurtas)."""
    a, b = _squash(a), _squash(b)
    if a == b:
        return True
    shorter, longer = sorted((a, b), key=len)
    return len(shorter) >= 4 and longer.startswith(shorter) and len(longer) - len(shorter) <= 2


def _hits(query: Sequence[str], field: Iterable[str]) -> float:
    pool = set(field)
    if not query or not pool:
        return 0.0
    return sum(1 for word in query if any(_close(word, other) for other in pool)) / len(query)


def _texts(item: CatalogItem) -> tuple[list[str], list[str]]:
    """The words of a product's names (with the other names people use for it), and of
    everything else said about it."""
    product = item.product
    names = [product.name, product.code, *map(str, product.tags)]  # tags: names people use
    names += [str(text.get("name", "")) for text in product.translations.values()]
    other = [product.description, product.category or ""]
    other += [str(value) for value in product.attributes.values()]
    other += [str(text.get("description", "")) for text in product.translations.values()]
    for variant in item.variants:
        other += [str(value) for value in variant.options.values()]
    return names, other


@dataclass(frozen=True, slots=True)
class Found:
    item: CatalogItem
    score: float


def search(items: Sequence[CatalogItem], query: str, *, limit: int = 5) -> list[Found]:
    """Products that fit the words, best first. Out-of-stock products are kept: the agent says
    so rather than pretending the shop never had them."""
    asked = words(query)
    if not asked:
        return []
    scored = []
    for item in items:
        names, other = _texts(item)
        name_words = [w for text in names for w in words(text)]
        other_words = [w for text in other for w in words(text)]
        by_name = max(_hits(asked, name_words), _name_likeness(query, item))
        by_other = _hits(asked, name_words + other_words)
        score = _NAME_WEIGHT * by_name + _OTHER_WEIGHT * by_other
        if score >= 0.25:
            scored.append(Found(item, round(score, 3)))
    scored.sort(key=lambda found: found.score, reverse=True)
    return scored[:limit]


def _name_likeness(query: str, item: CatalogItem) -> float:
    matches = find_products([item], query, threshold=0.0)
    return matches[0].score if matches else 0.0


def best(found: Sequence[Found], *, margin: float = 0.15, floor: float = 0.45) -> Found | None:
    """The one product a line of a shopping list means, or None when it is unclear."""
    if not found or found[0].score < floor:
        return None
    if len(found) > 1 and found[0].score - found[1].score < margin:
        return None
    return found[0]
