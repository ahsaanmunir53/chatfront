"""The catalog from what a shop already has, end to end: a sheet or the Meta catalog (feature
85), past Instagram posts (84) and a video of a rack (2) become drafts the owner adds; a shelf
photo sets the stock once the owner confirms (87); size charts and the size code picks (5);
and a clip or a shared reel in the customer's chat (88)."""

from __future__ import annotations

import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any

import httpx
import pytest
import respx
from sqlalchemy import select

from chatfront.ai.types import ImagePart, Role, VideoPart
from chatfront.books.models import EntryKind, LedgerEntry
from chatfront.catalog import meta
from chatfront.catalog.models import (
    CatalogImport,
    CommerceCatalog,
    ImportDraft,
    Product,
    Variant,
)
from chatfront.channels.models import Channel, SocialPost
from chatfront.core.i18n import t
from chatfront.core.models import Tenant
from chatfront.services import Services
from tests.integration.chat import (
    GRAPH,
    Client,
    agent,
    button,
    code_of,
    customer,
    fixture,
    last,
    owner_auth,
    photo,
    products,
    say,
    send_file,
    shop_id,
    sign_in,
    signed,
    use,
)
from tests.integration.conftest import SHOP
from tests.integration.test_selling_more import turn

IG = "https://graph.instagram.com/v23.0"
IMPORTS = "/api/owner/imports"
OWNER = "ur-Latn"  # the demo owner's language
needs_ffmpeg = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="needs ffmpeg")


async def run_worker(services: Services) -> None:
    assert services.worker is not None
    while await services.worker.run_once():
        pass


async def listed(client: Client, owner: dict[str, str]) -> dict[str, Any]:
    response = await client.get(IMPORTS, headers=owner)
    assert response.status_code == 200
    return dict(response.json())


