"""Posting and sales on real PostgreSQL: flash and live sales with first-come holds (feature 8),
leads from stories, mentions and ads and the orders they brought (78, 81), the morning post of
new stock and one product put up everywhere (7, 82), and the cleaned photo and its cards (3)."""

from __future__ import annotations

import io
import json
from datetime import timedelta
from typing import Any

import httpx
import respx
from PIL import Image
from sqlalchemy import select, update

from chatfront.catalog import sales
from chatfront.catalog.models import Product, SaleHold, Variant
from chatfront.channels.leads import LeadKind
from chatfront.channels.models import Conversation, Lead, Message, PostComment, SocialPost
from chatfront.core.clock import utcnow
from chatfront.core.i18n import t
from chatfront.jobs.queue import enqueue
from chatfront.media import service as media_service
from chatfront.orders.models import Order, OrderItem
from chatfront.publish import service as publishing
from chatfront.publish.models import Publication
from chatfront.services import Services
from tests.integration.chat import (
    GRAPH,
    Client,
    button,
    code_of,
    last,
    owner_auth,
    say,
    settle,
    shop_id,
    signed,
)
from tests.integration.conftest import SHOP
from tests.integration.shopping import guest_of, place, turn

LANG = "ur-Latn"
LAWN = "Printed lawn 3-piece"
DUPATTA = "Embroidered chiffon dupatta"
IG = "https://graph.instagram.com/v23.0"
IG_SECRET = b"test-ig-secret"


async def start_sale(client: Client, services: Services, **body: Any) -> Any:
    response = await client.post(
        "/api/owner/sales",
        json={"name": "Lawn flash sale", "minutes": 60, **body},
        headers=await owner_auth(services),
    )
    assert response.status_code == 200, response.text
    return response.json()


async def holds(services: Services) -> list[SaleHold]:
    async with services.db.system() as session:
        return list(await session.scalars(select(SaleHold).order_by(SaleHold.created_at)))


async def one_colour(services: Services, name: str, colour: str) -> None:
    """Leave a product with one colour on sale, so a claim needs no choice."""
    async with services.db.system() as session:
        for variant in await session.scalars(
            select(Variant)
            .join(Product, Product.id == Variant.product_id)
            .where(Product.name == name, Product.tenant_id == await shop_id(session))
        ):
            variant.active = colour in variant.options.values()


def picture(colour: str = "teal") -> bytes:
    out = io.BytesIO()
    Image.new("RGB", (640, 800), colour).save(out, "JPEG")
    return out.getvalue()


async def with_photo(services: Services, name: str) -> None:
    async with services.db.system() as session:
        tenant_id = await shop_id(session)
    async with services.db.tenant(tenant_id) as session:
        media = await media_service.store(
            session, services.storage, tenant_id, picture(), source="owner", max_bytes=10**7
        )
        product = await session.scalar(select(Product).where(Product.name == name))
        assert product is not None
        product.image_media_id = media.id


def ig_dm(mid: str, sender: str, **message: Any) -> dict[str, Any]:
    return {
        "object": "instagram",
        "entry": [
            {
                "id": "IG1",
                "time": 1,
                "messaging": [
                    {
                        "sender": {"id": sender},
                        "recipient": {"id": "IG1"},
                        "timestamp": int(utcnow().timestamp() * 1000),
                        "message": {"mid": mid, **message},
                    }
                ],
            }
        ],
    }


async def webhook(client: Client, payload: dict[str, Any]) -> None:
    body, headers = signed(payload, IG_SECRET)
    assert (await client.post("/webhooks/meta", content=body, headers=headers)).status_code == 200


async def run(services: Services) -> None:
    assert services.worker is not None
    while await services.worker.run_once():
        pass


# -- flash sales (feature 8) -------------------------------------------------------------------


