"""One string catalog for the API and the web app: i18n/<tag>.json at the repo root.

Every language with "ui": true in languages.json has a catalog with the same keys as the source
(English). Messages are ICU MessageFormat. A language without its own catalog, or a key a
catalog lacks, falls back along the language's chain and finally to English.
"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

from chatfront.core.icu import format_message
from chatfront.core.languages import load_registry
from chatfront.core.settings import get_settings


@lru_cache
def load_catalogs(directory: Path | None = None) -> dict[str, dict[str, str]]:
    base = directory or get_settings().i18n_dir
    registry = load_registry(directory)
    return {
        language.tag: json.loads((base / f"{language.tag}.json").read_text(encoding="utf-8"))
        for language in registry.languages.values()
        if language.ui
    }


def t(key: str, lang: str | None = None, /, **values: object) -> str:
    """The message for key in the reader's language, formatted with that language's rules."""
    catalogs = load_catalogs()
    for language in load_registry().chain(lang):
        template = catalogs.get(language.tag, {}).get(key)
        if template is not None:
            return format_message(template, language, values)
    raise KeyError(f"missing i18n key: {key}")
