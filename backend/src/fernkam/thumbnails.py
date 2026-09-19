from __future__ import annotations

import os
import subprocess
import tempfile
import uuid
from collections.abc import Iterable
from pathlib import Path
from typing import Literal

from PIL import Image, ImageOps

Image.MAX_IMAGE_PIXELS = None

from fernkam.config import get_settings
from fernkam.media_types import RAW_EXTENSIONS, VIDEO_EXTENSIONS  # noqa: F401 (re-exported for existing importers)

SIZES: dict[str, tuple[int, int]] = {
    "sm": (240, 240),
    "md": (480, 480),
    "lg": (960, 960),
    "xl": (1440, 1440),
    "xxl": (1920, 1920),
}


def _open_raw_as_pil(src: Path) -> "Image.Image":
    """Open a RAW camera file as PIL Image via embedded JPEG preview (fast)."""
    import rawpy
    from io import BytesIO
    with rawpy.imread(str(src)) as raw:
        try:
            thumb = raw.extract_thumb()
            if thumb.format == rawpy.ThumbFormat.JPEG:
                return Image.open(BytesIO(bytes(thumb.data))).copy()
            else:
                import numpy as np
                return Image.fromarray(np.asarray(thumb.data, dtype="uint8"))
        except Exception:
            import numpy as np
            rgb = raw.postprocess()
            return Image.fromarray(rgb)


def photo_disk_path(album_path: str, filename: str) -> Path:
    """Resolve full path from DB album_path + filename."""
    settings = get_settings()
    library = Path(settings.library_root)
    # album_path is relative to library root (e.g. "/" or "Anniversaries/1st")
    rel = album_path.strip("/")
    if rel:
        return library / rel / filename
    return library / filename


def thumb_cache_path(photo_id: int, size: str) -> Path:
    settings = get_settings()
    cache = Path(settings.thumb_cache_dir)
    bucket = f"{photo_id % 1000:03d}"
    return cache / bucket / f"{photo_id}_{size}.webp"


# ── Disk-backed thumbnail cache ────────────────────────────────────────────
# Thumbnails used to live in a `photo_thumbnails` bytea column, which grew to
# 32 GB of a 34 GB database — 94% of it a regenerable cache. The cost was not
# read latency (Postgres served these in ~0.5-0.8 ms) but connection pressure:
# every thumbnail held one of the 50 pooled connections, so changing the grid's
# thumbnail size made ~42 tiles contend with the catalogue query populating that
# same grid. Measured, the catalogue query went 0.67 ms idle -> 19.19 ms under
# that burst, and 2.55 ms once thumbnails were served from disk instead.
#
# These are blocking; call them via run_in_executor.

def read_thumbnail_from_disk(photo_id: int, size: str) -> bytes | None:
    """Return cached thumbnail bytes, or None if not cached yet."""
    try:
        return thumb_cache_path(photo_id, size).read_bytes()
    except OSError:
        return None


def write_thumbnail_to_disk(photo_id: int, size: str, data: bytes) -> None:
    """Cache thumbnail bytes to disk, atomically.

    Written to a unique temp name and renamed into place so a concurrent
    reader never sees a half-written file.
    """
    dest = thumb_cache_path(photo_id, size)
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_name(f"{dest.name}.{uuid.uuid4().hex}.tmp")
    try:
        tmp.write_bytes(data)
        os.replace(tmp, dest)
    except OSError:
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass


def delete_thumbnails_from_disk(photo_ids: "Iterable[int]") -> int:
    """Remove every cached size for the given photos. Returns files removed.

    The old bytea column was cleaned up by ON DELETE CASCADE; on disk the
    deleting code path has to say so explicitly.
    """
    removed = 0
    for photo_id in photo_ids:
        for size in SIZES:
            try:
                thumb_cache_path(photo_id, size).unlink(missing_ok=True)
                removed += 1
            except OSError:
                pass
    return removed


