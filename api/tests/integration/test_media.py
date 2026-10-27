import io
import time
from pathlib import Path

import httpx
import pytest
import respx
from PIL import Image

from chatfront.core.db import Database
from chatfront.core.errors import AppError
from chatfront.media import service
from chatfront.media.models import MediaKind
from chatfront.media.storage import LocalStorage, SupabaseStorage
from tests.factories import make_tenant


def png_bytes(color: str = "red", size: tuple[int, int] = (40, 30)) -> bytes:
    out = io.BytesIO()
    Image.new("RGB", size, color).save(out, "PNG")
    return out.getvalue()


@pytest.mark.parametrize(
    ("data", "mime", "kind"),
    [
        (b"\xff\xd8\xff\xe0rest", "image/jpeg", MediaKind.IMAGE),
        (b"RIFF\x00\x00\x00\x00WEBPVP8 ", "image/webp", MediaKind.IMAGE),
        (b"RIFF\x00\x00\x00\x00WAVEfmt ", "audio/wav", MediaKind.AUDIO),
        (b"OggS\x00\x02", "audio/ogg", MediaKind.AUDIO),
        (b"ID3\x04\x00", "audio/mpeg", MediaKind.AUDIO),
        (b"\x00\x00\x00\x20ftypM4A ", "audio/mp4", MediaKind.AUDIO),
        (b"\x00\x00\x00\x20ftypisom", "video/mp4", MediaKind.VIDEO),
        (b"%PDF-1.7", "application/pdf", MediaKind.DOCUMENT),
    ],
)
def test_sniff(data: bytes, mime: str, kind: MediaKind) -> None:
    file_type = service.sniff(data)
    assert (file_type.mime, file_type.kind) == (mime, kind)


def test_sniff_rejects_unknown() -> None:
    with pytest.raises(AppError, match="not supported"):
        service.sniff(b"MZ\x90\x00 an exe")


async def test_store_dedupes_and_blurs(clean_db: Database, tmp_path: Path) -> None:
    tenant = await make_tenant(clean_db)
    storage = LocalStorage(tmp_path, "http://testserver", "secret")
    data = png_bytes()
    async with clean_db.tenant(tenant.id) as session:
        first = await service.store(
            session, storage, tenant.id, data, source="test", max_bytes=10**6
        )
        again = await service.store(
            session, storage, tenant.id, data, source="test", max_bytes=10**6
        )
        assert first.id == again.id
        assert first.storage_key.startswith(f"{tenant.id}/image/")
        await service.add_blurred_copy(storage, first)
    assert first.blurred_key is not None
    blurred = await storage.get(first.blurred_key)
    assert blurred[:3] == b"\xff\xd8\xff"
    url = await service.signed_url(storage, first, 60, blurred=True)
    assert ".blur.jpg" in url


async def test_store_limits(clean_db: Database, tmp_path: Path) -> None:
    tenant = await make_tenant(clean_db)
    storage = LocalStorage(tmp_path, "http://testserver", "secret")
    async with clean_db.tenant(tenant.id) as session:
        with pytest.raises(AppError, match="too large"):
            await service.store(session, storage, tenant.id, png_bytes(), source="t", max_bytes=10)
        audio = await service.store(
            session, storage, tenant.id, b"OggS" + b"\x00" * 50, source="t", max_bytes=10**6
        )
    with pytest.raises(AppError, match="Only images"):
        await service.add_blurred_copy(storage, audio)
    with pytest.raises(AppError, match="no blurred"):
        await service.signed_url(storage, audio, 60, blurred=True)


async def test_local_signed_urls(tmp_path: Path) -> None:
    storage = LocalStorage(tmp_path, "http://testserver/", "secret")
    url = await storage.signed_url("t1/image/a.png", 60)
    query = dict(part.split("=") for part in url.split("?")[1].split("&"))
    assert url.startswith("http://testserver/media/t1/image/a.png?")
    assert storage.verify("t1/image/a.png", int(query["exp"]), query["sig"])
    assert not storage.verify("t1/image/b.png", int(query["exp"]), query["sig"])
    assert not storage.verify("t1/image/a.png", int(time.time()) - 1, query["sig"])
    with pytest.raises(AppError):
        await storage.get("../etc/passwd")
    with pytest.raises(AppError, match="not found"):
        await storage.get("t1/nothing.png")
    await storage.put("t1/x.bin", b"1", "application/octet-stream")
    await storage.delete("t1/x.bin")
    assert not (tmp_path / "t1/x.bin").exists()


@respx.mock
async def test_supabase_storage_calls() -> None:
    base = "https://proj.supabase.co/storage/v1"
    put = respx.post(f"{base}/object/media/t1/a.png").mock(
        side_effect=[httpx.Response(503), httpx.Response(200, json={"Key": "media/t1/a.png"})]
    )
    respx.get(f"{base}/object/authenticated/media/t1/a.png").mock(
        return_value=httpx.Response(200, content=b"png")
    )
    remove = respx.delete(f"{base}/object/media").mock(return_value=httpx.Response(200, json=[]))
    respx.post(f"{base}/object/sign/media/t1/a.png").mock(
        return_value=httpx.Response(200, json={"signedURL": "/object/sign/media/t1/a.png?token=t"})
    )
    async with httpx.AsyncClient() as client:
        storage = SupabaseStorage("https://proj.supabase.co/", "service-key", "media", client)
        await storage.put("t1/a.png", b"png", "image/png")
        assert await storage.get("t1/a.png") == b"png"
        await storage.delete("t1/a.png")
        url = await storage.signed_url("t1/a.png", 60)
    assert put.call_count == 2  # the 503 was retried
    assert put.calls[1].request.headers["x-upsert"] == "true"
    assert put.calls[1].request.headers["apikey"] == "service-key"
    assert remove.calls[0].request.content == b'{"prefixes":["t1/a.png"]}'
    assert url == f"{base}/object/sign/media/t1/a.png?token=t"


@respx.mock
async def test_supabase_sign_without_url_is_an_error() -> None:
    respx.post("https://p.supabase.co/storage/v1/object/sign/m/k").mock(
        return_value=httpx.Response(200, json={})
    )
    async with httpx.AsyncClient() as client:
        storage = SupabaseStorage("https://p.supabase.co", "k", "m", client)
        with pytest.raises(AppError, match="signed URL"):
            await storage.signed_url("k", 60)
