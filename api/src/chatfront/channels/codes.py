"""Messages that carry a code for Chatfront rather than words for the shop: "LINK K7Q2M9"
(feature 79). Code answers them, and they say nothing about the customer's language."""

from __future__ import annotations

import re

CODE_LENGTH = 6
_LINK_TEXT = re.compile(rf"^\s*link\s+([0-9a-z]{{{CODE_LENGTH}}})\s*[.!]?\s*$", re.IGNORECASE)


def link_code_of(text: str | None) -> str | None:
    """The code in a "LINK K7Q2M9" message, in capitals, or None for any other message."""
    found = _LINK_TEXT.match(text or "")
    return found.group(1).upper() if found else None