def generate_thumbnail_bytes(
    src: Path,
    size: Literal["sm", "md", "lg", "xl", "xxl"] = "md",
) -> bytes | None:
    """Generate a WebP thumbnail for the given source path and return raw bytes.

    Returns None if the file cannot be read (video without ffmpeg, missing file, etc.).
    """
    ext = src.suffix.lower()
    if ext in VIDEO_EXTENSIONS:
        # ffmpeg needs a real file to write to, and the result is read back and
        # deleted immediately. This used to use thumb_cache_path(0, size) — a
        # single fixed path shared by EVERY video, so concurrent thumbnail
        # workers (see THUMB_CONCURRENCY in importers/filesystem.py) raced on
        # it: one would unlink the file while another was reading it
        # ("No such file or directory"), or Windows would hold it open
        # ("Permission denied"). Both surfaced as 500s from /media/thumbnail.
        # A unique per-call temp path removes the shared state entirely.
        cache_dir = Path(get_settings().thumb_cache_dir) / "tmp"
        cache_dir.mkdir(parents=True, exist_ok=True)
        dest_path = cache_dir / f"vthumb_{uuid.uuid4().hex}_{size}.webp"
        tmp = _video_thumbnail(src, dest_path, size)
        try:
            if tmp and tmp.exists():
                return tmp.read_bytes()
            return None
        finally:
            for leftover in {dest_path, tmp} - {None}:
                try:
                    leftover.unlink(missing_ok=True)
                except (OSError, PermissionError):
                    pass

    try:
        if ext in RAW_EXTENSIONS:
            img = _open_raw_as_pil(src)
        else:
            img = Image.open(src)
        img = ImageOps.exif_transpose(img)
        img.thumbnail(SIZES[size], Image.LANCZOS)
        if img.mode not in ("RGB", "L"):
            img = img.convert("RGB")
        from io import BytesIO
        buf = BytesIO()
        img.save(buf, "WEBP", quality=82, method=4)
        return buf.getvalue()
    except Exception:
        return None


def _resolve_ffmpeg() -> str | None:
    """Return a usable ffmpeg executable path, or None if not found."""
    import shutil
    settings = get_settings()
    if settings.ffmpeg_path and Path(settings.ffmpeg_path).exists():
        return settings.ffmpeg_path
    # Fall back to ffmpeg on system PATH
    found = shutil.which("ffmpeg")
    return found  # None if not found anywhere


def probe_video_duration(src: Path) -> float | None:
    """Return video duration in seconds using ffprobe, or None on failure."""
    import shutil, json as _json
    ffmpeg = _resolve_ffmpeg()
    if not ffmpeg:
        return None
    ffprobe = str(Path(ffmpeg).with_name("ffprobe"))
    if not Path(ffprobe).exists():
        found = shutil.which("ffprobe")
        if not found:
            return None
        ffprobe = found
    try:
        result = subprocess.run(
            [
                ffprobe, "-v", "quiet", "-print_format", "json",
                "-show_streams", "-select_streams", "v:0", str(src),
            ],
            capture_output=True, timeout=15,
        )
        if result.returncode != 0:
            return None
        data = _json.loads(result.stdout)
        streams = data.get("streams", [])
        if streams and "duration" in streams[0]:
            return float(streams[0]["duration"])
        return None
    except Exception:
        return None


def _video_thumbnail(src: Path, dest: Path, size: str) -> Path | None:
    """Extract a frame at 10% into the video and save as WebP thumbnail."""
    ffmpeg = _resolve_ffmpeg()
    if not ffmpeg:
        return None

    max_dim = SIZES[size][0]
    with tempfile.NamedTemporaryFile(suffix=".jpg", delete=False) as tmp:
        tmp_path = Path(tmp.name)

    try:
        result = subprocess.run(
            [
                str(ffmpeg),
                "-ss", "00:00:01",
                "-i", str(src),
                "-vframes", "1",
                "-vf", f"scale={max_dim}:{max_dim}:force_original_aspect_ratio=decrease",
                "-q:v", "3",
                "-y",
                str(tmp_path),
            ],
            capture_output=True,
            timeout=30,
        )
        if result.returncode != 0 or not tmp_path.exists():
            return None

        with Image.open(tmp_path) as img:
            img = img.convert("RGB")
            img.save(dest, "WEBP", quality=82, method=4)
        return dest
    except Exception:
        return None
    finally:
        tmp_path.unlink(missing_ok=True)