async def test_a_flash_sale_goes_to_whoever_takes_it_first(app: tuple[Client, Services]) -> None:
    client, services = app
    lawn = await code_of(client, LAWN)
    owner = await owner_auth(services)
    higher = await client.post(
        "/api/owner/sales",
        json={
            "name": "Up",
            "minutes": 60,
            "items": [{"product_code": lawn, "price": 500_000, "units": 1}],
        },
        headers=owner,
    )
    assert higher.json()["error"]["code"] == "sale_not_lower"
    sale = await start_sale(
        client, services, items=[{"product_code": lawn, "price": 399_900, "units": 1}]
    )
    assert (sale["state"], sale["items"][0]["left"]) == ("on", 1)
    shown = {p["code"]: p for p in (await client.get(f"/api/shops/{SHOP}/products")).json()}
    assert shown[lawn]["sale"]["price"] == 399_900
    assert {v["price"] for v in shown[lawn]["variants"]} == {399_900}

    first = await guest_of(client)
    add = {"add_to_cart": {"product_code": lawn, "options": {"colour": "Rust"}}}
    _, found = await turn(client, services, first, "sale wala lawn", add)
    assert found[-1]["ok"] is True
    async with services.db.system() as session:
        line = await session.scalar(select(OrderItem))
    assert line is not None
    assert (line.unit_price, line.list_price) == (399_900, 485_000)

    second = await guest_of(client)
    _, found = await turn(client, services, second, "mujhe bhi sale lawn", add)
    assert found[-1]["error"] == "sale_sold_out"

    order = await place(client, services, first)
    assert [hold.expires_at for hold in await holds(services)] == [None]  # kept for good
    listed = (await client.get("/api/owner/sales", headers=owner)).json()
    assert (listed[0]["items"][0]["ordered"], listed[0]["items"][0]["left"]) == (1, 0)

    cancelled = await client.post(
        f"/api/owner/orders/{order.code}/status", json={"to": "cancelled"}, headers=owner
    )
    assert cancelled.status_code == 200
    assert await holds(services) == []  # the piece is free again
    stopped = await client.post(f"/api/owner/sales/{sale['id']}/stop", headers=owner)
    assert stopped.json()["state"] == "ended"
    shown = {p["code"]: p for p in (await client.get(f"/api/shops/{SHOP}/products")).json()}
    assert shown[lawn]["sale"] is None


async def test_a_hold_that_runs_out_leaves_the_cart(app: tuple[Client, Services]) -> None:
    client, services = app
    dupatta = await code_of(client, DUPATTA)
    await start_sale(
        client,
        services,
        hold_minutes=10,
        items=[{"product_code": dupatta, "price": 150_000, "units": 2, "per_customer": 1}],
    )
    guest = await guest_of(client)
    add = {"add_to_cart": {"product_code": dupatta, "options": {"colour": "Black"}, "quantity": 2}}
    _, found = await turn(client, services, guest, "sale dupatta black", add)
    assert found[-1]["error"] == "sale_limit"
    add["add_to_cart"]["quantity"] = 1
    await turn(client, services, guest, "phir sale dupatta black", add)
    async with services.db.system() as session:
        await session.execute(update(SaleHold).values(expires_at=utcnow() - timedelta(minutes=1)))
        await enqueue(session, sales.JOB, {}, tenant_id=None)
    told = last(await settle(client, services, SHOP, guest))
    assert told["body"] == t("sale.hold_ended", LANG, name=DUPATTA)
    async with services.db.system() as session:
        assert await session.scalar(select(OrderItem)) is None
    assert await holds(services) == []


