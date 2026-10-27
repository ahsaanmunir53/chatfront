import hashlib
import hmac
import json
from datetime import UTC, datetime, timedelta

import httpx
import pytest
import respx

from chatfront.channels.base import (
    AccountRef,
    Unsupported,
    WindowClosed,
    check_sendable,
    verify_meta_signature,
)
from chatfront.channels.models import Channel
from chatfront.channels.types import (
    Button,
    Kind,
    ListRow,
    OutButtons,
    OutList,
    OutTemplate,
    OutText,
    to_record,
)
from chatfront.channels.webchat import WebChatAdapter
from chatfront.channels.whatsapp import WhatsAppAdapter
from chatfront.core.errors import AppError

NOW = datetime(2026, 10, 27, 12, 0, tzinfo=UTC)
GRAPH = "https://graph.facebook.com/v23.0"


def webhook(
    *messages: dict[str, object],
    statuses: list[dict[str, object]] | None = None,
    field: str = "messages",
) -> dict[str, object]:
    return {
        "object": "whatsapp_business_account",
        "entry": [
            {
                "changes": [
                    {
                        "field": field,
                        "value": {
                            "metadata": {"phone_number_id": "PN1"},
                            "contacts": [{"wa_id": "923001234567", "profile": {"name": "Ayesha"}}],
                            "messages": list(messages),
                            "statuses": statuses or [],
                        },
                    }
                ]
            }
        ],
    }


def msg(kind: str, body: dict[str, object], **extra: object) -> dict[str, object]:
    return {
        "from": "923001234567",
        "id": f"wamid.{kind}",
        "timestamp": "1793000000",
        "type": kind,
        kind: body,
        **extra,
    }


@pytest.fixture
def adapter() -> WhatsAppAdapter:
    return WhatsAppAdapter(httpx.AsyncClient(), "https://graph.facebook.com/", "v23.0")


class TestParse:
    def test_every_kind(self, adapter: WhatsAppAdapter) -> None:
        payload = webhook(
            msg("text", {"body": "salam"}, context={"id": "wamid.prev"}),
            msg("image", {"id": "M1", "mime_type": "image/jpeg", "caption": "yeh wala"}),
            msg("audio", {"id": "M2", "mime_type": "audio/ogg"}),
            msg("location", {"latitude": 31.5, "longitude": 74.3, "name": "Home"}),
            msg(
                "interactive",
                {"type": "button_reply", "button_reply": {"id": "approve:1", "title": "Approve"}},
            ),
            msg("button", {"payload": "track", "text": "Track order"}),
            msg("order", {"catalog_id": "C1", "product_items": [], "text": "cart"}),
            msg("reaction", {"emoji": "👍"}),
        )
        inbound, statuses = adapter.parse(payload)
        kinds = [m.kind for m in inbound]
        assert kinds == [
            Kind.TEXT,
            Kind.IMAGE,
            Kind.AUDIO,
            Kind.LOCATION,
            Kind.BUTTON,
            Kind.BUTTON,
            Kind.ORDER,
            Kind.UNSUPPORTED,
        ]
        text, image = inbound[0], inbound[1]
        assert (text.text, text.profile_name, text.account_external_id) == (
            "salam",
            "Ayesha",
            "PN1",
        )
        assert text.payload["reply_to"] == "wamid.prev"
        assert text.sent_at == datetime.fromtimestamp(1793000000, UTC)
        assert (image.media_ref, image.text, image.payload["mime"]) == (
            "M1",
            "yeh wala",
            "image/jpeg",
        )
        assert inbound[3].payload["location"]["latitude"] == 31.5
        assert inbound[4].payload["button_id"] == "approve:1"
        assert inbound[5].payload["button_id"] == "track"
        assert inbound[6].payload["order"]["catalog_id"] == "C1"
        assert inbound[7].payload["type"] == "reaction"
        assert statuses == []

    def test_statuses_skips_and_other_fields(self, adapter: WhatsAppAdapter) -> None:
        payload = webhook(
            {"id": "broken", "type": "text"},  # no text body: skipped, not a crash
            statuses=[
                {
                    "id": "wamid.out",
                    "status": "failed",
                    "errors": [{"title": "Re-engagement message"}],
                }
            ],
        )
        inbound, statuses = adapter.parse(payload)
        assert inbound == []
        assert statuses[0].status == "failed"
        assert statuses[0].error == "Re-engagement message"
        assert adapter.parse(webhook(msg("text", {"body": "x"}), field="account_update")) == (
            [],
            [],
        )


