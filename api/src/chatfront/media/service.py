"""Storing media: size limits, type sniffing, per-tenant keys, dedupe and blurred copies; and
the form a model reads it in."""

from __future__ import annotations

import asyncio
import base64
import hashlib
import io
import logging
import shutil
import uuid
from dataclasses import dataclass

from PIL import Image, ImageFilter
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from chatfront.ai.types import AudioPart, ImagePart, Part, VideoPart
from chatfront.core.clock import utcnow
from chatfront.core.errors import AppError
from chatfront.core.ids import new_id
from chatfront.media.models import Media, MediaKind
from chatfront.media.storage import Storage

log = logging.getLogger(__name__)

MODEL_IMAGE_SIDE = 1600  # longest side sent to the model; phone photos are often 4000
AUDIO_TIMEOUT_SECONDS = 30


@dataclass(frozen=True)
class FileType:
    mime: str
    kind: MediaKind
    ext: str


def sniff(data: bytes) -> FileType:
    """Decide the type from the bytes themselves, never from what the sender claimed."""
    head = data[:16]
    if head.startswith(b"\xff\xd8\xff"):
        return FileType("image/jpeg", MediaKind.IMAGE, "jpg")
    if head.startswith(b"\x89PNG\r\n\x1a\n"):
        return FileType("image/png", MediaKind.IMAGE, "png")
    if head[:4] == b"RIFF" and head[8:12] == b"WEBP":
        return FileType("image/webp", MediaKind.IMAGE, "webp")
    if head[:4] == b"RIFF" and head[8:12] == b"WAVE":
        return FileType("audio/wav", MediaKind.AUDIO, "wav")
    if head.startswith(b"OggS"):
        return FileType("audio/ogg", MediaKind.AUDIO, "ogg")
    if head.startswith(b"ID3") or head[:2] in (b"\xff\xfb", b"\xff\xf3", b"\xff\xf2"):
        return FileType("audio/mpeg", MediaKind.AUDIO, "mp3")
    if head[4:8] == b"ftyp":
        brand = head[8:12]
        if brand in (b"M4A ", b"M4B "):
            return FileType("audio/mp4", MediaKind.AUDIO, "m4a")
        return FileType("video/mp4", MediaKind.VIDEO, "mp4")
    if head.startswith(b"\x1a\x45\xdf\xa3"):  # WebM, what browsers record voice notes in
        return FileType("audio/webm", MediaKind.AUDIO, "webm")
    if head.startswith(b"%PDF-"):
        return FileType("application/pdf", MediaKind.DOCUMENT, "pdf")
    raise AppError("unsupported_media", "This file type is not supported", status_code=415)


async def store(
    session: AsyncSession,
    storage: Storage,
    tenant_id: uuid.UUID,
    data: bytes,
    *,
    source: str,
    max_bytes: int,
) -> Media:
    """Save bytes for a tenant. The same file sent twice is stored once."""
    if len(data) > max_bytes:
        raise AppError("media_too_large", "The file is too large", status_code=413)
    file_type = sniff(data)
    digest = hashlib.sha256(data).hexdigest()
    existing = await session.scalar(
        select(Media).where(Media.tenant_id == tenant_id, Media.sha256 == digest)
    )
    if existing is not None:
        return existing
    media_id = new_id()
    now = utcnow()
    key = f"{tenant_id}/{file_type.kind}/{now:%Y/%m}/{media_id}.{file_type.ext}"
    await storage.put(key, data, file_type.mime)
    media = Media(
        id=media_id,
        tenant_id=tenant_id,
        kind=file_type.kind,
        mime=file_type.mime,
        size_bytes=len(data),
        sha256=digest,
        storage_key=key,
        source=source,
    )
    session.add(media)
    await session.flush()
    return media


def blur_image(data: bytes) -> bytes:
    """A strongly blurred JPEG copy, for photos that must not be kept readable (CNIC, cards)."""
    with Image.open(io.BytesIO(data)) as image:
        rgb = image.convert("RGB")
        radius = max(rgb.size) / 25
        out = io.BytesIO()
        rgb.filter(ImageFilter.GaussianBlur(radius)).save(out, "JPEG", quality=70)
        return out.getvalue()


async def add_blurred_copy(storage: Storage, media: Media) -> Media:
    if media.kind != MediaKind.IMAGE:
        raise AppError("not_an_image", "Only images can be blurred")
    blurred = blur_image(await storage.get(media.storage_key))
    key = media.storage_key.rsplit(".", 1)[0] + ".blur.jpg"
    await storage.put(key, blurred, "image/jpeg")
    media.blurred_key = key
    return media


async def signed_url(storage: Storage, media: Media, ttl_seconds: int, *, blurred: bool) -> str:
    key = media.blurred_key if blurred else media.storage_key
    if key is None:
        raise AppError("no_blurred_copy", "This media has no blurred copy")
    return await storage.signed_url(key, ttl_seconds)


def data_url(mime: str, data: bytes) -> str:
    return f"data:{mime};base64,{base64.b64encode(data).decode()}"


def shrink_image(data: bytes) -> bytes:
    """A JPEG no larger than MODEL_IMAGE_SIDE on its longest side."""
    with Image.open(io.BytesIO(data)) as image:
        rgb = image.convert("RGB")
        rgb.thumbnail((MODEL_IMAGE_SIDE, MODEL_IMAGE_SIDE))
        out = io.BytesIO()
        rgb.save(out, "JPEG", quality=85)
        return out.getvalue()


async def to_wav(data: bytes) -> bytes | None:
    """16 kHz mono WAV from any voice note (WhatsApp OGG, browser WebM), with ffmpeg."""
    ffmpeg = shutil.which("ffmpeg")
    if ffmpeg is None:
        return None
    process = await asyncio.create_subprocess_exec(
        ffmpeg,
        "-hide_banner",
        "-loglevel",
        "error",
        "-i",
        "pipe:0",
        "-ac",
        "1",
        "-ar",
        "16000",
        "-f",
        "wav",
        "pipe:1",
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        out, _ = await asyncio.wait_for(process.communicate(data), AUDIO_TIMEOUT_SECONDS)
    except TimeoutError:
        process.kill()
        return None
    return out if process.returncode == 0 and out else None


async def model_part(storage: Storage, media: Media, *, video_ttl_seconds: int = 900) -> Part:
    """What a model is given for this media: images and voice notes inline as data URLs, so
    no public link is needed; videos, which are large, as a short-lived signed link."""
    if media.kind == MediaKind.VIDEO:
        return VideoPart(await storage.signed_url(media.storage_key, video_ttl_seconds))
    data = await storage.get(media.storage_key)
    if media.kind == MediaKind.IMAGE:
        return ImagePart(data_url("image/jpeg", shrink_image(data)))
    if media.kind == MediaKind.AUDIO:
        wav = await to_wav(data)
        if wav is None:
            log.warning("ffmpeg could not read a voice note; sending it as it came")
            return AudioPart(data_url(media.mime, data))
        return AudioPart(data_url("audio/wav", wav))
    raise AppError("unsupported_media", "This file cannot be read by the model")
