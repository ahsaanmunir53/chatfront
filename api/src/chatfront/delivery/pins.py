"""Location pins (feature 33): from WhatsApp's location message, the web chat's Share my
location button, or a Google Maps link pasted in a message."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from chatfront.channels.models import Channel
from chatfront.channels.types import Outbound, OutLocationRequest, OutText
from chatfront.core.i18n import t

_COORDINATES = r"(?P<lat>-?\d{1,2}\.\d{3,})\s*,\s*(?P<lng>-?\d{1,3}\.\d{3,})"
# Full Google Maps links carry the coordinates: ?q=31.52,74.35, /@31.52,74.35,17z, ll=...
_MAP_LINK = re.compile(
    r"https?://(?:www\.|maps\.)?google\.[a-z.]+/\S*?(?:[?&](?:q|ll|query|destination)=|@)"
    + _COORDINATES,
    re.IGNORECASE,
)
_PIN_CHANNELS = frozenset({Channel.WHATSAPP, Channel.WEBCHAT})  # can send a pin with one tap


@dataclass(frozen=True, slots=True)
class Pin:
    lat: float
    lng: float
    label: str = ""
    source: str = ""

    @property
    def maps_url(self) -> str:
        return f"https://maps.google.com/?q={self.lat:.6f},{self.lng:.6f}"

    def as_dict(self) -> dict[str, Any]:
        return {"lat": self.lat, "lng": self.lng, "label": self.label, "source": self.source}


def _pin(lat: object, lng: object, label: str, source: str) -> Pin | None:
    try:
        latitude, longitude = float(str(lat)), float(str(lng))
    except ValueError:
        return None
    if not (-90 <= latitude <= 90 and -180 <= longitude <= 180) or (latitude, longitude) == (0, 0):
        return None
    return Pin(round(latitude, 6), round(longitude, 6), label[:120], source)


def from_message(payload: dict[str, Any], source: str) -> Pin | None:
    """A shared location: {"location": {"latitude", "longitude", "name", "address"}}."""
    location = payload.get("location") or {}
    label = ", ".join(str(location[k]) for k in ("name", "address") if location.get(k))
    return _pin(location.get("latitude"), location.get("longitude"), label, source)


def from_text(text: str | None) -> Pin | None:
    """A Google Maps link with its coordinates in it. Short maps.app.goo.gl links hide them,
    so the agent asks for the pin instead."""
    match = _MAP_LINK.search(text or "")
    return _pin(match["lat"], match["lng"], "", "link") if match else None


def pin_request(channel: str, lang: str) -> Outbound:
    """Ask for a pin the way the channel allows: WhatsApp's own Send location button, the web
    chat's Share my location button, else a request for a maps link or a landmark."""
    if channel in _PIN_CHANNELS:
        return OutLocationRequest(t("delivery.ask_pin", lang))
    return OutText(t("delivery.ask_pin_link", lang))