class TestSend:
    @respx.mock
    async def test_bodies_and_auth(self, adapter: WhatsAppAdapter) -> None:
        route = respx.post(f"{GRAPH}/PN1/messages").mock(
            return_value=httpx.Response(200, json={"messages": [{"id": "wamid.sent"}]})
        )
        account = AccountRef("PN1", "tok")
        messages = [
            OutText("salam"),
            OutButtons("Payment unclear", (Button("ok", "Approve"), Button("no", "Reject"))),
            OutList("Choose", "Sizes", (ListRow("s", "Small", "36"), ListRow("m", "Medium"))),
            OutTemplate("order_dispatched", "ur", ("CF-123",)),
            OutTemplate("hello", "en"),
        ]
        for message in messages:
            assert await adapter.send(account, "923001234567", message) == "wamid.sent"
        bodies = [json.loads(call.request.content) for call in route.calls]
        assert route.calls[0].request.headers["Authorization"] == "Bearer tok"
        assert bodies[0]["text"] == {"body": "salam", "preview_url": False}
        assert bodies[1]["interactive"]["action"]["buttons"][1]["reply"]["title"] == "Reject"
        rows = bodies[2]["interactive"]["action"]["sections"][0]["rows"]
        assert rows[0] == {"id": "s", "title": "Small", "description": "36"}
        assert "description" not in rows[1]
        assert bodies[3]["template"]["components"][0]["parameters"][0]["text"] == "CF-123"
        assert "components" not in bodies[4]["template"]
        assert all(b["messaging_product"] == "whatsapp" for b in bodies)

    async def test_missing_token(self, adapter: WhatsAppAdapter) -> None:
        with pytest.raises(AppError, match="token"):
            await adapter.send(AccountRef("PN1", None), "92300", OutText("x"))

    @respx.mock
    async def test_bad_response(self, adapter: WhatsAppAdapter) -> None:
        respx.post(f"{GRAPH}/PN1/messages").mock(return_value=httpx.Response(200, json={}))
        with pytest.raises(AppError, match="message id"):
            await adapter.send(AccountRef("PN1", "tok"), "92300", OutText("x"))

    @respx.mock
    async def test_download_media(self, adapter: WhatsAppAdapter) -> None:
        respx.get(f"{GRAPH}/M1").mock(
            return_value=httpx.Response(200, json={"url": "https://lookaside.test/m1"})
        )
        respx.get("https://lookaside.test/m1").mock(
            return_value=httpx.Response(200, content=b"img")
        )
        assert await adapter.download_media(AccountRef("PN1", "tok"), "M1") == b"img"
        respx.get(f"{GRAPH}/M2").mock(return_value=httpx.Response(200, json={}))
        with pytest.raises(AppError, match="no media URL"):
            await adapter.download_media(AccountRef("PN1", "tok"), "M2")


class TestRules:
    def test_window(self, adapter: WhatsAppAdapter) -> None:
        check_sendable(adapter, OutText("x"), NOW - timedelta(hours=23), NOW)
        with pytest.raises(WindowClosed):
            check_sendable(adapter, OutText("x"), NOW - timedelta(hours=25), NOW)
        with pytest.raises(WindowClosed):
            check_sendable(adapter, OutText("x"), None, NOW)
        check_sendable(adapter, OutTemplate("t", "en"), None, NOW)  # templates may go any time

    def test_webchat_has_no_window_but_no_templates(self) -> None:
        web = WebChatAdapter()
        check_sendable(web, OutText("x"), None, NOW)
        with pytest.raises(Unsupported):
            check_sendable(web, OutTemplate("t", "en"), None, NOW)

    async def test_webchat_stores_only(self) -> None:
        web = WebChatAdapter()
        assert (await web.send(AccountRef("s", None), "tok", OutText("x"))).startswith("web-")
        with pytest.raises(AppError):
            web.parse({})
        with pytest.raises(AppError):
            await web.download_media(AccountRef("s", None), "m")
        assert web.channel is Channel.WEBCHAT

    def test_signature(self) -> None:
        body = b'{"a":1}'
        good = "sha256=" + hmac.new(b"secret", body, hashlib.sha256).hexdigest()
        assert verify_meta_signature(body, good, "secret")
        assert not verify_meta_signature(body + b" ", good, "secret")
        assert not verify_meta_signature(body, None, "secret")
        assert not verify_meta_signature(body, "md5=abc", "secret")

    def test_to_record(self) -> None:
        assert to_record(OutText("hi")) == ("hi", {})
        _, payload = to_record(OutButtons("Pay?", (Button("y", "Yes"),)))
        assert payload == {"buttons": [{"id": "y", "title": "Yes"}]}
        assert (
            to_record(OutList("Pick", "Open", (ListRow("a", "A"),)))[1]["list"]["button"] == "Open"
        )
        assert to_record(OutTemplate("t", "ur", ("CF-1",))) == (
            "CF-1",
            {"template": {"name": "t", "lang": "ur"}},
        )
