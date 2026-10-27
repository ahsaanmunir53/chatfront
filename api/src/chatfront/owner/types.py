"""Small shapes shared by the owner's assistant and the modules it drives."""

from __future__ import annotations

from typing import Any

# Something to tell the owner instead of a change: a string key and its values.
Problem = tuple[str, dict[str, Any]]
