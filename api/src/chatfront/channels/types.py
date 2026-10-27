"""Messages normalised across WhatsApp, Instagram, Messenger and the web chat."""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Any

from chatfront.channels.models import Channel


class Kind(StrEnum):
    TEXT = "text"
    IMAGE = "image"
    AUDIO = "audio"
    VIDEO = "video"
    DOCUMENT = "document"
    LOCATION = "location"
    BUTTON = "button"
    ORDER = "order"
    UNSUPPORTED = "unsupported"


MEDIA_KINDS = frozenset({Kind.IMAGE, Kind.AUDIO, Kind.VIDEO, Kind.DOCUMENT})


@dataclass(frozen=True)
class Inbound:
    """One customer message, whatever channel it came from."""

    channel: Channel
    account_external_id: str
    sender_id: str
    message_id: str
    sent_at: datetime
    kind: Kind
    text: str | None = None
    media_ref: str | None = None
    payload: dict[str, Any] = field(default_factory=dict)
    profile_name: str | None = None
    # Set only by trusted code: the owner console after checking a signed session.
    staff_id: uuid.UUID | None = None
    # Media already stored by us (web uploads); channel media arrives as media_ref instead.
    media_id: uuid.UUID | None = None


@dataclass(frozen=True)
class StatusUpdate:
    channel: Channel
    message_id: str
    status: str
    error: str | None = None


class Capability(StrEnum):
    TEXT = "text"
    BUTTONS = "buttons"
    LIST = "list"
    TEMPLATE = "template"
    LOCATION = "location"  # ask for a location pin with one tap
    FLOW = "flow"  # a WhatsApp Flow: a form inside the chat


@dataclass(frozen=True)
class OutText:
    text: str


@dataclass(frozen=True)
class Button:
    id: str
    title: str


@dataclass(frozen=True)
class OutButtons:
    """Up to three reply buttons, such as Approve and Reject for the owner."""

    text: str
    buttons: tuple[Button, ...]


@dataclass(frozen=True)
class ListRow:
    id: str
    title: str
    description: str | None = None


@dataclass(frozen=True)
class OutList:
    text: str
    button_label: str
    rows: tuple[ListRow, ...]


@dataclass(frozen=True)
class OutTemplate:
    """An approved template: the only thing allowed outside the 24-hour window."""

    name: str
    lang: str
    params: tuple[str, ...] = ()


@dataclass(frozen=True)
class OutLocationRequest:
    """Asks for the customer's location pin: WhatsApp's Send location button, or the web
    chat's Share my location button."""

    text: str


@dataclass(frozen=True)
class OutFlow:
    """A WhatsApp Flow published in WhatsApp Manager, opened on its first screen with data.
    The form comes back as an inbound message carrying what the owner filled in."""

    text: str
    flow_id: str
    cta: str
    token: str  # comes back with the answers, to tell which form they belong to
    screen: str
    data: dict[str, Any]


Outbound = OutText | OutButtons | OutList | OutTemplate | OutLocationRequest | OutFlow


def required_capability(message: Outbound) -> Capability:
    match message:
        case OutText():
            return Capability.TEXT
        case OutButtons():
            return Capability.BUTTONS
        case OutList():
            return Capability.LIST
        case OutTemplate():
            return Capability.TEMPLATE
        case OutLocationRequest():
            return Capability.LOCATION
        case OutFlow():
            return Capability.FLOW
    raise AssertionError(message)  # pragma: no cover


def to_record(message: Outbound) -> tuple[str, dict[str, Any]]:
    """The text and structured parts of an outbound message, as stored in the messages table."""
    match message:
        case OutText(text=text):
            return text, {}
        case OutButtons(text=text, buttons=buttons):
            return text, {"buttons": [{"id": b.id, "title": b.title} for b in buttons]}
        case OutList(text=text, button_label=label, rows=rows):
            items = [{"id": r.id, "title": r.title, "description": r.description} for r in rows]
            return text, {"list": {"button": label, "rows": items}}
        case OutTemplate(name=name, lang=lang, params=params):
            return " ".join(params), {"template": {"name": name, "lang": lang}}
        case OutLocationRequest(text=text):
            return text, {"location_request": True}
        case OutFlow(text=text, token=token):
            return text, {"flow": {"token": token}}
    raise AssertionError(message)  # pragma: no cover
