from __future__ import annotations

from pathlib import Path
from typing import Literal

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import FileResponse
from sqlalchemy import select

from fernkam.api.deps import DB
from fernkam.db.models.photos import Photo
from fernkam.thumbnails import get_or_create_thumbnail, photo_disk_path

router = APIRouter()

MIME_MAP = {
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
}


async def _get_photo(photo_id: int, db: DB) -> Photo:
    row = (await db.execute(select(Photo).where(Photo.id == photo_id))).scalar_one_or_none()
    if not row:
        raise HTTPException(404, "Photo not found")
    return row


@router.get("/thumbnail/{photo_id}")
async def serve_thumbnail(
    photo_id: int,
    db: DB,
    size: Literal["sm", "md", "lg"] = Query("md"),
) -> FileResponse:
    photo = await _get_photo(photo_id, db)
    thumb = get_or_create_thumbnail(photo.id, photo.album_path, photo.filename, size)
    if not thumb:
        raise HTTPException(404, "Thumbnail unavailable (video or missing source)")
    return FileResponse(thumb, media_type="image/webp", headers={"Cache-Control": "public, max-age=86400"})


@router.get("/original/{photo_id}")
async def serve_original(photo_id: int, db: DB) -> FileResponse:
    photo = await _get_photo(photo_id, db)
    src = photo_disk_path(photo.album_path, photo.filename)
    if not src.exists():
        raise HTTPException(404, "Source file not found on disk")
    ext = src.suffix.lower()
    mime = MIME_MAP.get(ext, "application/octet-stream")
    return FileResponse(
        src,
        media_type=mime,
        filename=photo.filename,
        headers={"Cache-Control": "private, max-age=3600"},
    )
