"""Alerts on phones (feature 115): the Web Push message matches RFC 8291's own example, and the
VAPID token is a valid ES256 signature with the claims push services check."""

from __future__ import annotations

import json

import pytest
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.utils import encode_dss_signature

from chatfront.push.webpush import (
    MAX_PAYLOAD,
    Vapid,
    b64url,
    encrypt,
    new_private_key,
    unb64url,
)

# RFC 8291, section 5 and appendix A.
PLAINTEXT = b"When I grow up, I want to be a watermelon"
AS_PRIVATE = "yfWPiYE-n46HLnH0KqZOF1fJJU3MYrct3AELtAQ-oRw"
UA_PUBLIC = (
    "BCVxsr7N_eNgVRqvHtD0zTZsEc6-VV-JvLexhqUzORcxaOzi6-AYWXvTBHm4bjyPjs7Vd8pZGH6SRpkNtoIAiw4"
)
AUTH = "BTBZMqHH6r4Tts7J_aSIgg"
SALT = "DGv6ra1nlYgDCS1FRnbzlw"
BODY = (
    "DGv6ra1nlYgDCS1FRnbzlwAAEABBBP4z9KsN6nGRTbVYI_c7VJSPQTBtkgcy27mlmlMoZIIg"
    "Dll6e3vCYLocInmYWAmS6TlzAC8wEqKK6PBru3jl7A_yl95bQpu6cVPTpK4Mqgkf1CXztLVBSt2Ks3oZwbuwXPXLWyou"
    "BWLVWGNWQexSgSxsj_Qulcy4a-fN"
)


def test_the_message_matches_the_rfc_example() -> None:
    sender = ec.derive_private_key(int.from_bytes(unb64url(AS_PRIVATE), "big"), ec.SECP256R1())
    body = encrypt(PLAINTEXT, UA_PUBLIC, AUTH, sender=sender, salt=unb64url(SALT))
    assert b64url(body) == BODY


def test_each_message_has_its_own_key_and_salt() -> None:
    first = encrypt(PLAINTEXT, UA_PUBLIC, AUTH)
    second = encrypt(PLAINTEXT, UA_PUBLIC, AUTH)
    assert first[:16] != second[:16]
    assert len(first) == len(second) == 21 + 65 + len(PLAINTEXT) + 1 + 16
    with pytest.raises(ValueError, match="too large"):
        encrypt(b"x" * (MAX_PAYLOAD + 1), UA_PUBLIC, AUTH)


def test_the_vapid_token_is_signed_for_the_push_service() -> None:
    vapid = Vapid.from_text(new_private_key(), "mailto:shop@example.com")
    header = vapid.header("https://fcm.googleapis.com/fcm/send/abc123", now=1_000)
    token, key = header.removeprefix("vapid t=").split(", k=")
    assert key == vapid.public_key
    assert len(unb64url(key)) == 65
    head, claims, signature = token.split(".")
    assert json.loads(unb64url(head)) == {"typ": "JWT", "alg": "ES256"}
    assert json.loads(unb64url(claims)) == {
        "aud": "https://fcm.googleapis.com",
        "exp": 1_000 + 12 * 3600,
        "sub": "mailto:shop@example.com",
    }
    raw = unb64url(signature)
    der = encode_dss_signature(int.from_bytes(raw[:32], "big"), int.from_bytes(raw[32:], "big"))
    public = ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), unb64url(key))
    public.verify(der, f"{head}.{claims}".encode(), ec.ECDSA(hashes.SHA256()))  # no error


def test_the_same_private_key_gives_the_same_public_key() -> None:
    private = new_private_key()
    assert Vapid.from_text(private, "").public_key == Vapid.from_text(private, "x").public_key