async def test_a_live_sale_is_claimed_by_number_in_the_chat_and_in_comments(
    app: tuple[Client, Services],
) -> None:
    client, services = app
    lawn, dupatta = await code_of(client, LAWN), await code_of(client, DUPATTA)
    await one_colour(services, DUPATTA, "Black")
    await start_sale(
        client,
        services,
        name="Friday live",
        live=True,
        items=[
            {"product_code": dupatta, "price": 150_000, "units": 1},
            {"product_code": lawn, "price": 399_900, "units": 2},
        ],
    )
    guest = await guest_of(client)
    token = guest["X-Chat-Token"]
    took = last(await say(client, services, SHOP, guest, "1", token=token))
    assert took["body"] == t(
        "sale.claimed", LANG, number=1, name=DUPATTA, price="Rs 1,500", minutes=15
    )
    choose = last(await say(client, services, SHOP, guest, "#2 chahiye", token=token))
    assert choose["body"] == t("sale.choose", LANG, number=2, name=LAWN)
    assert [b["title"] for b in choose["payload"]["buttons"]] == ["Sea green", "Rust"]
    rust = last(await say(client, services, SHOP, guest, token=token, **button(choose, 1)))
    assert rust["body"].startswith("#2")
    assert "Rust" in rust["body"]

    other = await guest_of(client)
    gone = last(await say(client, services, SHOP, other, "1", token=other["X-Chat-Token"]))
    assert gone["body"] == t("sale.sold_out", LANG, number=1, name=DUPATTA)

    # A comment on the live takes the last lawn piece, its first colour, in a private reply.
    live_comment = {
        "object": "instagram",
        "entry": [
            {
                "id": "IG1",
                "time": 1,
                "field": "live_comments",
                "value": {
                    "id": "LC1",
                    "from": {"id": "IGU7", "username": "sana"},
                    "text": "2",
                    "media": {"id": "LIVE1", "media_product_type": "LIVE"},
                },
            }
        ],
    }
    await webhook(client, live_comment)
    await webhook(client, live_comment)  # Meta retries; one piece only
    with respx.mock:
        sent = respx.post(f"{IG}/IG1/messages").mock(
            return_value=httpx.Response(200, json={"recipient_id": "IGU7", "message_id": "pr1"})
        )
        await run(services)
    reply = json.loads(sent.calls[0].request.content)
    assert reply["recipient"] == {"comment_id": "LC1"}
    assert reply["message"]["text"].startswith("#2")
    assert len(sent.calls) == 1
    lawn_holds = [hold.quantity for hold in await holds(services)]
    assert sorted(lawn_holds) == [1, 1, 1]
    async with services.db.system() as session:
        comment = await session.scalar(select(PostComment))
    assert comment is not None
    assert comment.claim_reply == reply["message"]["text"]


# -- leads and what they brought (features 78 and 81) -----------------------------------------


