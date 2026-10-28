"""Web Push (feature 115) without a third-party service: the alert is encrypted for the phone
(RFC 8291, aes128gcm) and the request signed with the shop's VAPID key (RFC 8292), then posted
to the push service the browser chose (Google's for Chrome, Mozilla's for Firefox, Apple's for
Safari). Those services are free and need no account; the VAPID key is generated once with
`python -m chatfront.push`.
"""

from __future__ import annotations

import base64
import json
import os
import struct
import time
from dataclasses import dataclass
from urllib.parse import urlsplit

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.utils import decode_dss_signature
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

RECORD_SIZE = 4096
MAX_PAYLOAD = 3000  # one record, with room to spare
TOKEN_LIFETIME = 12 * 3600  # seconds; push services refuse more than a day


def b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode().rstrip("=")


def unb64url(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def public_bytes(key: ec.EllipticCurvePublicKey) -> bytes:
    """The uncompressed point, 65 bytes, as Web Push writes keys."""
    return key.public_bytes(
        serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint
    )


@dataclass(frozen=True, slots=True)
class Vapid:
    """The shop server's signing key: private part from VAPID_PRIVATE_KEY, public part given
    to browsers when they subscribe."""

    private: ec.EllipticCurvePrivateKey
    subject: str  # mailto: or https: address the push service can reach the sender at

    @classmethod
    def from_text(cls, private_key: str, subject: str) -> Vapid:
        number = int.from_bytes(unb64url(private_key.strip()), "big")
        return cls(ec.derive_private_key(number, ec.SECP256R1()), subject)

    @property
    def public_key(self) -> str:
        return b64url(public_bytes(self.private.public_key()))

    def header(self, endpoint: str, now: float | None = None) -> str:
        """The Authorization header for one push service."""
        parts = urlsplit(endpoint)
        claims = {
            "aud": f"{parts.scheme}://{parts.netloc}",
            "exp": int((now or time.time()) + TOKEN_LIFETIME),
            "sub": self.subject,
        }
        head = b64url(json.dumps({"typ": "JWT", "alg": "ES256"}, separators=(",", ":")).encode())
        body = b64url(json.dumps(claims, separators=(",", ":")).encode())
        signing_input = f"{head}.{body}".encode()
        r, s = decode_dss_signature(self.private.sign(signing_input, ec.ECDSA(hashes.SHA256())))
        signature = b64url(r.to_bytes(32, "big") + s.to_bytes(32, "big"))
        return f"vapid t={head}.{body}.{signature}, k={self.public_key}"


def new_private_key() -> str:
    key = ec.generate_private_key(ec.SECP256R1())
    return b64url(key.private_numbers().private_value.to_bytes(32, "big"))


def _hkdf(secret: bytes, salt: bytes, info: bytes, length: int) -> bytes:
    return HKDF(algorithm=hashes.SHA256(), length=length, salt=salt, info=info).derive(secret)


def encrypt(
    payload: bytes,
    p256dh: str,
    auth: str,
    *,
    sender: ec.EllipticCurvePrivateKey | None = None,
    salt: bytes | None = None,
) -> bytes:
    """The message body for one subscription: a header, then one aes128gcm record. A fresh
    sender key and salt every time; tests pass the RFC's own to check against its example."""
    if len(payload) > MAX_PAYLOAD:
        raise ValueError("push payload too large")
    receiver = unb64url(p256dh)
    secret = unb64url(auth)
    sender = sender or ec.generate_private_key(ec.SECP256R1())
    sender_public = public_bytes(sender.public_key())
    shared = sender.exchange(
        ec.ECDH(), ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), receiver)
    )
    ikm = _hkdf(shared, secret, b"WebPush: info\x00" + receiver + sender_public, 32)
    salt = salt or os.urandom(16)
    key = _hkdf(ikm, salt, b"Content-Encoding: aes128gcm\x00", 16)
    nonce = _hkdf(ikm, salt, b"Content-Encoding: nonce\x00", 12)
    record = AESGCM(key).encrypt(nonce, payload + b"\x02", None)  # 0x02: the last record
    header = salt + struct.pack("!IB", RECORD_SIZE, len(sender_public)) + sender_public
    return header + record
