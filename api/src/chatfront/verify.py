"""Check a signed Chatfront document by hand (feature 72), the reference for the verified
commerce standard: `python -m chatfront.verify document.json`.

The file is what a receipt or passport page's API returns: the document under "payload" (a
receipt) or "statement" (a passport), with "signature" and "public_key". Nothing else is
needed: the signature is checked over the canonical JSON with the public key alone.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

from chatfront.payments.receipts import check


def document_of(data: dict[str, Any]) -> dict[str, Any]:
    found = data.get("payload") or data.get("statement")
    if not isinstance(found, dict):
        raise ValueError("No payload or statement in the file")
    return found


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    if len(args) != 1:
        print("usage: python -m chatfront.verify document.json", file=sys.stderr)
        return 2
    data = json.loads(Path(args[0]).read_text(encoding="utf-8"))
    result = check(document_of(data), str(data.get("signature", "")), data.get("public_key"))
    print("valid" if result.valid else "invalid")
    return 0 if result.valid else 1


if __name__ == "__main__":
    raise SystemExit(main())