async def test_ads_stories_and_mentions_are_leads_and_orders_are_credited(
    app: tuple[Client, Services],
) -> None:
    client, services = app
    referral = {
        "ref": "lawn",
        "ad_id": "AD42",
        "source": "ADS",
        "type": "OPEN_THREAD",
        "ads_context_data": {"ad_title": "Lawn sale"},
    }
    await webhook(client, ig_dm("ad-1", "IGSID9", text="price?", referral=referral))
    story = {"story": {"id": "ST1", "url": "https://cdn.ig/st1"}}
    await webhook(client, ig_dm("st-1", "IGSID8", text="ye kitne ka?", reply_to=story))
    mention = [{"type": "story_mention", "payload": {"url": "https://cdn.ig/m1"}}]
    await webhook(client, ig_dm("mn-1", "IGSID7", attachments=mention))
    async with services.db.system() as session:
        found = {lead.kind: lead for lead in await session.scalars(select(Lead))}
    assert (found[LeadKind.AD].ref, found[LeadKind.AD].title) == ("AD42", "Lawn sale")
    assert (found[LeadKind.STORY_REPLY].ref, found[LeadKind.STORY_REPLY].url) == (
        "ST1",
        "https://cdn.ig/st1",
    )
    assert found[LeadKind.STORY_MENTION].url == "https://cdn.ig/m1"

    with respx.mock:
        respx.post(f"{IG}/IG1/messages").mock(
            return_value=httpx.Response(200, json={"message_id": "out-1"})
        )
        await run(services)
    async with services.db.system() as session:
        mention_chat = found[LeadKind.STORY_MENTION].conversation_id
        thanks = await session.scalar(
            select(Message.body).where(
                Message.conversation_id == mention_chat, Message.direction == "out"
            )
        )
    assert thanks == t("lead.mention_thanks", "en", shop="Gulmohar Lawn House") or thanks == t(
        "lead.mention_thanks", LANG, shop="Gulmohar Lawn House"
    )

    # An order from a customer an ad brought is credited to that ad.
    lawn = await code_of(client, LAWN)
    guest = await guest_of(client)
    add = {"add_to_cart": {"product_code": lawn, "options": {"colour": "Rust"}}}
    await turn(client, services, guest, "lawn chahiye", add)
    async with services.db.system() as session:
        chat = await session.scalar(
            select(Conversation).where(
                Conversation.id.not_in([found[kind].conversation_id for kind in found])
            )
        )
        assert chat is not None
        session.add(
            Lead(
                tenant_id=chat.tenant_id,
                customer_id=chat.customer_id,
                conversation_id=chat.id,
                channel="instagram",
                kind=LeadKind.AD,
                ref="AD42",
                title="Lawn sale",
            )
        )
    order = await place(client, services, guest)
    async with services.db.system() as session:
        credited = await session.get(Order, order.id)
    assert credited is not None
    assert credited.attribution is not None
    assert (credited.attribution["kind"], credited.attribution["ref"]) == ("ad", "AD42")

    board = (await client.get("/api/owner/leads", headers=await owner_auth(services))).json()
    assert {lead["kind"] for lead in board["leads"]} == {
        "ad",
        "story_reply",
        "story_mention",
    }
    ad = next(row for row in board["brought"] if row["ref"] == "AD42")
    assert (ad["chats"], ad["orders"], ad["title"]) == (2, 1, "Lawn sale")
    assert ad["value"] == order.total


# -- posts (features 7 and 82) and pictures (feature 3) ----------------------------------------


async def test_one_product_is_put_up_everywhere_once_the_owner_says_so(
    app: tuple[Client, Services],
) -> None:
    client, services = app
    owner = await owner_auth(services)
    lawn = await code_of(client, LAWN)
    no_photo = await client.post(
        "/api/owner/publications/product", json={"product_code": lawn}, headers=owner
    )
    assert no_photo.json()["error"]["code"] == "no_photo"
    await with_photo(services, LAWN)
    made = await client.post(
        "/api/owner/publications/product", json={"product_code": lawn}, headers=owner
    )
    assert made.status_code == 200, made.text
    draft = made.json()
    assert (draft["kind"], draft["status"], draft["by_model"]) == ("product", "draft", True)
    assert draft["captions"]["en"].startswith("New stock is in")
    assert len(draft["images"]) == 1
    assert draft["images"][0]["post_url"]

    with respx.mock:
        created = respx.post(f"{IG}/IG1/media").mock(
            return_value=httpx.Response(200, json={"id": "CONTAINER1"})
        )
        published = respx.post(f"{IG}/IG1/media_publish").mock(
            return_value=httpx.Response(200, json={"id": "IGPOST1"})
        )
        photo = respx.post(f"{GRAPH}/PAGE1/photos").mock(
            return_value=httpx.Response(200, json={"id": "PH1", "post_id": "PAGE1_5"})
        )
        sent = await client.post(
            f"/api/owner/publications/{draft['id']}/send",
            json={"targets": ["instagram", "facebook", "whatsapp_catalog", "whatsapp_status"]},
            headers=owner,
        )
        assert sent.status_code == 200, sent.text
        await run(services)
    container = json.loads(created.calls[0].request.content)
    assert f"{LAWN}: Rs 4,850" in container["caption"]
    assert f"/s/{SHOP}#p-{lawn}" in container["caption"]
    assert f"/api/shops/{SHOP}/posters/" in container["image_url"]
    assert json.loads(published.calls[0].request.content) == {"creation_id": "CONTAINER1"}
    assert json.loads(photo.calls[0].request.content)["caption"] == container["caption"]

    after = (await client.get("/api/owner/publications", headers=owner)).json()
    assert after["connected"] == {
        "instagram": True,
        "facebook": True,
        "whatsapp_catalog": False,
    }
    targets = after["items"][0]["targets"]
    assert targets["instagram"] == {"status": "sent", "id": "IGPOST1"}
    assert targets["facebook"] == {"status": "sent", "id": "PAGE1_5"}
    assert targets["whatsapp_catalog"]["status"] == "not_connected"
    assert targets["whatsapp_status"]["status"] == "by_hand"
    async with services.db.system() as session:
        post = await session.scalar(select(SocialPost).where(SocialPost.media_id == "IGPOST1"))
    assert post is not None
    assert post.linked_by == "post"  # comments on it get the lawn suit (feature 77)
    again = await client.post(
        f"/api/owner/publications/{draft['id']}/send",
        json={"targets": ["instagram"]},
        headers=owner,
    )
    assert again.status_code == 409

    # Meta fetches the picture from the shop's poster link; nothing else is served there.
    poster = draft["images"][0]["post_url"]
    async with services.db.system() as session:
        stored = await session.get(Publication, draft["id"])
    assert stored is not None
    media_id = stored.images[0]["post"]
    fetched = await client.get(f"/api/shops/{SHOP}/posters/{media_id}", follow_redirects=False)
    assert fetched.status_code == 302
    assert poster


