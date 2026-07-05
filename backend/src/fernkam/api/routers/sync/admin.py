"""Admin / maintenance endpoints: reset DB, sync status, backfill thumbnails/crops."""
from __future__ import annotations

import logging

from fastapi import APIRouter, Query
from sqlalchemy import func, select, update

from fernkam.api.deps import DB
from fernkam.db.models.photos import Face, Photo

logger = logging.getLogger(__name__)
router = APIRouter()


@router.post("/reset-db")
async def reset_db(db: DB) -> dict:
    """Truncate all application tables and restart all sequences.

    Irreversible. Intended for development / fresh-start workflows.
    """
    from sqlalchemy import text as _sql

    tables = [
        "faces", "photo_tags", "photos",
        "tags", "cameras", "lenses", "people",
        "audit_log", "app_logs",
    ]
    truncate_sql = "TRUNCATE TABLE {} RESTART IDENTITY CASCADE".format(
        ", ".join(tables)
    )
    await db.execute(_sql(truncate_sql))
    await db.commit()
    logger.warning("[RESET-DB] All tables truncated by user request")
    return {"ok": True, "tables_cleared": tables}


@router.get("/status")
async def sync_status(db: DB) -> dict:
    """Summary of sync state — pure DB, no disk access."""
    dirty = (await db.execute(
        select(func.count()).select_from(Photo)
        .where(Photo.file_sync_dirty == True)  # noqa: E712
    )).scalar_one()
    never_synced = (await db.execute(
        select(func.count()).select_from(Photo)
        .where(Photo.meta_synced_at.is_(None))
        .where(Photo.status == 1)
    )).scalar_one()
    last_sync = (await db.execute(
        select(func.max(Photo.meta_synced_at)).select_from(Photo)
    )).scalar_one()
    return {
        "dirty_count": dirty,
        "never_synced_count": never_synced,
        "last_sync": last_sync.isoformat() if last_sync else None,
    }


@router.post("/backfill-thumbnails")
async def backfill_thumbnails(db: DB, limit: int = Query(500)) -> dict:
    """Generate DB thumbnails for photos that don't have them yet."""
    import asyncio
    from fernkam.thumbnails import generate_thumbnail_bytes, store_thumbnail_to_db, photo_disk_path
    from sqlalchemy import text

    rows = (await db.execute(
        text("""
            SELECT p.id, p.album_path, p.filename
            FROM photos p
            WHERE p.status = 1
              AND p.media_type = 'image'
              AND NOT EXISTS (
                  SELECT 1 FROM photo_thumbnails t WHERE t.photo_id = p.id
              )
            LIMIT :lim
        """),
        {"lim": limit},
    )).fetchall()

    ok = errors = 0
    loop = asyncio.get_event_loop()
    for row in rows:
        src = photo_disk_path(row.album_path, row.filename)
        try:
            for size in ("sm", "md", "lg", "xl"):
                data = await loop.run_in_executor(None, generate_thumbnail_bytes, src, size)
                if data:
                    await store_thumbnail_to_db(row.id, size, data, db)
            await db.commit()
            ok += 1
        except Exception as exc:
            logger.warning("backfill thumb error photo %d: %s", row.id, exc)
            errors += 1

    return {"processed": ok, "errors": errors, "remaining": max(0, len(rows) - ok)}


@router.post("/backfill-crops")
async def backfill_crops(db: DB, limit: int = Query(500)) -> dict:
    """Generate DB face crops for faces that don't have crop_data yet."""
    import asyncio
    import cv2
    from fernkam.thumbnails import photo_disk_path

    rows = (await db.execute(
        select(Face.id, Face.photo_id, Face.x, Face.y, Face.w, Face.h)
        .where(Face.crop_data.is_(None))
        .where(Face.x.isnot(None))
        .limit(limit)
    )).fetchall()

    photo_cache: dict[int, Photo] = {}
    ok = errors = 0

    for row in rows:
        try:
            if row.photo_id not in photo_cache:
                photo = (await db.execute(select(Photo).where(Photo.id == row.photo_id))).scalar_one_or_none()
                if not photo:
                    continue
                photo_cache[row.photo_id] = photo
            photo = photo_cache[row.photo_id]
            src = photo_disk_path(photo.album_path, photo.filename)
            img = cv2.imread(str(src))
            if img is None:
                continue
            h_img, w_img = img.shape[:2]
            pad = int(max(row.w, row.h) * 0.2)
            x1 = max(0, row.x - pad)
            y1 = max(0, row.y - pad)
            x2 = min(w_img, row.x + row.w + pad)
            y2 = min(h_img, row.y + row.h + pad)
            crop = cv2.resize(img[y1:y2, x1:x2], (200, 200), interpolation=cv2.INTER_AREA)
            enc_ok, buf = cv2.imencode(".webp", crop, [cv2.IMWRITE_WEBP_QUALITY, 85])
            if enc_ok:
                await db.execute(update(Face).where(Face.id == row.id).values(crop_data=bytes(buf)))
                ok += 1
        except Exception as exc:
            logger.warning("backfill crop error face %s: %s", row.id, exc)
            errors += 1

    await db.commit()
    return {"processed": ok, "errors": errors}