def by_name(found: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {draft["name"]: draft for draft in found["drafts"]}


def seen(name: str, colour: str | None, second: float, price: float | None = None) -> Any:
    return {
        "name": name,
        "description": f"A {name.lower()} on the rack",
        "category": "suits",
        "tags": [],
        "attributes": [],
        "colour": colour,
        "size": None,
        "at_second": second,
        "price": price,
        "price_seen": "said" if price else None,
    }


def a_video() -> bytes:
    """Three seconds of a test pattern as MP4, made by ffmpeg."""
    ffmpeg = shutil.which("ffmpeg")
    assert ffmpeg is not None
    with tempfile.TemporaryDirectory() as folder:
        out = Path(folder) / "rack.mp4"
        subprocess.run(  # noqa: S603 - fixed arguments, no input from outside the test
            [
                ffmpeg,
                "-loglevel",
                "error",
                "-f",
                "lavfi",
                "-i",
                "testsrc=duration=3:size=160x120:rate=10",
                "-pix_fmt",
                "yuv420p",
                str(out),
            ],
            check=True,
        )
        return out.read_bytes()


# -- a sheet (feature 85) ---------------------------------------------------------------------

SHEET = (
    b"title,price,color,item_group_id,description\n"
    b"Silk scarf,2200,Red,S1,Pure silk\n"
    b"Silk scarf,2400,Blue,S1,\n"
    b"Velvet clutch,,,,\n"
    b"Linen kurta,2750,,,\n"
)


async def test_a_sheet_becomes_drafts_the_owner_adds(app: tuple[Client, Services]) -> None:
    client, services = app
    owner = await owner_auth(services)
    run = (
        await client.post(f"{IMPORTS}/sheet", headers=owner, files={"file": ("shop.csv", SHEET)})
    ).json()
    assert (run["source"], run["label"], run["status"], run["found"]) == (
        "sheet",
        "shop.csv",
        "ready",
        3,
    )
    found = await listed(client, owner)
    drafts = by_name(found)
    scarf, clutch, kurta = drafts["Silk scarf"], drafts["Velvet clutch"], drafts["Linen kurta"]
    assert (scarf["price"], scarf["price_from"], scarf["ready"]) == (220000, "sheet", True)
    assert scarf["description"] == "Pure silk"
    assert [(v["options"], v["price"]) for v in scarf["variants"]] == [
        ({"colour": "Red"}, None),
        ({"colour": "Blue"}, 240000),
    ]
    assert (clutch["price"], clutch["ready"]) == (None, False)
    assert kurta["in_shop"] == "Linen kurta"  # the demo shop sells one already

    # "Add the ones with your prices" leaves the look-alike and the one without a price.
    added = await client.post(f"{IMPORTS}/{run['id']}/add-ready", headers=owner)
    assert added.json() == {"added": 1}
    shop = await products(client, SHOP)
    assert [v["price"] for v in shop["Silk scarf"]["variants"]] == [220000, 240000]

    missing = await client.post(f"{IMPORTS}/drafts/{clutch['id']}/add", headers=owner, json={})
    assert missing.json()["error"]["code"] == "draft_incomplete"
    priced = await client.post(
        f"{IMPORTS}/drafts/{clutch['id']}/add", headers=owner, json={"price": 1500}
    )
    assert priced.json()["name"] == "Velvet clutch"
    assert (await products(client, SHOP))["Velvet clutch"]["price"] == 150000
    again = await client.post(f"{IMPORTS}/drafts/{clutch['id']}/add", headers=owner, json={})
    assert (again.status_code, again.json()["error"]["code"]) == (409, "draft_done")

    rest = await client.post(f"{IMPORTS}/{run['id']}/drop-rest", headers=owner)
    assert rest.json() == {"dropped": 1}
    after = await listed(client, owner)
    assert after["drafts"] == []
    assert after["runs"][0]["waiting"] == 0


async def test_columns_by_the_model_and_a_sheet_without_prices(
    app: tuple[Client, Services],
) -> None:
    client, services = app
    owner = await owner_auth(services)
    use(
        services,
        fixture(
            "map_product_columns",
            {
                "name": "Cheez",
                "price": "Daam",
                "stock": "Kitne",
                "fields": [
                    {"key": "size", "header": "Naap"},
                    {"key": "mood", "header": "Kitne"},
                ],
            },
        ),
    )
    sheet = b"Cheez,Daam,Naap,Kitne\nWool sweater,3200,M,4\nWool sweater,3200,L,2\n"
    response = await client.post(
        f"{IMPORTS}/sheet", headers=owner, files={"file": ("dukaan.csv", sheet)}
    )
    assert response.json()["found"] == 1
    sweater = by_name(await listed(client, owner))["Wool sweater"]
    assert [(v["options"], v["stock"]) for v in sweater["variants"]] == [
        ({"size": "M"}, 4),
        ({"size": "L"}, 2),
    ]

    no_price = await client.post(
        f"{IMPORTS}/sheet", headers=owner, files={"file": ("x.csv", b"Item,Qty\nPen,3\n")}
    )
    assert no_price.json()["error"]["code"] == "no_columns"


# -- the Meta catalog (feature 85) ------------------------------------------------------------


async def test_the_meta_catalog_comes_in_and_gives_way_to_the_shops_own(
    app: tuple[Client, Services],
) -> None:
    client, services = app
    owner = await owner_auth(services)
    refused = await client.post(f"{IMPORTS}/meta-catalog", headers=owner)
    assert refused.json()["error"]["code"] == "catalog_not_connected"
    await meta.connect(
        services.db, services.settings, shop=SHOP, catalog_id="CAT1", token="catalog-token"
    )
    lawn = await code_of(client, "Printed lawn 3-piece")
    first = {
        "data": [
            {
                "retailer_id": "SC-R",
                "name": "Silk scarf",
                "price": "Rs2,200.00",
                "color": "Red",
                "retailer_product_group_id": "SC",
                "inventory": 3,
                "image_url": "https://scontent.xx.fbcdn.net/scarf.jpg",
            },
            {
                "retailer_id": "SC-B",
                "name": "Silk scarf",
                "price": "Rs2,200.00",
                "color": "Blue",
                "retailer_product_group_id": "SC",
            },
            {"retailer_id": f"{lawn}-1", "name": "Printed lawn 3-piece", "price": "Rs4,850.00"},
            {
                "retailer_id": "BOX",
                "name": "Gift box",
                "price": "Rs500.00",
                "image_url": "https://example.com/box.jpg",
            },
        ],
        "paging": {"next": f"{GRAPH}/CAT1/products?after=X"},
    }
    second = {"data": [{"retailer_id": "TEA", "name": "Kashmiri tea set", "price": ""}]}
    assert (await client.post(f"{IMPORTS}/meta-catalog", headers=owner)).json()["queued"]
    busy = await client.post(f"{IMPORTS}/meta-catalog", headers=owner)
    assert (busy.status_code, busy.json()["error"]["code"]) == (409, "import_running")
    with respx.mock:
        pages = respx.get(url__startswith=f"{GRAPH}/CAT1/products").mock(
            side_effect=[httpx.Response(200, json=first), httpx.Response(200, json=second)]
        )
        respx.get("https://scontent.xx.fbcdn.net/scarf.jpg").mock(
            return_value=httpx.Response(200, content=photo())
        )
        await run_worker(services)
    assert pages.calls[0].request.headers["Authorization"] == "Bearer catalog-token"
    assert "retailer_product_group_id" in str(pages.calls[0].request.url)
    drafts = by_name(await listed(client, owner))
    assert set(drafts) == {"Silk scarf", "Gift box", "Kashmiri tea set"}
    scarf = drafts["Silk scarf"]
    assert (scarf["price"], scarf["price_from"], scarf["ready"]) == (220000, "catalog", True)
    assert scarf["photo"] is not None
    assert [(v["options"], v["stock"]) for v in scarf["variants"]] == [
        ({"colour": "Red"}, 3),
        ({"colour": "Blue"}, None),
    ]
    assert drafts["Gift box"]["photo"] is None  # not on Meta's servers, so not fetched
    assert drafts["Kashmiri tea set"]["ready"] is False

    await client.post(f"{IMPORTS}/drafts/{scarf['id']}/add", headers=owner, json={})
    async with services.db.tenant(await _shop(services)) as session:
        link = await session.scalar(select(CommerceCatalog))
        assert link is not None
        assert {"SC-R", "SC-B"} <= set(link.synced_ids)  # deleted at the next send
        added = await session.scalar(select(Product).where(Product.name == "Silk scarf"))
        assert added is not None
        assert (added.source, added.image_media_id is not None) == ("meta_catalog", True)


async def _shop(services: Services) -> Any:
    async with services.db.system() as session:
        return await shop_id(session)


# -- Instagram posts (feature 84) -------------------------------------------------------------


POSTS = {
    "data": [
        {
            "id": "P1",
            "caption": "Chikankari kurta 🌸 #kurta\nRs. 3,200 only\nOrder: 0300-1234567",
            "media_type": "IMAGE",
            "media_url": "https://scontent.cdninstagram.com/p1.jpg",
            "permalink": "https://www.instagram.com/p/P1/",
        },
        {
            "id": "P2",
            "caption": "New velvet shawl, DM for price",
            "media_type": "VIDEO",
            "thumbnail_url": "https://scontent.cdninstagram.com/p2.jpg",
        },
        {
            "id": "P3",
            "caption": "Eid Mubarak!",
            "media_type": "IMAGE",
            "media_url": "https://scontent.cdninstagram.com/p3.jpg",
        },
    ]
}


async def test_past_instagram_posts_become_drafts(app: tuple[Client, Services]) -> None:
    client, services = app
    owner = await owner_auth(services)
    shawl = {
        "name": "Velvet shawl",
        "price": 3500,
        "price_words": "Rs 3500 on the picture",
        "description": "A maroon velvet shawl.",
        "category": "shawls",
        "tags": ["velvet"],
        "attributes": [{"key": "colour", "value": "maroon"}],
        "variants": [],
        "stock": None,
        "missing": [],
    }
    mock = use(
        services,
        fixture(
            "post_product",
            {"is_product": True, "product": shawl, "price_seen": "image"},
            match="velvet shawl",
        ),
        fixture(
            "post_product",
            {"is_product": False, "product": None, "price_seen": None},
            match="Eid Mubarak",
        ),
    )
    assert (await client.post(f"{IMPORTS}/instagram", headers=owner)).json()["queued"]
    with respx.mock:
        feed = respx.get(f"{IG}/IG1/media").mock(return_value=httpx.Response(200, json=POSTS))
        pictures = respx.get(url__startswith="https://scontent.cdninstagram.com/").mock(
            return_value=httpx.Response(200, content=photo())
        )
        await run_worker(services)
    assert feed.calls[0].request.headers["Authorization"] == "Bearer test-ig-token"
    assert len(pictures.calls) == 3
    read = [r for r in mock.requests if (r.prompt or "").startswith("post_product")]
    assert any(isinstance(p, ImagePart) for p in read[0].messages[1].content or [])  # type: ignore[union-attr]

    drafts = by_name(await listed(client, owner))
    assert set(drafts) == {"Chikankari kurta", "Velvet shawl"}  # Eid Mubarak is no product
    kurta, velvet = drafts["Chikankari kurta"], drafts["Velvet shawl"]
    # No recorded answer for the first post: code read its caption.
    assert (kurta["price"], kurta["price_from"], kurta["ready"]) == (320000, "caption", True)
    assert kurta["link"] == "https://www.instagram.com/p/P1/"
    assert (velvet["price"], velvet["price_from"], velvet["ready"]) == (350000, "image", False)

    await client.post(f"{IMPORTS}/drafts/{kurta['id']}/add", headers=owner, json={})
    async with services.db.tenant(await _shop(services)) as session:
        post = await session.scalar(select(SocialPost).where(SocialPost.media_id == "P1"))
        assert post is not None
        assert (post.linked_by, post.permalink) == ("import", "https://www.instagram.com/p/P1/")

    # A second import reads only what is new: P1 shows a product, P2 is drafted already.
    await client.post(f"{IMPORTS}/instagram", headers=owner)
    with respx.mock:
        respx.get(f"{IG}/IG1/media").mock(return_value=httpx.Response(200, json=POSTS))
        again = respx.get(url__startswith="https://scontent.cdninstagram.com/").mock(
            return_value=httpx.Response(200, content=photo())
        )
        await run_worker(services)
    assert [str(c.request.url) for c in again.calls] == ["https://scontent.cdninstagram.com/p3.jpg"]


# -- a video of a rack (feature 2) ------------------------------------------------------------


@needs_ffmpeg
async def test_a_video_of_a_rack_becomes_drafts_with_photos(
    app: tuple[Client, Services],
) -> None:
    client, services = app
    owner = await owner_auth(services)
    clip = a_video()
    mock = use(
        services,
        fixture(
            "video_products",
            {
                "products": [
                    seen("Lawn suit", "Rust", 0.5),
                    seen("Velvet shawl", None, 1.5, 3500),
                    seen("Lawn suit", "Teal", 2.9),
                ]
            },
        ),
    )
    files = {"file": ("rack.mp4", clip, "video/mp4")}
    assert (await client.post(f"{IMPORTS}/video", headers=owner, files=files)).json()["queued"]
    busy = await client.post(f"{IMPORTS}/video", headers=owner, files=files)
    assert busy.json()["error"]["code"] == "import_running"
    await run_worker(services)
    watched = next(r for r in mock.requests if (r.prompt or "").startswith("video_products"))
    assert watched.role == Role.VIDEO
    assert isinstance((watched.messages[1].content or [None])[0], VideoPart)  # type: ignore[index]

    drafts = by_name(await listed(client, owner))
    lawn, shawl = drafts["Lawn suit"], drafts["Velvet shawl"]
    assert [v["options"] for v in lawn["variants"]] == [{"colour": "Rust"}, {"colour": "Teal"}]
    assert (lawn["price"], lawn["ready"]) == (None, False)
    assert (shawl["price"], shawl["price_from"], shawl["ready"]) == (350000, "said", False)
    assert lawn["photo"]  # a still taken from the video
    assert shawl["photo"]
    async with services.db.tenant(await _shop(services)) as session:
        stills = list(await session.scalars(select(ImportDraft.extra)))
    assert sorted(extra["second"] for extra in stills) == [0.5, 1.5]

    added = await client.post(
        f"{IMPORTS}/drafts/{lawn['id']}/add", headers=owner, json={"price": 4200}
    )
    assert added.json()["name"] == "Lawn suit"

    # A photo is not a video.
    wrong = await client.post(
        f"{IMPORTS}/video", headers=owner, files={"file": ("x.png", photo(), "image/png")}
    )
    assert wrong.json()["error"]["code"] == "not_a_video"


@needs_ffmpeg
async def test_a_video_in_the_owners_chat_and_one_that_cannot_be_read(
    app: tuple[Client, Services],
) -> None:
    client, services = app
    slug, auth = await sign_in(client)
    reply = last(await send_file(client, services, slug, auth, a_video(), caption="naya maal"))
    link = f"{services.settings.web_url}/owner"
    assert reply["body"] == t("imports.video_reading", OWNER, link=link)
    async with services.db.system() as session:
        run = await session.scalar(select(CatalogImport).where(CatalogImport.source == "video"))
    assert run is not None
    # No model answer is recorded for this video, so nothing could be read.
    assert (run.status, run.error, run.label) == (
        "failed",
        "mock_fixture_missing",
        "WhatsApp video",
    )


# -- a shelf photo (feature 87) ---------------------------------------------------------------


SHELF = {
    "lines": [
        {"item": "Printed lawn 3-piece Rust", "count": 2},
        {"item": "Printed lawn 3-piece Sea green", "count": 9},
        {"item": "Linen kurta", "count": None},
    ],
    "note": "A rack of lawn suits",
}


async def test_a_shelf_photo_sets_the_stock_once_confirmed(app: tuple[Client, Services]) -> None:
    client, services = app
    use(services, fixture("shelf_count", SHELF))
    slug, auth = await sign_in(client)
    card = last(await send_file(client, services, slug, auth, photo(), caption="stock count"))
    rust = {"name": "Printed lawn 3-piece (Rust)", "before": 4, "counted": 2, "missing": 2}
    green = {"name": "Printed lawn 3-piece (Sea green)", "before": 6, "counted": 9, "extra": 3}
    assert t("stock.count_short", OWNER, **rust) in card["body"]
    assert t("stock.count_extra", OWNER, **green) in card["body"]
    assert t("stock.need_count", OWNER, item="Linen kurta") in card["body"]
    assert card["payload"]["buttons"][0]["title"] == t("stock.count_save", OWNER)
    done = last(await say(client, services, slug, auth, **button(card)))
    assert done["body"] == t("owner.changes_applied", OWNER, count=2)

    async with services.db.system() as session:
        tenant = await session.scalar(select(Tenant.id).where(Tenant.slug == slug))
        rows = await session.execute(
            select(Variant.options, Variant.stock)
            .join(Product, Product.id == Variant.product_id)
            .where(Product.tenant_id == tenant, Product.name == "Printed lawn 3-piece")
        )
        assert sorted((o["colour"], s) for o, s in rows.all()) == [("Rust", 2), ("Sea green", 9)]
        loss = await session.scalar(
            select(LedgerEntry).where(
                LedgerEntry.tenant_id == tenant, LedgerEntry.kind == EntryKind.WRITE_OFF
            )
        )
    assert loss is not None
    assert loss.amount == 2 * 340000  # two pieces at their batch cost


async def test_counting_in_the_console(app: tuple[Client, Services]) -> None:
    client, services = app
    owner = await owner_auth(services)
    unread = await client.post(
        "/api/owner/stock/count/photo", headers=owner, files={"file": ("s.png", photo())}
    )
    assert (unread.status_code, unread.json()["error"]["code"]) == (503, "shelf_unread")
    use(services, fixture("shelf_count", SHELF))
    counted = (
        await client.post(
            "/api/owner/stock/count/photo", headers=owner, files={"file": ("s.png", photo())}
        )
    ).json()
    assert [(r["name"], r["before"], r["counted"]) for r in counted["rows"]] == [
        ("Printed lawn 3-piece (Rust)", 4, 2),
        ("Printed lawn 3-piece (Sea green)", 6, 9),
    ]
    assert counted["problems"] == [t("stock.need_count", OWNER, item="Linen kurta")]
    rust, green = counted["rows"]
    saved = await client.post(
        "/api/owner/stock/count",
        headers=owner,
        json={
            "lines": [
                {"variant_id": rust["variant_id"], "counted": 4},  # as in the books: no change
                {"variant_id": green["variant_id"], "counted": 7},
            ]
        },
    )
    assert saved.json() == {"changed": 1}
    shop = await products(client, SHOP)
    assert [v["in_stock"] for v in shop["Printed lawn 3-piece"]["variants"]] == [True, True]


# -- size charts and size advice (feature 5) --------------------------------------------------


KURTAS_CM = {
    "name": "Kurtas",
    "unit": "cm",
    "categories": ["kurtas"],
    "rows": [
        {"size": "M", "ranges": {"chest": [91, 96]}},
        {"size": "L", "ranges": {"chest": [97, 102]}},
        {"size": "XL", "ranges": {"chest": [103, 110]}},
    ],
}


async def test_size_charts_in_the_console_and_on_the_page(app: tuple[Client, Services]) -> None:
    client, services = app
    owner = await owner_auth(services)
    charts = (await client.get("/api/owner/size-charts", headers=owner)).json()
    assert [c["name"] for c in charts["charts"]] == ["Stitched suits and kurtas"]
    assert charts["usual"] == charts["charts"]
    assert {"kurtas", "suits"} <= set(charts["categories"])
    shop = await products(client, SHOP)
    assert shop["Khaddar 2-piece, stitched"]["size_chart"]["measures"] == ["chest", "waist", "hips"]
    assert shop["Printed lawn 3-piece"]["size_chart"] is None  # colours only, no sizes

    suits = {**charts["charts"][0], "categories": ["suits"]}
    saved = await client.put(
        "/api/owner/size-charts", headers=owner, json={"charts": [suits, KURTAS_CM]}
    )
    assert [c["name"] for c in saved.json()["charts"]] == ["Stitched suits and kurtas", "Kurtas"]
    kurta = (await products(client, SHOP))["Linen kurta"]["size_chart"]
    assert (kurta["unit"], kurta["rows"][0]) == ("cm", {"size": "M", "ranges": {"chest": [91, 96]}})

    backwards = {**KURTAS_CM, "rows": [{"size": "M", "ranges": {"chest": [96, 91]}}]}
    refused = await client.put(
        "/api/owner/size-charts", headers=owner, json={"charts": [backwards]}
    )
    assert refused.status_code == 422


async def test_the_size_comes_from_the_chart_not_the_model(app: tuple[Client, Services]) -> None:
    client, services = app
    khaddar = await code_of(client, "Khaddar 2-piece, stitched")
    lawn = await code_of(client, "Printed lawn 3-piece")
    guest, _ = await customer(client)
    _, answers = await turn(
        client,
        services,
        guest,
        "mera size kya hoga",
        {"size_advice": {"code": khaddar, "chest": 36.5, "waist": 29.5}},
        {"size_advice": {"code": khaddar, "unit": "cm", "chest": 120}},
        {"size_advice": {"code": khaddar}},
        {"size_advice": {"code": lawn, "chest": 36}},
        reply="Aap ka size M hai.",
    )
    fits, tight, ask, none = answers
    assert (fits["size"], fits["in_stock"], fits["chart"]) == (
        "M",
        True,
        "Stitched suits and kurtas",
    )
    assert (tight["size"], tight["tight_even_in_largest"]) == (None, ["chest"])
    assert ask["ask_for"] == ["chest", "waist", "hips"]
    assert none == {
        "ok": False,
        "error": "no_size_chart",
        "hint": "ask which size they usually wear",
    }


# -- a clip or a shared reel in the customer's chat (feature 88) ------------------------------


@needs_ffmpeg
async def test_a_customers_clip_goes_to_the_model_as_a_video(
    app: tuple[Client, Services],
) -> None:
    client, services = app
    guest, _ = await customer(client)
    mock = use(services, agent("*", role="video", content="Yeh hamara lawn suit hai."))
    await send_file(client, services, SHOP, guest, a_video(), caption="yeh wala")
    asked = [r for r in mock.requests if (r.prompt or "").startswith("order_agent")][-1]
    assert asked.role == Role.VIDEO
    assert isinstance((asked.messages[-1].content or [None])[0], VideoPart)  # type: ignore[index]


async def test_the_shops_own_reel_names_its_product(app: tuple[Client, Services]) -> None:
    client, services = app
    khaddar = await code_of(client, "Khaddar 2-piece, stitched")
    async with services.db.system() as session:
        tenant_id = await shop_id(session)
        product_id = await session.scalar(select(Product.id).where(Product.code == khaddar))
        session.add(
            SocialPost(
                tenant_id=tenant_id,
                channel=Channel.INSTAGRAM,
                media_id="R9",
                product_id=product_id,
                linked_by="owner",
            )
        )
    mock = use(services, agent("*", role="video", content="Ji, yeh khaddar suit hai."))
    dm = {
        "object": "instagram",
        "entry": [
            {
                "id": "IG1",
                "time": 1,
                "messaging": [
                    {
                        "sender": {"id": "IGU5"},
                        "recipient": {"id": "IG1"},
                        "timestamp": 1_700_000_000_000,
                        "message": {
                            "mid": "reel-1",
                            "attachments": [
                                {
                                    "type": "ig_reel",
                                    "payload": {
                                        "url": "https://lookaside.fbsbx.com/reel.mp4",
                                        "reel_video_id": "R9",
                                    },
                                }
                            ],
                        },
                    }
                ],
            }
        ],
    }
    body, headers = signed(dm, b"test-ig-secret")
    assert (await client.post("/webhooks/meta", content=body, headers=headers)).status_code == 200
    with respx.mock:
        respx.get("https://lookaside.fbsbx.com/reel.mp4").mock(
            return_value=httpx.Response(200, content=b"\x00\x00\x00\x18ftypmp42" + b"\x00" * 64)
        )
        respx.post(f"{IG}/IG1/messages").mock(
            return_value=httpx.Response(200, json={"recipient_id": "IGU5", "message_id": "o1"})
        )
        await run_worker(services)
    asked = [r for r in mock.requests if (r.prompt or "").startswith("order_agent")][-1]
    notes = [m.text() for m in asked.messages if m.role == "system"]
    assert any(f"(code {khaddar})" in note for note in notes)