async def test_the_morning_post_waits_for_the_owner(app: tuple[Client, Services]) -> None:
    client, services = app
    owner = await owner_auth(services)
    await with_photo(services, DUPATTA)
    switched = await client.put("/api/owner/publications/daily", json={"on": True}, headers=owner)
    assert switched.json() == {"on": True}
    async with services.db.system() as session:
        tenant_id = await shop_id(session)
        await enqueue(session, publishing.DAY_JOB, {}, tenant_id=tenant_id)
    await run(services)
    listed = (await client.get("/api/owner/publications", headers=owner)).json()
    assert listed["daily"] is True
    [daily] = listed["items"]
    assert (daily["kind"], daily["status"], daily["products"]) == ("daily", "draft", [DUPATTA])
    skipped = await client.post(f"/api/owner/publications/{daily['id']}/skip", headers=owner)
    assert skipped.json() == {"status": "skipped"}
    today = await client.post("/api/owner/publications/today", headers=owner)
    assert today.status_code == 200
    assert len(today.json()["images"]) == 1


async def test_a_cleaned_photo_is_used_only_when_the_owner_chooses_it(
    app: tuple[Client, Services],
) -> None:
    client, services = app
    owner = await owner_auth(services)
    lawn = await code_of(client, LAWN)
    await with_photo(services, LAWN)
    cleaned = await client.post(f"/api/owner/products/{lawn}/photo/clean", headers=owner)
    assert cleaned.status_code == 200, cleaned.text
    async with services.db.system() as session:
        before = await session.scalar(select(Product.image_media_id).where(Product.code == lawn))
    assert str(before) != cleaned.json()["media_id"]
    used = await client.put(
        f"/api/owner/products/{lawn}/photo",
        json={"media_id": cleaned.json()["media_id"]},
        headers=owner,
    )
    assert used.status_code == 200
    async with services.db.system() as session:
        after = await session.scalar(select(Product.image_media_id).where(Product.code == lawn))
    assert str(after) == cleaned.json()["media_id"]
    card = await client.post(
        f"/api/owner/products/{lawn}/card", params={"shape": "status"}, headers=owner
    )
    assert card.status_code == 200
    assert card.json()["url"]
    not_a_poster = await client.get(
        f"/api/shops/{SHOP}/posters/{cleaned.json()['media_id']}", follow_redirects=False
    )
    assert not_a_poster.status_code == 404
