import json
from urllib.parse import parse_qs, urlparse

import httpx
import pytest
import respx

from chatfront.channels.base import AccountRef
from chatfront.channels.links import chat_link, ref_for
from chatfront.channels.messaging import (
    MetaMessagingAdapter,
    instagram_adapter,
    messenger_adapter,
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
)
from chatfront.core.errors import AppError

IG = "https://graph.instagram.com/v23.0"
FB = "https://graph.facebook.com/v23.0"


def ig_payload(*events: dict[str, object], obj: str = "instagram") -> dict[str, object]:
    return {"object": obj, "entry": [{"id": "IG1", "time": 1, "messaging": list(events)}]}


def event(**body: object) -> dict[str, object]:
    return {
        "sender": {"id": "IGSID9"},
        "recipient": {"id": "IG1"},
        "timestamp": 1793000000000,
        **body,
    }


@pytest.fixture
def instagram() -> MetaMessagingAdapter:
    return instagram_adapter(httpx.AsyncClient(), "https://graph.instagram.com", "v23.0")


@pytest.fixture
def messenger() -> MetaMessagingAdapter:
    return messenger_adapter(httpx.AsyncClient(), "https://graph.facebook.com", "v23.0")


def test_parse_instagram_events(instagram: MetaMessagingAdapter) -> None:
    adapter = instagram
    payload = ig_payload(
        event(message={"mid": "m1", "text": "price?"}, referral={"ref": "shop_gulmohar-lawn"}),
        event(message={"mid": "m2", "text": "Medium", "quick_reply": {"payload": "size:m"}}),
        event(
            message={
                "mid": "m3",
                "attachments": [{"type": "image", "payload": {"url": "https://cdn.test/p.jpg"}}],
            }
        ),
        event(
            message={
                "mid": "m4",
                "attachments": [{"type": "share", "payload": {"url": "https://cdn.test/post"}}],
            }
        ),
        event(
            message={
                "mid": "m7",
                "attachments": [
                    {
                        "type": "ig_reel",
                        "payload": {"url": "https://cdn.test/reel", "reel_video_id": "R9"},
                    }
                ],
            }
        ),
        event(message={"mid": "m5", "text": "our own reply", "is_echo": True}),
        event(postback={"mid": "m6", "title": "Track order", "payload": "track"}),
        event(delivery={"mids": ["out1", "out2"]}),
        event(read={"mid": "out2"}),
        {"broken": True},
    )
    inbound, statuses = adapter.parse(payload)
    assert [m.kind for m in inbound] == [
        Kind.TEXT,
        Kind.BUTTON,
        Kind.IMAGE,
        Kind.UNSUPPORTED,
        Kind.VIDEO,  # a shared reel is read as a video (feature 88)
        Kind.BUTTON,
    ]
    first = inbound[0]
    assert (first.channel, first.account_external_id, first.sender_id) == (
        Channel.INSTAGRAM,
        "IG1",
        "IGSID9",
    )
    assert first.payload["referral"] == {"ref": "shop_gulmohar-lawn"}
    assert inbound[1].payload["button_id"] == "size:m"
    assert inbound[2].media_ref == "https://cdn.test/p.jpg"
    assert inbound[3].media_ref is None
    assert inbound[3].payload["url"] == "https://cdn.test/post"
    assert (inbound[4].media_ref, inbound[4].payload["reel_id"]) == ("https://cdn.test/reel", "R9")
    assert (inbound[5].text, inbound[5].payload["button_id"], inbound[5].message_id) == (
        "Track order",
        "track",
        "m6",
    )
    assert [(s.message_id, s.status) for s in statuses] == [
        ("out1", "delivered"),
        ("out2", "delivered"),
        ("out2", "read"),
    ]


def test_parse_ignores_other_objects(
    instagram: MetaMessagingAdapter, messenger: MetaMessagingAdapter
) -> None:
    payload = ig_payload(event(message={"mid": "m1", "text": "x"}))
    assert messenger.parse(payload) == ([], [])
    page = ig_payload(event(message={"mid": "m1", "text": "x"}), obj="page")
    inbound, _ = messenger.parse(page)
    assert inbound[0].channel is Channel.MESSENGER


