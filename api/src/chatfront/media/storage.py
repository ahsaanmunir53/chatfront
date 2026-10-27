"""Where media bytes live: Supabase Storage in production, a local folder in development and tests.

Keys always start with the tenant id, and files are only ever handed out by signed,
expiring URLs.
"""

from __future__ import annotations

import hashlib
import hmac
import time
from pathlib import Path
from typing import Protocol
from urllib.parse import quote

import httpx

from chatfront.core.errors import AppError
from chatfront.core.http import request


class Storage(Protocol):
    async def put(self, key: str, data: bytes, content_type: str) -> None: ...
    async def get(self, key: str) -> bytes: ...
    async def delete(self, key: str) -> None: ...
    async def signed_url(self, key: str, ttl_seconds: int) -> str: ...


def _safe_key(key: str) -> str:
    if key.startswith("/") or ".." in key.split("/") or "\\" in key:
        raise AppError("bad_media_key", "Invalid media key")
    return key


class LocalStorage:
    """Files on disk; URLs are signed with the app secret and served by /media."""

    def __init__(self, root: Path, public_base_url: str, secret: str) -> None:
        self.root = root
        self.public_base_url = public_base_url.rstrip("/")
        self.secret = secret.encode()

    def _path(self, key: str) -> Path:
        return self.root / _safe_key(key)

    async def put(self, key: str, data: bytes, content_type: str) -> None:  # noqa: ARG002
        path = self._path(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)

    async def get(self, key: str) -> bytes:
        path = self._path(key)
        if not path.is_file():
            raise AppError("media_missing", "Media not found", status_code=404)
        return path.read_bytes()

    async def delete(self, key: str) -> None:
        self._path(key).unlink(missing_ok=True)

    def _signature(self, key: str, expires: int) -> str:
        return hmac.new(self.secret, f"{key}:{expires}".encode(), hashlib.sha256).hexdigest()

    async def signed_url(self, key: str, ttl_seconds: int) -> str:
        expires = int(time.time()) + ttl_seconds
        signature = self._signature(_safe_key(key), expires)
        return f"{self.public_base_url}/media/{quote(key)}?exp={expires}&sig={signature}"

    def verify(self, key: str, expires: int, signature: str) -> bool:
        if expires < time.time():
            return False
        return hmac.compare_digest(self._signature(key, expires), signature)


class SupabaseStorage:
    """Supabase Storage over its REST API, with the service key (server side only)."""

    def __init__(self, base_url: str, service_key: str, bucket: str, client: httpx.AsyncClient):
        self.api = base_url.rstrip("/") + "/storage/v1"
        self.bucket = bucket
        self.client = client
        self.headers = {"Authorization": f"Bearer {service_key}", "apikey": service_key}

    def _object(self, key: str) -> str:
        return f"{self.api}/object/{self.bucket}/{quote(_safe_key(key))}"

    async def put(self, key: str, data: bytes, content_type: str) -> None:
        headers = {**self.headers, "Content-Type": content_type, "x-upsert": "true"}
        await request(
            self.client,
            "POST",
            self._object(key),
            service="supabase",
            content=data,
            headers=headers,
        )

    async def get(self, key: str) -> bytes:
        url = f"{self.api}/object/authenticated/{self.bucket}/{quote(_safe_key(key))}"
        response = await request(self.client, "GET", url, service="supabase", headers=self.headers)
        return response.content

    async def delete(self, key: str) -> None:
        url = f"{self.api}/object/{self.bucket}"
        await request(
            self.client,
            "DELETE",
            url,
            service="supabase",
            headers=self.headers,
            json={"prefixes": [_safe_key(key)]},
        )

    async def signed_url(self, key: str, ttl_seconds: int) -> str:
        url = f"{self.api}/object/sign/{self.bucket}/{quote(_safe_key(key))}"
        response = await request(
            self.client,
            "POST",
            url,
            service="supabase",
            headers=self.headers,
            json={"expiresIn": ttl_seconds},
        )
        signed = response.json().get("signedURL")
        if not isinstance(signed, str):
            raise AppError("supabase_sign_failed", "Supabase did not return a signed URL")
        return self.api + signed
