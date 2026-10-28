"""Comment-to-DM (feature 77): a price question under a post gets the product by private reply,
once, within 7 days; a post that names no product waits for the owner."""

from __future__ import annotations

import json
from datetime import timedelta
from typing import Any

import httpx
import pytest
import respx
from sqlalchemy import select

from chatfront.channels import comments
from chatfront.channels.models import (
    Channel,
    CommentStatus,
    Conversation,
    Message,
    PostComment,
    SocialPost,
)
from chatfront.core.clock import utcnow
from chatfront.services import Services
from tests.integration.chat import GRAPH, Client, code_of, owner_auth, signed

IG = "https://graph.instagram.com/v23.0"
IG_SECRET = b"test-ig-secret"


def ig_comment(comment_id: str, text: str, author: str = "IGU1", media: str = "MEDIA1") -> Any:
    """The Instagram Login shape: the field sits on the entry itself."""
    return {
        "object": "instagram",
        "entry": [
            {
                "id": "IG1",
                "time": int(utcnow().timestamp()),
                "field": "comments",
                "value": {
                    "id": comment_id,
                    "from": {"id": author, "username": "ayesha.k"},
                    "text": text,
                    "media": {"id": media, "media_product_type": "FEED"},
                },
            }
        ],
    }


def fb_comment(comment_id: str, text: str, *, author: str = "FBU1", days_ago: int = 0) -> Any:
    return {
        "object": "page",
        "entry": [
            {
                "id": "PAGE1",
                "time": 1,
                "changes": [
                    {
                        "field": "feed",
                        "value": {
                            "item": "comment",
                            "verb": "add",
                            "comment_id": comment_id,
                            "post_id": "PAGE1_99",
                            "from": {"id": author, "name": "Bilal"},
                            "message": text,
                            "created_time": int((utcnow() - timedelta(days=days_ago)).timestamp()),
                        },
                    }
                ],
            }
        ],
    }


async def _post(client: Client, payload: Any, secret: bytes = b"test-app-secret") -> None:
    body, headers = signed(payload, secret)
    assert (await client.post("/webhooks/meta", content=body, headers=headers)).status_code == 200


async def _run(services: Services) -> None:
    assert services.worker is not None
    while await services.worker.run_once():
        pass


async def _comment(services: Services, comment_id: str) -> PostComment:
    async with services.db.system() as session:
        found = await session.scalar(
            select(PostComment).where(PostComment.comment_id == comment_id)
        )
    assert found is not None
    return found


async def test_a_price_question_gets_the_product_in_a_private_reply_once(
    app: tuple[Client, Services],
) -> None:
    client, services = app
    lawn = await code_of(client, "Printed lawn 3-piece")
    await _post(client, ig_comment("C1", "price plz?"), IG_SECRET)
    await _post(client, ig_comment("C1", "price plz?"), IG_SECRET)  # Meta retries
    with respx.mock:
        respx.get(f"{IG}/MEDIA1").mock(
            return_value=httpx.Response(
                200,
                json={"caption": f"New lawn, code {lawn}", "permalink": "https://ig.test/p/1"},
            )
        )
        sent = respx.post(f"{IG}/IG1/messages").mock(
            return_value=httpx.Response(200, json={"recipient_id": "IGSID1", "message_id": "m1"})
        )
        await _run(services)
    body = json.loads(sent.calls[0].request.content)
    assert body["recipient"] == {"comment_id": "C1"}
    text = body["message"]["text"]
    assert "Printed lawn 3-piece: Rs 4,850" in text
    assert "Sea green, Rust" in text
    assert f"/s/gulmohar-lawn#p-{lawn}" in text

    answered = await _comment(services, "C1")
    assert (answered.status, answered.reason) == (CommentStatus.SENT, None)
    async with services.db.system() as session:
        post = await session.scalar(select(SocialPost))
        reply = await session.scalar(select(Message).where(Message.external_id == "m1"))
        assert post is not None
        assert (post.linked_by, post.permalink) == ("caption", "https://ig.test/p/1")
        assert reply is not None
        assert reply.conversation_id == answered.conversation_id

    # The same person again, and a compliment, get nothing.
    await _post(client, ig_comment("C2", "rate kya hai?"), IG_SECRET)
    await _post(client, ig_comment("C3", "so pretty 😍", author="IGU2"), IG_SECRET)
    await _run(services)
    assert (await _comment(services, "C2")).reason == "already_answered"
    assert (await _comment(services, "C3")).reason == "not_a_question"

    # When they answer in DMs, it is the same chat, and the agent has the product in view.
    dm = {
        "object": "instagram",
        "entry": [
            {
                "id": "IG1",
                "time": 1,
                "messaging": [
                    {
                        "sender": {"id": "IGSID1"},
                        "recipient": {"id": "IG1"},
                        "timestamp": int(utcnow().timestamp() * 1000),
                        "message": {"mid": "in-1", "text": "rust wala chahiye"},
                    }
                ],
            }
        ],
    }
    await _post(client, dm, IG_SECRET)
    async with services.db.system() as session:
        chats = list(await session.scalars(select(Conversation)))
    assert [chat.id for chat in chats] == [answered.conversation_id]


