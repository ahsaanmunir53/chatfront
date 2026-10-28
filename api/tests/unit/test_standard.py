"""The verified commerce standard (feature 72): the published test vectors sign and check as
written, and the reference checker says valid, invalid or how to use it."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

from chatfront import verify
from chatfront.payments import receipts

VECTORS = Path(__file__).parents[3] / "docs" / "verified-commerce" / "vectors.json"


def vectors() -> dict[str, Any]:
    data: dict[str, Any] = json.loads(VECTORS.read_text(encoding="utf-8"))
    return data


def document(entry: dict[str, Any]) -> dict[str, Any]:
    found: dict[str, Any] = entry.get("payload") or entry["statement"]
    return found


class TestVectors:
    def test_the_test_key_signs_every_document_as_published(self) -> None:
        data = vectors()
        key = Ed25519PrivateKey.from_private_bytes(bytes.fromhex(data["private_key_seed_hex"]))
        public = receipts.b64(key.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw))
        assert public == data["public_key"]
        assert {e["kind"] for e in data["documents"]} == {"receipt", "credit_passport"}
        for entry in data["documents"]:
            assert receipts.canonical(document(entry)) == entry["canonical"]
            assert receipts.sign(key, document(entry)) == entry["signature"]  # Ed25519 is fixed
            assert receipts.check(document(entry), entry["signature"], public).valid

    def test_any_change_breaks_the_signature(self) -> None:
        data = vectors()
        for entry in data["documents"]:
            changed = {**document(entry), "shop": "Another shop"}
            assert not receipts.check(changed, entry["signature"], data["public_key"]).valid


class TestReferenceChecker:
    def saved(self, tmp_path: Path, entry: dict[str, Any], public_key: str) -> Path:
        path = tmp_path / "document.json"
        body = {k: v for k, v in entry.items() if k in ("payload", "statement", "signature")}
        path.write_text(json.dumps({**body, "public_key": public_key}), encoding="utf-8")
        return path

    def test_valid_invalid_and_usage(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        data = vectors()
        receipt, passport = data["documents"]
        assert verify.main([str(self.saved(tmp_path, receipt, data["public_key"]))]) == 0
        assert verify.main([str(self.saved(tmp_path, passport, data["public_key"]))]) == 0
        other = receipts.b64(bytes(32))
        assert verify.main([str(self.saved(tmp_path, receipt, other))]) == 1
        assert capsys.readouterr().out.split() == ["valid", "valid", "invalid"]
        assert verify.main([]) == 2
        assert "usage" in capsys.readouterr().err

    def test_a_file_without_a_document(self) -> None:
        with pytest.raises(ValueError, match="No payload"):
            verify.document_of({"signature": "x"})
