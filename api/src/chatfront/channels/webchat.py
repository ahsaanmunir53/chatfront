"""The try-it chat on the shop page. Same agent as WhatsApp; delivery is the messages table.

Judges cannot reach a WhatsApp test number, so this channel is how they try the shop. It has
no 24-hour window, because the shop owns it.
"""

from __future__ import annotations

import hashlib
import uuid
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from chatfront.channels import service as channels
from chatfront.channels.base import AccountRef
from chatfront.channels.models import Channel, ChannelAccount
from chatfront.channels.types import Capability, Inbound, Kind, Outbound, StatusUpdate
from chatfront.core.clock import utcnow
from chatfront.core.errors import AppError
from chatfront.core.ids import new_id

if TYPE_CHECKING:
    from chatfront.services import Services


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


@dataclass(frozen=True, slots=True)
class ChatIdentity:
    """Who writes in the web chat: a visitor by their session token, or signed-in staff."""

    sender_id: str
    staff_id: uuid.UUID | None


async def accept_web(
    services: Services,
    account: ChannelAccount,
    identity: ChatIdentity,
    client_id: uuid.UUID,
    *,
    kind: Kind,
    text: str | None,
    payload: dict[str, Any] | None = None,
    media_id: uuid.UUID | None = None,
) -> uuid.UUID | None:
    """Store a message typed into the web chat and queue its reply; None for a repeat."""
    tag = hashlib.sha256(identity.sender_id.encode()).hexdigest()[:12]
    message = Inbound(
        channel=Channel.WEBCHAT,
        account_external_id=account.external_id,
        sender_id=identity.sender_id,
        message_id=f"web:{tag}:{client_id}",
        sent_at=utcnow(),
        kind=kind,
        text=text,
        payload=payload or {},
        staff_id=identity.staff_id,
        media_id=media_id,
    )
    return await channels.accept(services.db, account, message, services.keys)