async def test_a_post_without_a_product_waits_for_the_owner(app: tuple[Client, Services]) -> None:
    client, services = app
    owner = await owner_auth(services)
    await _post(client, fb_comment("F1", "kitne ka hai?"))
    await _post(client, fb_comment("F2", "price?", author="FBU2", days_ago=8))
    await _post(client, fb_comment("F3", "price?", author="PAGE1"))  # the Page's own comment
    with respx.mock:
        respx.get(f"{GRAPH}/PAGE1_99").mock(
            return_value=httpx.Response(
                200, json={"message": "Eid collection!", "permalink_url": "https://fb.test/99"}
            )
        )
        await _run(services)
    waiting = await _comment(services, "F1")
    assert (waiting.status, waiting.reason) == (CommentStatus.WAITING, "which_product")
    assert (await _comment(services, "F2")).reason == "too_old"
    async with services.db.system() as session:
        assert await session.scalar(
            select(PostComment.id).where(PostComment.comment_id == "F3")
        ) is None  # fmt: skip

    listed = (await client.get("/api/owner/posts", headers=owner)).json()
    post = listed["posts"][0]
    assert (post["waiting"], post["permalink"], post["caption"]) == (
        1,
        "https://fb.test/99",
        "Eid collection!",
    )
    assert {c["status"] for c in listed["comments"]} == {"waiting", "skipped"}

    products = (await client.get("/api/owner/products", headers=owner)).json()
    kurta = next(p["id"] for p in products if p["name"] == "Linen kurta")
    chosen = await client.put(
        f"/api/owner/posts/{post['id']}/product", json={"product_id": kurta}, headers=owner
    )
    assert chosen.json() == {"answering": 1}
    with respx.mock:
        sent = respx.post(f"{GRAPH}/PAGE1/messages").mock(
            return_value=httpx.Response(200, json={"recipient_id": "PSID1", "message_id": "m2"})
        )
        await _run(services)
    assert "Linen kurta" in json.loads(sent.calls[0].request.content)["message"]["text"]
    assert (await _comment(services, "F1")).status == CommentStatus.SENT
    after = (await client.get("/api/owner/posts", headers=owner)).json()["posts"][0]
    assert (after["product_name"], after["linked_by"], after["answered"]) == (
        "Linen kurta",
        "owner",
        1,
    )
    unknown = await client.put(
        f"/api/owner/posts/{post['id']}/product",
        json={"product_id": "01900000-0000-7000-8000-000000000000"},
        headers=owner,
    )
    assert unknown.json()["error"]["code"] == "unknown_product"


async def test_failures_and_the_hourly_limit(
    app: tuple[Client, Services], monkeypatch: pytest.MonkeyPatch
) -> None:
    client, services = app
    lawn = await code_of(client, "Printed lawn 3-piece")
    await _post(client, ig_comment("C1", f"{lawn} price?", media="M2"), IG_SECRET)
    with respx.mock:
        respx.get(f"{IG}/M2").mock(return_value=httpx.Response(500))
        respx.post(f"{IG}/IG1/messages").mock(return_value=httpx.Response(400, json={}))
        await _run(services)
    failed = await _comment(services, "C1")
    assert failed.status == CommentStatus.FAILED  # the code in the comment named the product

    monkeypatch.setattr(comments, "HOURLY_LIMIT", 0)
    await _post(client, ig_comment("C9", "price?", author="IGU9"), IG_SECRET)
    await _run(services)
    assert (await _comment(services, "C9")).reason == "hourly_limit"


def test_reading_comment_webhooks_and_questions() -> None:
    facebook = comments.parse(fb_comment("F1", "price?"))
    assert [(c.channel, c.comment_id, c.media_id) for c in facebook] == [
        (Channel.MESSENGER, "F1", "PAGE1_99")
    ]
    edited = fb_comment("F2", "price?")
    edited["entry"][0]["changes"][0]["value"]["verb"] = "edited"
    assert comments.parse(edited) == []
    with_changes = ig_comment("C5", "dm")
    entry = with_changes["entry"][0]
    entry["changes"] = [{"field": "comments", "value": entry.pop("value")}]
    del entry["field"]
    assert [c.comment_id for c in comments.parse(with_changes)] == ["C5"]
    assert comments.parse({"object": "user", "entry": []}) == []
    assert comments.parse({"object": "page", "entry": [{"id": "P", "changes": ["bad"]}]}) == []
    for question in ("Price?", "pp", "kitne ka hai", "قیمت کیا ہے؟", "how much for 2", "بكم؟"):
        assert comments.asks(question), question
    for remark in ("so pretty", "MashaAllah", "😍😍"):
        assert not comments.asks(remark), remark
