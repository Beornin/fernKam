from __future__ import annotations

import subprocess
import tempfile
from pathlib import Path
from typing import Literal

from PIL import Image, ImageOps

Image.MAX_IMAGE_PIXELS = None

from fernkam.config import get_settings

SIZES: dict[str, tuple[int, int]] = {
    "sm": (240, 240),
    "md": (480, 480),
    "lg": (960, 960),
    "xl": (1440, 1440),
    "xxl": (1920, 1920),
}

VIDEO_EXTENSIONS = {".mp4", ".mov", ".avi", ".mkv", ".m4v", ".mts", ".mpg", ".mpeg", ".wmv"}

RAW_EXTENSIONS = {
    ".nef", ".cr2", ".cr3", ".arw", ".orf", ".raf",
    ".rw2", ".dng", ".pef", ".srw", ".x3f", ".3fr",
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


def generate_thumbnail_bytes(
    src: Path,
    size: Literal["sm", "md", "lg", "xl", "xxl"] = "md",
) -> bytes | None:
    """Generate a WebP thumbnail for the given source path and return raw bytes.

    Returns None if the file cannot be read (video without ffmpeg, missing file, etc.).
    """
    ext = src.suffix.lower()
    if ext in VIDEO_EXTENSIONS:
        dest_path = thumb_cache_path(0, size)
        dest_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = _video_thumbnail(src, dest_path, size)
        if tmp and tmp.exists():
            data = tmp.read_bytes()
            try:
                tmp.unlink(missing_ok=True)
            except (OSError, PermissionError):
                pass
            return data
        return None

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


async def get_thumbnail_from_db(photo_id: int, size: str, db) -> bytes | None:
    """Fetch thumbnail bytes from photo_thumbnails table. Returns None if not found."""
    from sqlalchemy import text
    row = (await db.execute(
        text("SELECT data FROM photo_thumbnails WHERE photo_id = :pid AND size = :sz"),
        {"pid": photo_id, "sz": size},
    )).fetchone()
    return bytes(row[0]) if row else None


async def store_thumbnail_to_db(photo_id: int, size: str, data: bytes, db) -> None:
    """Insert or replace thumbnail bytes in photo_thumbnails table."""
    from sqlalchemy import text
    await db.execute(
        text("""
            INSERT INTO photo_thumbnails (photo_id, size, data)
            VALUES (:pid, :sz, :data)
            ON CONFLICT (photo_id, size) DO UPDATE SET data = EXCLUDED.data, created_at = now()
        """),
        {"pid": photo_id, "sz": size, "data": data},
    )


def get_or_create_thumbnail(
    photo_id: int,
    album_path: str,
    filename: str,
    size: Literal["sm", "md", "lg"] = "md",
) -> Path | None:
    """Return path to cached thumbnail, generating it if needed.
    Returns None if source file not found or is a video (no thumbnail yet).
    """
    import logging
    logger = logging.getLogger(__name__)
    
    dest = thumb_cache_path(photo_id, size)
    if dest.exists():
        logger.debug(f"Cache hit: {dest}")
        return dest

    logger.debug(f"Cache miss, generating thumbnail: photo_id={photo_id}, size={size}")
    src = photo_disk_path(album_path, filename)
    if not src.exists():
        logger.error(f"Source file not found: {src}")
        return None

    ext = src.suffix.lower()
    dest.parent.mkdir(parents=True, exist_ok=True)

    if ext in VIDEO_EXTENSIONS:
        logger.debug(f"Video thumbnail: {src}")
        return _video_thumbnail(src, dest, size)

    try:
        logger.debug(f"Opening image: {src}")
        if ext in RAW_EXTENSIONS:
            img = _open_raw_as_pil(src)
        else:
            img = Image.open(src)
        img = ImageOps.exif_transpose(img)
        img.thumbnail(SIZES[size], Image.LANCZOS)
        if img.mode not in ("RGB", "L"):
            img = img.convert("RGB")
        img.save(dest, "WEBP", quality=82, method=4)
        logger.debug(f"Thumbnail saved: {dest}")
        return dest
    except Exception as e:
        logger.error(f"Thumbnail generation failed for {src}: {e}", exc_info=True)
        return None


def _video_thumbnail(src: Path, dest: Path, size: str) -> Path | None:
    """Extract a frame at 10% into the video and save as WebP thumbnail."""
    settings = get_settings()
    ffmpeg = Path(settings.ffmpeg_path)
    if not ffmpeg.exists():
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
