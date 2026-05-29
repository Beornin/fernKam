"""Bidirectional metadata sync: DB ↔ image file XMP.

POST /api/sync/db-to-file   — write DB state → file XMP (tags, rating, faces)
POST /api/sync/file-to-db   — read file XMP → DB (pick up external edits)
GET  /api/sync/status       — photos whose file mtime is newer than last sync
POST /api/sync/scan-library — scan library root for new/updated photos
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Body, Query
from pydantic import BaseModel
from sqlalchemy import func, select, update
from sqlalchemy.orm import selectinload

from fernkam.api.deps import DB
from fernkam.db.models.photos import Face, Photo, PhotoTag, Tag

logger = logging.getLogger(__name__)
router = APIRouter()


class ScanLibraryRequest(BaseModel):
    custom_path: Optional[str] = None


@router.post("/db-to-file")
async def sync_db_to_file(
    db: DB,
    photo_ids: Optional[list[int]] = Body(None),
    album_path: Optional[str] = Body(None),
    limit: int = Body(100),
) -> dict:
    """Write DB metadata (tags, rating, caption, face regions) → image file XMP."""
    from fernkam.metadata_sync import sync_db_to_file as _sync

    q = (
        select(Photo)
        .where(Photo.status == 1)
        .options(
            selectinload(Photo.photo_tags).selectinload(PhotoTag.tag),
            selectinload(Photo.faces).selectinload(Face.person_tag),
        )
    )
    if photo_ids:
        q = q.where(Photo.id.in_(photo_ids))
    elif album_path:
        q = q.where(Photo.album_path.like(f"{album_path}%"))
    q = q.limit(limit)

    photos = (await db.execute(q)).scalars().all()
    ok = errors = 0
    for photo in photos:
        tags = [pt.tag for pt in photo.photo_tags if pt.tag]
        success = await _sync(photo, tags, list(photo.faces))
        if success:
            await db.execute(
                update(Photo).where(Photo.id == photo.id)
                .values(meta_synced_at=datetime.now(timezone.utc), file_sync_dirty=False)
            )
            ok += 1
        else:
            errors += 1

    await db.commit()
    return {"synced": ok, "errors": errors, "total": len(photos)}


@router.get("/write-status")
async def write_status(db: DB) -> dict:
    """Count of photos with pending DB→file sync (DB-only, no disk access)."""
    dirty = (await db.execute(
        select(func.count()).select_from(Photo)
        .where(Photo.file_sync_dirty == True)  # noqa: E712
    )).scalar_one()
    last_sync = (await db.execute(
        select(func.max(Photo.meta_synced_at)).select_from(Photo)
    )).scalar_one()
    return {"dirty_count": dirty, "last_sync": last_sync.isoformat() if last_sync else None}


@router.post("/file-to-db")
async def sync_file_to_db(
    db: DB,
    photo_ids: Optional[list[int]] = Body(None),
    album_path: Optional[str] = Body(None),
    limit: int = Body(100),
) -> dict:
    """Read XMP from image files and update DB (tags, rating, caption, color_label)."""
    from fernkam.metadata_sync import sync_file_to_db as _sync

    q = select(Photo).where(Photo.status == 1)
    if photo_ids:
        q = q.where(Photo.id.in_(photo_ids))
    elif album_path:
        q = q.where(Photo.album_path.like(f"{album_path}%"))
    q = q.limit(limit)

    photos = (await db.execute(q)).scalars().all()
    ok = tags_created = errors = 0

    for photo in photos:
        try:
            changes = await _sync(photo, db)
            if not changes:
                continue

            photo_updates: dict = {}
            for field in ("rating", "color_label", "title", "caption"):
                if field in changes:
                    photo_updates[field] = changes[field]
            if changes.get("file_mtime"):
                photo_updates["file_modified_at_sync"] = changes["file_mtime"]
            if photo_updates:
                await db.execute(update(Photo).where(Photo.id == photo.id).values(**photo_updates))

            if "tags" in changes:
                for tag_name in changes["tags"]:
                    if not tag_name.strip():
                        continue
                    tag = (await db.execute(
                        select(Tag).where(Tag.name == tag_name)
                    )).scalar_one_or_none()
                    if not tag:
                        tag = Tag(name=tag_name, path=tag_name.replace(" ", "_"), is_person=False)
                        db.add(tag)
                        await db.flush()
                        tags_created += 1
                    exists = (await db.execute(
                        select(PhotoTag).where(
                            PhotoTag.photo_id == photo.id, PhotoTag.tag_id == tag.id
                        )
                    )).scalar_one_or_none()
                    if not exists:
                        db.add(PhotoTag(photo_id=photo.id, tag_id=tag.id))

            ok += 1
        except Exception as exc:
            logger.warning("sync file→db error for photo %d: %s", photo.id, exc)
            errors += 1

    await db.commit()
    return {"synced": ok, "tags_created": tags_created, "errors": errors, "total": len(photos)}


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


@router.post("/scan-library")
async def scan_library(db: DB, request: ScanLibraryRequest) -> dict:
    """Scan library root (or custom_path) for new/updated photos and import them.
    
    Runs in background and returns immediately.
    """
    import asyncio
    import sys
    from fernkam.importers.filesystem import scan_library
    from fernkam.task_manager import task_manager

    custom_path = request.custom_path
    print(f"[SCAN-LIBRARY] Received request with custom_path: {custom_path}", flush=True)
    sys.stderr.write(f"[SCAN-LIBRARY] Received request with custom_path: {custom_path}\n")
    sys.stderr.flush()
    
    # Create task
    task_id = task_manager.create_task("scan_library", f"Scanning: {custom_path or 'library root'}")
    
    # Write to debug file
    with open("debug_scan.log", "a") as f:
        f.write(f"[SCAN-LIBRARY] Received request with custom_path: {custom_path}\n")
        f.flush()
    
    # Run scan as background asyncio task with its OWN db session
    # (request db session closes when handler returns)
    async def run_scan():
        import traceback as tb
        from fernkam.db.session import async_session_factory
        async with async_session_factory() as bg_db:
            try:
                print(f"[SCAN-LIBRARY] Starting scan for: {custom_path}", flush=True)
                task_manager.update_task(task_id, message="Scanning files...")
                stats = await scan_library(bg_db, custom_path=custom_path)
                added = stats.get('added', 0)
                updated = stats.get('updated', 0)
                errors = stats.get('errors', 0)
                print(f"[SCAN-LIBRARY] Scan completed: {stats}", flush=True)
                logger.info(f"Scan completed: {stats}")
                task_manager.update_task(task_id, message=f"Import done: +{added} new, {updated} updated, {errors} errors. Starting face scan...", progress=stats)

                # Auto face-scan newly imported photos
                if added > 0:
                    print(f"[SCAN-LIBRARY] Auto face-scan for {added} new photos...", flush=True)
                    task_manager.update_task(task_id, message=f"Face scanning {added} new photos...")
                    from fernkam.api.routers.photos import _detect_and_suggest
                    from sqlalchemy import select as sa_select
                    from fernkam.db.models.photos import Face, Photo as PhotoModel
                    scanned = sa_select(Face.photo_id).distinct()
                    unscanned_q = sa_select(PhotoModel.id).where(
                        PhotoModel.media_type == "image",
                        PhotoModel.status == 1,
                        PhotoModel.id.not_in(scanned)
                    )
                    result = await bg_db.execute(unscanned_q)
                    photo_ids = [r[0] for r in result.fetchall()]
                    face_count = 0
                    for pid in photo_ids:
                        try:
                            faces, _ = await _detect_and_suggest(pid, bg_db)
                            face_count += len(faces)
                            print(f"[SCAN-LIBRARY] Photo {pid}: {len(faces)} faces", flush=True)
                        except Exception as fe:
                            print(f"[SCAN-LIBRARY] Face scan error for {pid}: {fe}", flush=True)
                    task_manager.update_task(task_id, status="completed",
                        message=f"Done: +{added} imported, {face_count} faces detected",
                        progress={**stats, "faces_detected": face_count})
                else:
                    task_manager.update_task(task_id, status="completed",
                        message=f"Done: {added} new, {updated} updated, {errors} errors",
                        progress=stats)
            except Exception as e:
                print(f"[SCAN-LIBRARY] ERROR: {e}", flush=True)
                print(tb.format_exc(), flush=True)
                logger.error(f"Scan library error: {e}", exc_info=True)
                task_manager.update_task(task_id, status="failed", message=f"Error: {e}")
    
    # Start background task (named so lifespan can cancel selectively)
    asyncio.create_task(run_scan(), name=f"fernkam-scan-{task_id}")
    
    print(f"[SCAN-LIBRARY] Returning immediately with status: running", flush=True)
    return {"status": "running", "message": "Scan started in background", "task_id": task_id}


@router.get("/tasks")
async def get_tasks() -> dict:
    """Get all background tasks."""
    from fernkam.task_manager import task_manager
    tasks = task_manager.get_all_tasks()
    return {
        "tasks": [
            {
                "id": t.id,
                "task_type": t.task_type,
                "status": t.status,
                "message": t.message,
                "started_at": t.started_at.isoformat(),
                "completed_at": t.completed_at.isoformat() if t.completed_at else None,
                "progress": t.progress
            }
            for t in tasks
        ]
    }


@router.post("/backfill-thumbnails")
async def backfill_thumbnails(db: DB, limit: int = Body(500)) -> dict:
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
async def backfill_crops(db: DB, limit: int = Body(500)) -> dict:
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
