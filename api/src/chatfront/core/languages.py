"""The language registry, i18n/languages.json, shared with the web app.

A language is identified by its BCP 47 tag ("ur", "ur-Latn", "pa-Arab", "afb"). Code-mixed and
romanised ways of writing (Roman Urdu, Hinglish, Banglish, Arabizi) are languages of their own,
because a customer who writes one expects the same back.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from enum import StrEnum
from functools import lru_cache
from pathlib import Path
from typing import Any, Literal

from chatfront.core.settings import get_settings


class Status(StrEnum):
    SUPPORTED = "supported"  # tier 1, and a live evaluation reached the pass mark
    BETA = "beta"  # tier 1, built and tested, not yet proven by a live evaluation
    STRETCH = "stretch"  # tier 2: known, detected by script, not yet built for


@dataclass(frozen=True, slots=True)
class Language:
    tag: str
    name: str
    native: str
    script: str
    dir: Literal["ltr", "rtl"]
    tier: int
    ui: bool
    cldr: str
    digits: str
    font: str
    prompt: str
    fallback: tuple[str, ...]

    @property
    def is_rtl(self) -> bool:
        return self.dir == "rtl"


@dataclass(frozen=True, slots=True)
class Registry:
    languages: dict[str, Language]
    aliases: dict[str, str]
    source: str
    pass_mark: float
    live_pass_rates: dict[str, float]

    def resolve(self, tag: str | None) -> Language | None:
        """The language for a tag, ignoring case, following aliases, then dropping subtags."""
        if not tag:
            return None
        by_lower = {key.lower(): key for key in self.languages}
        aliases = {key.lower(): value for key, value in self.aliases.items()}
        parts = tag.replace("_", "-").split("-")
        while parts:
            candidate = "-".join(parts).lower()
            if candidate in by_lower:
                return self.languages[by_lower[candidate]]
            if candidate in aliases:
                return self.languages[aliases[candidate]]
            parts.pop()
        return None

    def get(self, tag: str) -> Language:
        language = self.resolve(tag)
        if language is None:
            raise KeyError(f"unknown language: {tag}")
        return language

    def chain(self, tag: str | None) -> tuple[Language, ...]:
        """The language, its fallbacks, then the source language, without repeats."""
        start = self.resolve(tag) or self.languages[self.source]
        tags = [start.tag, *start.fallback, self.source]
        return tuple(self.languages[t] for t in dict.fromkeys(tags))

    def ui_language(self, tag: str | None) -> Language:
        """The first language in the chain that has its own interface catalog."""
        return next(language for language in self.chain(tag) if language.ui)

    def status(self, tag: str) -> Status:
        language = self.get(tag)
        if language.tier > 1:
            return Status.STRETCH
        passed = self.live_pass_rates.get(language.tag, 0.0) >= self.pass_mark
        return Status.SUPPORTED if passed else Status.BETA

    def by_tier(self, tier: int) -> list[Language]:
        return [language for language in self.languages.values() if language.tier == tier]


# Tags whose readers read each other: one language written two close ways, or a dialect and
# the standard language its readers also read.
_MUTUAL = (frozenset({"ar", "afb", "arz"}), frozenset({"ur-Latn", "hi-Latn"}))


def mutually_readable(a: str, b: str) -> bool:
    return a == b or any(a in group and b in group for group in _MUTUAL)


def _language(raw: dict[str, Any]) -> Language:
    return Language(
        tag=raw["tag"],
        name=raw["name"],
        native=raw["native"],
        script=raw["script"],
        dir=raw["dir"],
        tier=raw["tier"],
        ui=raw["ui"],
        cldr=raw["cldr"],
        digits=raw["digits"],
        font=raw["font"],
        prompt=raw["prompt"],
        fallback=tuple(raw["fallback"]),
    )


def live_pass_rates(results: dict[str, Any]) -> dict[str, float]:
    """Pass rate per language from the latest live evaluation; mock runs never count."""
    return {
        tag: float(run["live"]["pass_rate"])
        for tag, run in results.get("runs", {}).items()
        if run.get("live")
    }


@lru_cache
def load_registry(directory: Path | None = None) -> Registry:
    base = directory or get_settings().i18n_dir
    raw = json.loads((base / "languages.json").read_text(encoding="utf-8"))
    results = json.loads((base / "eval-results.json").read_text(encoding="utf-8"))
    languages = {item["tag"]: _language(item) for item in raw["languages"]}
    return Registry(
        languages=languages,
        aliases=raw["aliases"],
        source=raw["source"],
        pass_mark=float(raw["pass_mark"]),
        live_pass_rates=live_pass_rates(results),
    )
