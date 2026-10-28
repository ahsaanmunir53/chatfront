"""Stills from a video with ffmpeg: a product's photo is the frame where the model saw it
(feature 2). Cut-outs need the Meta Model API, so the whole frame is kept for now."""

from __future__ import annotations

import asyncio
import shutil
import tempfile
from collections.abc import AsyncIterator, Sequence
from contextlib import asynccontextmanager
from pathlib import Path

TIMEOUT_SECONDS = 20
END_MARGIN = 0.2  # seconds kept clear of the end, where a frame may not decode


async def _run(*args: str) -> bytes | None:
    """A tool's output, or None when it is missing, fails or takes too long."""
    if shutil.which(args[0]) is None:
        return None
    process = await asyncio.create_subprocess_exec(
        *args, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
    )
    try:
        out, _ = await asyncio.wait_for(process.communicate(), TIMEOUT_SECONDS)
    except TimeoutError:
        process.kill()
        return None
    return out if process.returncode == 0 and out else None


@asynccontextmanager
async def saved(data: bytes) -> AsyncIterator[Path]:
    """The video as a file for ffmpeg to seek in, removed afterwards."""
    with tempfile.TemporaryDirectory(prefix="chatfront-video-") as folder:
        path = Path(folder) / "video"
        path.write_bytes(data)
        yield path


async def duration(path: Path) -> float | None:
    out = await _run(
        "ffprobe",
        "-v",
        "error",
        "-show_entries",
        "format=duration",
        "-of",
        "default=noprint_wrappers=1:nokey=1",
        str(path),
    )
    try:
        return float(out.decode().strip()) if out else None
    except ValueError:
        return None


async def frame_at(path: Path, second: float) -> bytes | None:
    """A JPEG of the frame at this second."""
    return await _run(
        "ffmpeg",
        "-hide_banner",
        "-loglevel",
        "error",
        "-ss",
        f"{max(second, 0):.2f}",
        "-i",
        str(path),
        "-frames:v",
        "1",
        "-q:v",
        "3",
        "-f",
        "image2",
        "-c:v",
        "mjpeg",
        "pipe:1",
    )


async def frames(data: bytes, seconds: Sequence[float]) -> list[bytes | None]:
    """A still for each moment, kept inside the video's length."""
    async with saved(data) as path:
        length = await duration(path)
        last = max(length - END_MARGIN, 0) if length is not None else None
        return [
            await frame_at(path, min(second, last) if last is not None else second)
            for second in seconds
        ]
