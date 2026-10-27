"""The try-it chat on the shop page. Same agent as WhatsApp; delivery is the messages table.

Judges cannot reach a WhatsApp test number, so this channel is how they try the shop. It has
no 24-hour window, because the shop owns it.
"""

from __future__ import annotations

from typing import Any

from chatfront.channels.base import AccountRef
from chatfront.channels.models import Channel
from chatfront.channels.types import Capability, Inbound, Outbound, StatusUpdate
from chatfront.core.errors import AppError
from chatfront.core.ids import new_id


class WebChatAdapter:
    channel = Channel.WEBCHAT
    capabilities = frozenset(
        {Capability.TEXT, Capability.BUTTONS, Capability.LIST, Capability.LOCATION}
    )
    has_service_window = False

    def parse(self, payload: dict[str, Any]) -> tuple[list[Inbound], list[StatusUpdate]]:
        del payload
        raise AppError("not_a_webhook", "The web chat has no webhook; it posts to its own API")

    async def send(self, account: AccountRef, to: str, message: Outbound) -> str:
        del account, to, message  # the stored outbound row is what the page shows
        return f"web-{new_id()}"

    async def download_media(self, account: AccountRef, media_ref: str) -> bytes:
        del account, media_ref
        raise AppError("not_supported", "Web chat uploads are stored directly")
