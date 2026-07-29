"""Admin / maintenance endpoints: reset DB, sync status, backfill thumbnails/crops."""
from __future__ import annotations

import logging

from fastapi import APIRouter
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
async def backfill_thumbnails(db: DB) -> dict:
    """Generate DB thumbnails for every photo that doesn't have them yet.

    Runs as a cancellable background task over the full backlog. Progress via /sync/tasks.
    """
    import asyncio
    from fernkam.task_manager import task_manager
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
        """),
    )).fetchall()
    if not rows:
        return {"task_id": None, "queued": 0, "message": "No photos missing thumbnails"}

    task_id = await task_manager.create_task(
        "backfill_thumbnails", f"Queued {len(rows):,} photos for thumbnail backfill…"
    )

    async def run_backfill() -> None:
        import time as _time
        from fernkam.db.session import async_session_factory
        from fernkam.thumbnails import generate_thumbnail_bytes, store_thumbnail_to_db, photo_disk_path

        t_start = _time.time()
        total = len(rows)
        ok = errors = 0
        loop = asyncio.get_event_loop()
        BATCH = 50

        async with async_session_factory() as bdb:
            for batch_start in range(0, total, BATCH):
                task = await task_manager.get_task(task_id)
                if task and task.status == "cancelled":
                    break
                for row in rows[batch_start: batch_start + BATCH]:
                    src = photo_disk_path(row.album_path, row.filename)
                    try:
                        for size in ("sm", "md", "lg", "xl"):
                            data = await loop.run_in_executor(None, generate_thumbnail_bytes, src, size)
                            if data:
                                await store_thumbnail_to_db(row.id, size, data, bdb)
                        ok += 1
                    except Exception as exc:
                        logger.warning("backfill thumb error photo %d: %s", row.id, exc)
                        errors += 1
                await bdb.commit()
                done = min(batch_start + BATCH, total)
                elapsed = _time.time() - t_start
                rate = done / elapsed if elapsed > 0 else 0
                eta = int((total - done) / rate) if rate > 0 else 0
                await task_manager.update_task(
                    task_id,
                    message=f"Backfilling thumbnails… {done:,}/{total:,} (✓ {ok} ✗ {errors} · ETA {eta}s)",
                    progress={"done": done, "total": total, "ok": ok, "errors": errors},
                )

        t_total = _time.time() - t_start
        await task_manager.update_task(
            task_id, status="completed",
            message=f"Done: {ok:,} thumbnailed, {errors} errors ({t_total:.0f}s)",
            progress={"done": total, "total": total, "ok": ok, "errors": errors},
        )

    asyncio.create_task(run_backfill(), name=f"fernkam-backfill-thumbs-{task_id}")
    return {"task_id": task_id, "queued": len(rows), "message": "running"}


@router.post("/backfill-crops")
async def backfill_crops(db: DB) -> dict:
    """Generate DB face crops for every face that doesn't have crop_data yet.

    Runs as a cancellable background task over the full backlog. Progress via /sync/tasks.
    """
    import asyncio
    from fernkam.task_manager import task_manager

    rows = (await db.execute(
        select(Face.id, Face.photo_id, Face.x, Face.y, Face.w, Face.h)
        .where(Face.crop_data.is_(None))
        .where(Face.x.isnot(None))
    )).fetchall()
    if not rows:
        return {"task_id": None, "queued": 0, "message": "No faces missing crops"}

    task_id = await task_manager.create_task(
        "backfill_crops", f"Queued {len(rows):,} faces for crop backfill…"
    )

    async def run_backfill() -> None:
        import time as _time
        import cv2
        from fernkam.db.session import async_session_factory
        from fernkam.thumbnails import photo_disk_path

        t_start = _time.time()
        total = len(rows)
        ok = errors = 0
        # Cache only the two fields actually used (album_path, filename), not
        # full Photo ORM entities — this backlog can span tens of thousands of
        # distinct photos, held for the whole (potentially hours-long) run.
        photo_cache: dict[int, tuple[str, str]] = {}
        BATCH = 50
        loop = asyncio.get_event_loop()

        def _make_crop(src_path, x, y, w, h) -> bytes | None:
            img = cv2.imread(str(src_path))
            if img is None:
                return None
            h_img, w_img = img.shape[:2]
            pad = int(max(w, h) * 0.2)
            x1, y1 = max(0, x - pad), max(0, y - pad)
            x2, y2 = min(w_img, x + w + pad), min(h_img, y + h + pad)
            crop = cv2.resize(img[y1:y2, x1:x2], (200, 200), interpolation=cv2.INTER_AREA)
            enc_ok, buf = cv2.imencode(".webp", crop, [cv2.IMWRITE_WEBP_QUALITY, 85])
            return bytes(buf) if enc_ok else None

        async with async_session_factory() as bdb:
            for batch_start in range(0, total, BATCH):
                task = await task_manager.get_task(task_id)
                if task and task.status == "cancelled":
                    break
                for row in rows[batch_start: batch_start + BATCH]:
                    try:
                        if row.photo_id not in photo_cache:
                            photo = (await bdb.execute(
                                select(Photo.album_path, Photo.filename).where(Photo.id == row.photo_id)
                            )).first()
                            if not photo:
                                continue
                            photo_cache[row.photo_id] = (photo.album_path, photo.filename)
                        album_path, filename = photo_cache[row.photo_id]
                        src = photo_disk_path(album_path, filename)
                        crop_bytes = await loop.run_in_executor(None, _make_crop, src, row.x, row.y, row.w, row.h)
                        if crop_bytes:
                            await bdb.execute(update(Face).where(Face.id == row.id).values(crop_data=crop_bytes))
                            ok += 1
                    except Exception as exc:
                        logger.warning("backfill crop error face %s: %s", row.id, exc)
                        errors += 1
                await bdb.commit()
                done = min(batch_start + BATCH, total)
                elapsed = _time.time() - t_start
                rate = done / elapsed if elapsed > 0 else 0
                eta = int((total - done) / rate) if rate > 0 else 0
                await task_manager.update_task(
                    task_id,
                    message=f"Backfilling face crops… {done:,}/{total:,} (✓ {ok} ✗ {errors} · ETA {eta}s)",
                    progress={"done": done, "total": total, "ok": ok, "errors": errors},
                )

        t_total = _time.time() - t_start
        await task_manager.update_task(
            task_id, status="completed",
            message=f"Done: {ok:,} crops generated, {errors} errors ({t_total:.0f}s)",
            progress={"done": total, "total": total, "ok": ok, "errors": errors},
        )

    asyncio.create_task(run_backfill(), name=f"fernkam-backfill-crops-{task_id}")
    return {"task_id": task_id, "queued": len(rows), "message": "running"}
