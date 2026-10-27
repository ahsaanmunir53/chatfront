"""The interface every channel implements, plus the rules every channel shares."""

from __future__ import annotations

import hashlib
import hmac
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Protocol

from chatfront.channels.models import Channel
from chatfront.channels.types import (
    Capability,
    Inbound,
    Outbound,
    OutTemplate,
    StatusUpdate,
    required_capability,
)
from chatfront.core.errors import AppError

SERVICE_WINDOW = timedelta(hours=24)


@dataclass(frozen=True)
class AccountRef:
    """Enough to act as one channel account: its id at the provider and its token."""

    external_id: str
    access_token: str | None


class ChannelAdapter(Protocol):
    channel: Channel
    capabilities: frozenset[Capability]
    has_service_window: bool

    def parse(self, payload: dict[str, Any]) -> tuple[list[Inbound], list[StatusUpdate]]: ...
    async def send(self, account: AccountRef, to: str, message: Outbound) -> str: ...
    async def download_media(self, account: AccountRef, media_ref: str) -> bytes: ...


class WindowClosed(AppError):
    def __init__(self) -> None:
        super().__init__(
            "window_closed",
            "More than 24 hours since the customer's last message; only templates may be sent",
        )


class Unsupported(AppError):
    def __init__(self, channel: Channel, capability: Capability) -> None:
        super().__init__("unsupported_message", f"{channel} cannot send {capability}")


def check_sendable(
    adapter: ChannelAdapter, message: Outbound, last_inbound_at: datetime | None, now: datetime
) -> None:
    """Enforce the 24-hour service window and the channel's capabilities before any send."""
    capability = required_capability(message)
    if capability not in adapter.capabilities:
        raise Unsupported(adapter.channel, capability)
    if not adapter.has_service_window or isinstance(message, OutTemplate):
        return
    if last_inbound_at is None or now - last_inbound_at > SERVICE_WINDOW:
        raise WindowClosed


def verify_meta_signature(raw_body: bytes, header: str | None, app_secret: str) -> bool:
    """Meta signs every webhook body with the app secret: X-Hub-Signature-256: sha256=<hex>."""
    if not header or not header.startswith("sha256="):
        return False
    expected = hmac.new(app_secret.encode(), raw_body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, header.removeprefix("sha256="))
