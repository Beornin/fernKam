from __future__ import annotations

import asyncio
import io
import subprocess
from pathlib import Path
from typing import AsyncIterator, Literal
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import FileResponse, Response, StreamingResponse
from sqlalchemy import select

from fernkam.api.deps import DB
from fernkam.db.models.photos import Face, Photo
from fernkam.media_types import MIME_MAP, RAW_EXTENSIONS, VIDEO_EXTENSIONS
from fernkam.thumbnails import (
    generate_thumbnail_bytes,
    get_thumbnail_from_db,
    store_thumbnail_to_db,
    photo_disk_path,
)

router = APIRouter()


async def _get_photo(photo_id: int, db: DB) -> Photo:
    row = (await db.execute(select(Photo).where(Photo.id == photo_id))).scalar_one_or_none()
    if not row:
        raise HTTPException(404, "Photo not found")
    return row


@router.get("/thumbnail/{photo_id}")
async def serve_thumbnail(
    photo_id: int,
    db: DB,
    size: Literal["sm", "md", "lg", "xl", "xxl"] = Query("md"),
) -> Response:
    # 1. Try DB cache
    data = await get_thumbnail_from_db(photo_id, size, db)
    if data:
        return Response(content=data, media_type="image/webp",
                        headers={"Cache-Control": "public, max-age=86400"})

    # 2. Fallback: generate from disk and cache to DB
    photo = await _get_photo(photo_id, db)
    src = photo_disk_path(photo.album_path, photo.filename)
    loop = asyncio.get_event_loop()
    data = await loop.run_in_executor(None, generate_thumbnail_bytes, src, size)
    if not data:
        raise HTTPException(422, "Thumbnail unavailable (video or missing source)")
    await store_thumbnail_to_db(photo_id, size, data, db)
    await db.commit()
    return Response(content=data, media_type="image/webp",
                    headers={"Cache-Control": "public, max-age=86400"})


STORED_CROP_SIZE = 200  # matches the fixed size backfill_crops()/auto-generation store at


def _generate_face_crop(src: Path, x: int, y: int, w: int, h: int, size: int, pad: int) -> bytes | None:
    """Blocking: decode the source photo, crop/resize/encode. Call via run_in_executor."""
    import cv2

    try:
        img = cv2.imread(str(src))
    except Exception:
        img = None
    if img is None:
        try:
            from PIL import Image
            import numpy as np
            pil_img = Image.open(src)
            if pil_img.mode != "RGB":
                pil_img = pil_img.convert("RGB")
            img = cv2.cvtColor(np.array(pil_img), cv2.COLOR_RGB2BGR)
        except Exception:
            return None

    h_img, w_img = img.shape[:2]
    x1 = max(0, x - pad)
    y1 = max(0, y - pad)
    x2 = min(w_img, x + w + pad)
    y2 = min(h_img, y + h + pad)
    crop = img[y1:y2, x1:x2]
    crop = cv2.resize(crop, (size, size), interpolation=cv2.INTER_AREA)
    ok, buf = cv2.imencode(".webp", crop, [cv2.IMWRITE_WEBP_QUALITY, 90 if size > STORED_CROP_SIZE else 85])
    return bytes(buf) if ok else None


@router.get("/face/{face_id}")
async def serve_face_crop(face_id: UUID, db: DB, size: int = Query(120, ge=40, le=800)) -> Response:
    """Return a square-cropped thumbnail of a face region.

    Requests at or below the stored resolution use the cached crop_data (fast,
    no disk read). Larger requests — the face review close-up view — always
    regenerate from the source photo with a little padding around the tight
    bounding box, since upscaling the small cached crop would just look soft.
    """
    row = (await db.execute(
        select(Face).where(Face.id == face_id)
    )).scalar_one_or_none()
    if not row:
        raise HTTPException(404, "Face not found")

    # 1. Serve from stored crop if available and the request doesn't need more detail
    if row.crop_data and size <= STORED_CROP_SIZE:
        return Response(content=bytes(row.crop_data), media_type="image/webp",
                        headers={"Cache-Control": "public, max-age=86400"})

    # 2. Generate from disk (fallback for uncached crops, or a larger close-up request)
    if row.x is None:
        raise HTTPException(422, "Face has no bounding box")

    photo = (await db.execute(
        select(Photo).where(Photo.id == row.photo_id)
    )).scalar_one_or_none()
    if not photo:
        raise HTTPException(404, "Photo not found")

    src = photo_disk_path(photo.album_path, photo.filename)
    if not src.exists():
        raise HTTPException(404, "Source file not found")

    fw, fh = row.w or 0, row.h or 0
    # A little padding for larger requests so a close-up isn't an ultra-tight
    # crop of just the face — matches the padding backfill_crops() already
    # uses for the stored 200px version.
    pad = int(max(fw, fh) * 0.2) if size > STORED_CROP_SIZE else 0

    # Decode/crop/encode is CPU-bound (a full-resolution photo, up to a 60 MP
    # TIFF, gets decoded) — run off the event loop so one close-up request
    # doesn't stall every other in-flight request.
    loop = asyncio.get_event_loop()
    crop_bytes = await loop.run_in_executor(
        None, _generate_face_crop, src, row.x or 0, row.y or 0, fw, fh, size, pad
    )
    if crop_bytes is None:
        raise HTTPException(422, "Could not read image")

    # Only cache the standard small size — an oversized close-up crop would
    # clobber the cache every grid tile elsewhere on the site relies on.
    if size <= STORED_CROP_SIZE:
        from sqlalchemy import update as sa_update
        await db.execute(sa_update(Face).where(Face.id == face_id).values(crop_data=crop_bytes))
        await db.commit()

    return Response(content=crop_bytes, media_type="image/webp",
                    headers={"Cache-Control": "public, max-age=86400"})


