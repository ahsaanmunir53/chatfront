"""Outbound HTTP: one place for timeouts, retries with backoff and clear errors."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Mapping
from typing import Any

import httpx

from chatfront.core.errors import AppError

log = logging.getLogger(__name__)

RETRYABLE_STATUS = frozenset({408, 425, 429, 500, 502, 503, 504})


class UpstreamError(AppError):
    """A call to another service failed; `retryable` says whether trying again could help."""

    status_code = 502

    def __init__(self, service: str, message: str, *, status: int | None, retryable: bool) -> None:
        super().__init__("upstream_error", f"{service}: {message}")
        self.service = service
        self.status = status
        self.retryable = retryable


async def request(
    client: httpx.AsyncClient,
    method: str,
    url: str,
    *,
    service: str,
    retries: int = 2,
    backoff_seconds: float = 0.5,
    json: Any = None,
    content: bytes | None = None,
    headers: Mapping[str, str] | None = None,
    params: Mapping[str, str] | None = None,
) -> httpx.Response:
    """Send a request, retrying timeouts and retryable statuses with exponential backoff.

    Only pass retries > 0 for requests that are safe to repeat.
    """
    attempt = 0
    while True:
        try:
            response = await client.request(
                method, url, json=json, content=content, headers=headers, params=params
            )
        except httpx.TransportError as exc:
            error = UpstreamError(service, type(exc).__name__, status=None, retryable=True)
        else:
            if response.is_success:
                return response
            retryable = response.status_code in RETRYABLE_STATUS
            detail = response.text[:300]
            error = UpstreamError(
                service,
                f"HTTP {response.status_code} {detail}",
                status=response.status_code,
                retryable=retryable,
            )
        if not error.retryable or attempt >= retries:
            raise error
        delay = backoff_seconds * (2**attempt)
        log.warning("%s failed (%s), retrying in %.1fs", service, error.message, delay)
        await asyncio.sleep(delay)
        attempt += 1
