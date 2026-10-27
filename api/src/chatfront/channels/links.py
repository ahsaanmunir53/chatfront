"""Links that open a chat with the shop in the customer's own app: wa.me, ig.me and m.me.

These are Meta's official short links. The ref on ig.me and m.me comes back in the
webhook's referral, so the conversation is credited to the shop page that sent it.
"""

from __future__ import annotations

import re
from urllib.parse import quote, urlencode

from chatfront.channels.models import Channel
from chatfront.core.i18n import t

_REF_UNSAFE = re.compile(r"[^A-Za-z0-9_=-]")


def ref_for(slug: str) -> str:
    return _REF_UNSAFE.sub("_", f"shop_{slug}")


def chat_link(channel: str, handle: str, *, shop_name: str, slug: str, lang: str) -> str | None:
    """The link that opens a chat with this account, or None for channels without one."""
    match channel:
        case Channel.WHATSAPP:
            digits = re.sub(r"\D", "", handle)
            text = t("link.whatsapp_greeting", lang, shop=shop_name)
            return f"https://wa.me/{digits}?{urlencode({'text': text})}" if digits else None
        case Channel.INSTAGRAM:
            username = quote(handle.strip().lstrip("@"))
            return f"https://ig.me/m/{username}?ref={ref_for(slug)}" if username else None
        case Channel.MESSENGER:
            page = quote(handle.strip())
            return f"https://m.me/{page}?ref={ref_for(slug)}" if page else None
    return None