@router.get("/original/{photo_id}", response_model=None)
async def serve_original(photo_id: int, db: DB) -> FileResponse | Response:
    photo = await _get_photo(photo_id, db)
    src = photo_disk_path(photo.album_path, photo.filename)
    if not src.exists():
        raise HTTPException(404, "Source file not found on disk")
    ext = src.suffix.lower()

    if ext in RAW_EXTENSIONS:
        from fastapi.responses import RedirectResponse
        return RedirectResponse(url=f"/media/raw-preview/{photo_id}", status_code=307)

    # Browsers cannot render TIFF — transcode to JPEG on-the-fly.
    if ext in (".tif", ".tiff"):
        try:
            from PIL import Image, ImageOps
            from io import BytesIO
            Image.MAX_IMAGE_PIXELS = None
            loop = asyncio.get_event_loop()
            def _tif_to_jpeg() -> bytes:
                pil_img = Image.open(src)
                pil_img = ImageOps.exif_transpose(pil_img)
                if pil_img.mode not in ("RGB", "L"):
                    pil_img = pil_img.convert("RGB")
                buf = BytesIO()
                pil_img.save(buf, "JPEG", quality=92)
                return buf.getvalue()
            data = await loop.run_in_executor(None, _tif_to_jpeg)
            return Response(
                content=data,
                media_type="image/jpeg",
                headers={"Cache-Control": "private, max-age=3600"},
            )
        except Exception as exc:
            raise HTTPException(422, f"Cannot decode TIFF: {exc}")

    mime = MIME_MAP.get(ext, "application/octet-stream")
    
    # For videos, serve without filename to avoid Content-Disposition: attachment
    if ext in VIDEO_EXTENSIONS:
        return FileResponse(
            src,
            media_type=mime,
            headers={"Cache-Control": "private, max-age=3600"},
        )
    
    return FileResponse(
        src,
        media_type=mime,
        filename=photo.filename,
        headers={"Cache-Control": "private, max-age=3600"},
    )


async def _stream_transcode(src: Path, ffmpeg_path: str) -> AsyncIterator[bytes]:
    """Stream video transcoded to H.264/AAC via ffmpeg for browser compatibility."""
    cmd = [
        ffmpeg_path,
        "-i", str(src),
        "-vcodec", "libx264",
        "-acodec", "aac",
        "-preset", "fast",
        "-crf", "23",
        "-movflags", "frag_keyframe+empty_moov+faststart",
        "-f", "mp4",
        "pipe:1",
    ]
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.DEVNULL,
    )
    assert proc.stdout is not None
    try:
        while True:
            chunk = await proc.stdout.read(65536)
            if not chunk:
                break
            yield chunk
    finally:
        try:
            proc.kill()
        except Exception:
            pass
        await proc.wait()


@router.get("/video/{photo_id}")
async def serve_video_transcoded(photo_id: int, db: DB) -> StreamingResponse:
    """Serve video transcoded to H.264/AAC for browser compatibility."""
    photo = await _get_photo(photo_id, db)
    src = photo_disk_path(photo.album_path, photo.filename)
    if not src.exists():
        raise HTTPException(404, "Source file not found on disk")
    ext = src.suffix.lower()
    if ext not in VIDEO_EXTENSIONS:
        raise HTTPException(400, "Not a video file")
    from fernkam.thumbnails import _resolve_ffmpeg
    ffmpeg = _resolve_ffmpeg()
    if not ffmpeg:
        raise HTTPException(503, "ffmpeg not available — install it or set FERNKAM_FFMPEG_PATH")
    return StreamingResponse(
        _stream_transcode(src, ffmpeg),
        media_type="video/mp4",
        headers={
            "Cache-Control": "no-cache",
            "Content-Disposition": "inline",
            "X-Content-Type-Options": "nosniff",
        },
    )