@respx.mock
async def test_send_shapes(
    instagram: MetaMessagingAdapter, messenger: MetaMessagingAdapter
) -> None:
    ig_route = respx.post(f"{IG}/IG1/messages").mock(
        return_value=httpx.Response(200, json={"recipient_id": "x", "message_id": "mid.1"})
    )
    fb_route = respx.post(f"{FB}/PAGE1/messages").mock(
        return_value=httpx.Response(200, json={"recipient_id": "x", "message_id": "mid.2"})
    )
    ig, fb = instagram, messenger
    assert await ig.send(AccountRef("IG1", "igtok"), "IGSID9", OutText("salam")) == "mid.1"
    await ig.send(
        AccountRef("IG1", "igtok"),
        "IGSID9",
        OutButtons("Pay?", (Button("yes", "Haan, transfer karunga"), Button("cod", "COD"))),
    )
    await ig.send(
        AccountRef("IG1", "igtok"),
        "IGSID9",
        OutList("Size?", "Sizes", tuple(ListRow(str(n), f"Size {n}") for n in range(20))),
    )
    assert await fb.send(AccountRef("PAGE1", "fbtok"), "PSID", OutText("hi")) == "mid.2"
    bodies = [json.loads(c.request.content) for c in ig_route.calls]
    assert ig_route.calls[0].request.headers["Authorization"] == "Bearer igtok"
    assert bodies[0] == {"recipient": {"id": "IGSID9"}, "message": {"text": "salam"}}
    replies = bodies[1]["message"]["quick_replies"]
    assert replies[0] == {"content_type": "text", "title": "Haan, transfer karun", "payload": "yes"}
    assert len(bodies[2]["message"]["quick_replies"]) == 13
    assert json.loads(fb_route.calls[0].request.content)["messaging_type"] == "RESPONSE"


@respx.mock
async def test_long_urdu_is_clipped_on_a_character(instagram: MetaMessagingAdapter) -> None:
    route = respx.post(f"{IG}/IG1/messages").mock(
        return_value=httpx.Response(200, json={"message_id": "m"})
    )
    await instagram.send(AccountRef("IG1", "t"), "u", OutText("سلام " * 400))
    text = json.loads(route.calls[0].request.content)["message"]["text"]
    assert len(text.encode()) <= 1000
    assert text.endswith("…")


async def test_send_errors(instagram: MetaMessagingAdapter) -> None:
    with pytest.raises(AppError, match="token"):
        await instagram.send(AccountRef("IG1", None), "u", OutText("x"))
    with pytest.raises(AppError, match="cannot send"):
        await instagram.send(AccountRef("IG1", "t"), "u", OutTemplate("t", "en"))
    with respx.mock:
        respx.post(f"{IG}/IG1/messages").mock(return_value=httpx.Response(200, json={}))
        with pytest.raises(AppError, match="message id"):
            await instagram.send(AccountRef("IG1", "t"), "u", OutText("x"))


@respx.mock
async def test_download_goes_to_the_cdn_without_a_token(instagram: MetaMessagingAdapter) -> None:
    route = respx.get("https://cdn.test/p.jpg").mock(return_value=httpx.Response(200, content=b"j"))
    assert await instagram.download_media(AccountRef("IG1", "t"), "https://cdn.test/p.jpg") == b"j"
    assert "Authorization" not in route.calls[0].request.headers


def test_chat_links() -> None:
    wa = chat_link(
        "whatsapp",
        "+92 300 1234567",
        shop_name="Saffron",
        slug="saffron-kitchen",
        lang="ur-Latn",
    )
    assert wa is not None
    parsed = urlparse(wa)
    assert (parsed.netloc, parsed.path) == ("wa.me", "/923001234567")
    assert parse_qs(parsed.query)["text"] == [
        "Salam Saffron! Main ne aap ki dukaan Chatfront par dekhi."
    ]
    assert (
        chat_link("instagram", "@saffron.pk", shop_name="S", slug="saffron-kitchen", lang="en")
        == "https://ig.me/m/saffron.pk?ref=shop_saffron-kitchen"
    )
    assert (
        chat_link("messenger", "saffronpk", shop_name="S", slug="s", lang="en")
        == "https://m.me/saffronpk?ref=shop_s"
    )
    assert chat_link("webchat", "x", shop_name="S", slug="s", lang="en") is None
    assert chat_link("whatsapp", "n/a", shop_name="S", slug="s", lang="en") is None
    assert ref_for("a.b c") == "shop_a_b_c"
