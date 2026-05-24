from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Literal

from PIL import Image, ImageOps

from fernkam.config import get_settings

SIZES: dict[str, tuple[int, int]] = {
    "sm": (240, 240),
    "md": (480, 480),
    "lg": (960, 960),
}

VIDEO_EXTENSIONS = {".mp4", ".mov", ".avi", ".mkv", ".m4v", ".mts", ".mpg", ".mpeg", ".wmv"}


def photo_disk_path(album_path: str, filename: str) -> Path:
    """Resolve full path from DB album_path + filename."""
    settings = get_settings()
    library = Path(settings.library_root)
    # album_path starts with /Pictures and Videos/... strip leading slash
    rel = album_path.lstrip("/")
    return library.parent / rel / filename


def thumb_cache_path(photo_id: int, size: str) -> Path:
    settings = get_settings()
    cache = Path(settings.thumb_cache_dir)
    bucket = f"{photo_id % 1000:03d}"
    return cache / bucket / f"{photo_id}_{size}.webp"


def get_or_create_thumbnail(
    photo_id: int,
    album_path: str,
    filename: str,
    size: Literal["sm", "md", "lg"] = "md",
) -> Path | None:
    """Return path to cached thumbnail, generating it if needed.
    Returns None if source file not found or is a video (no thumbnail yet).
    """
    dest = thumb_cache_path(photo_id, size)
    if dest.exists():
        return dest

    src = photo_disk_path(album_path, filename)
    if not src.exists():
        return None

    ext = src.suffix.lower()
    if ext in VIDEO_EXTENSIONS:
        return None

    dest.parent.mkdir(parents=True, exist_ok=True)

    try:
        with Image.open(src) as img:
            img = ImageOps.exif_transpose(img)
            img.thumbnail(SIZES[size], Image.LANCZOS)
            if img.mode not in ("RGB", "L"):
                img = img.convert("RGB")
            img.save(dest, "WEBP", quality=82, method=4)
        return dest
    except Exception:
        return None
