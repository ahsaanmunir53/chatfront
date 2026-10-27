"""Prompt files under version control: ai/prompts/<name>.md, starting with 'version: N'."""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from string import Template

PROMPTS_DIR = Path(__file__).parent / "prompts"


@dataclass(frozen=True)
class Prompt:
    name: str
    version: int
    template: str

    @property
    def ref(self) -> str:
        """What the cost meter records, so a reply can be traced to the exact prompt."""
        return f"{self.name}@v{self.version}"

    def render(self, **values: str) -> str:
        return Template(self.template).substitute(values)


@lru_cache
def load_prompt(name: str, directory: Path = PROMPTS_DIR) -> Prompt:
    raw = (directory / f"{name}.md").read_text(encoding="utf-8")
    header, _, body = raw.partition("\n\n")
    key, _, value = header.partition(":")
    if key.strip() != "version" or not value.strip().isdigit():
        raise ValueError(f"prompt {name} must start with 'version: N'")
    return Prompt(name=name, version=int(value), template=body.strip() + "\n")
