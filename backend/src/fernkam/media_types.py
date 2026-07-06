"""Single source of truth for file-extension classification across fernKam.

Every module that needs to know whether a file is a RAW/picture/video, or
what MIME type to serve it as, imports from here. Do NOT redefine extension
sets locally — that's how ``.cr3`` silently fell through the importer while
the thumbnailer already supported it.
"""
from __future__ import annotations

from pathlib import Path
from typing import Union

# RAW camera formats (need rawpy/dcraw to decode a preview).
RAW_EXTENSIONS: frozenset[str] = frozenset({
    ".nef", ".cr2", ".cr3", ".arw", ".orf", ".raf",
    ".rw2", ".dng", ".pef", ".srw", ".x3f", ".3fr",
})

# Regular (non-RAW) picture/image formats.
PICTURE_EXTENSIONS: frozenset[str] = frozenset({
    ".jpg", ".jpeg", ".png", ".gif", ".bmp",
    ".tif", ".tiff", ".webp", ".heic", ".heif",
})

# All still-image formats (pictures + RAW).
IMAGE_EXTENSIONS: frozenset[str] = PICTURE_EXTENSIONS | RAW_EXTENSIONS

# Video formats. Reconciled superset of every set previously duplicated
# across importers/filesystem.py, thumbnails.py, api/routers/media.py and
# workflows/shared.py. Note: `.vlc` was removed — it is not a video file
# extension (it was a bug in the old workflows/shared.py set).
VIDEO_EXTENSIONS: frozenset[str] = frozenset({
    ".mp4", ".mov", ".avi", ".mkv", ".m4v", ".mts",
    ".mpg", ".mpeg", ".wmv", ".webm", ".flv", ".3gp",
})

ALL_EXTENSIONS: frozenset[str] = IMAGE_EXTENSIONS | VIDEO_EXTENSIONS

MIME_MAP: dict[str, str] = {
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
    ".gif": "image/gif",
    ".webp": "image/webp",
    ".tif": "image/tiff",
    ".tiff": "image/tiff",
    ".heic": "image/heic",
    ".heif": "image/heif",
    ".bmp": "image/bmp",
    ".mp4": "video/mp4",
    ".mov": "video/quicktime",
    ".avi": "video/x-msvideo",
    ".mkv": "video/x-matroska",
    ".m4v": "video/mp4",
    ".wmv": "video/x-ms-wmv",
    ".mts": "video/mp2t",
    ".mpg": "video/mpeg",
    ".mpeg": "video/mpeg",
    ".webm": "video/webm",
    ".flv": "video/x-flv",
    ".3gp": "video/3gpp",
    # RAW files have no universally-supported browser MIME type; callers
    # generally redirect RAW requests to a generated JPEG preview instead.
    ".nef": "image/x-nikon-nef",
    ".cr2": "image/x-canon-cr2",
    ".cr3": "image/x-canon-cr3",
    ".arw": "image/x-sony-arw",
    ".orf": "image/x-olympus-orf",
    ".raf": "image/x-fuji-raf",
    ".rw2": "image/x-panasonic-rw2",
    ".dng": "image/x-adobe-dng",
    ".pef": "image/x-pentax-pef",
    ".srw": "image/x-samsung-srw",
    ".x3f": "image/x-sigma-x3f",
    ".3fr": "image/x-hasselblad-3fr",
}


def _ext(name: Union[str, Path]) -> str:
    return Path(name).suffix.lower()


def is_raw(name: Union[str, Path]) -> bool:
    return _ext(name) in RAW_EXTENSIONS


def is_picture(name: Union[str, Path]) -> bool:
    return _ext(name) in PICTURE_EXTENSIONS


def is_image(name: Union[str, Path]) -> bool:
    return _ext(name) in IMAGE_EXTENSIONS


def is_video(name: Union[str, Path]) -> bool:
    return _ext(name) in VIDEO_EXTENSIONS


def media_type_for(name: Union[str, Path]) -> str:
    """Return 'video' or 'image' (default) for the given filename."""
    return "video" if is_video(name) else "image"


def mime_for(name: Union[str, Path]) -> str:
    return MIME_MAP.get(_ext(name), "application/octet-stream")
