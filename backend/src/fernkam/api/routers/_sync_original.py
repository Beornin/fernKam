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


class ScanFacesRequest(BaseModel):
    """Re-scan photos that have never been face-scanned.

    - album_path: limit to photos under this album path (NULL = entire library).
    - limit: max number of photos to process this run; 0 means all.
    """
    album_path: Optional[str] = None
    limit: int = 0


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


@router.post("/refresh-metadata")
async def refresh_metadata_from_files(
    db: DB,
    album_path: Optional[str] = Body(None),
    photo_ids: Optional[list[int]] = Body(None),
) -> dict:
    """Re-read full EXIF + metadata from every image file and update the DB.

    Unlike /file-to-db (XMP-only), this reads ALL metadata fields:
    taken_at, camera, lens, GPS, orientation, dimensions, rating, tags, faces, etc.

    Runs as a cancellable background task. Progress via /sync/tasks.
    """
    import asyncio
    from fernkam.task_manager import task_manager

    q = select(Photo.id, Photo.album_path, Photo.filename).where(Photo.status == 1)
    if photo_ids:
        q = q.where(Photo.id.in_(photo_ids))
    elif album_path:
        q = q.where(Photo.album_path.like(f"{album_path}%"))
    q = q.order_by(Photo.id.asc())

    rows = (await db.execute(q)).fetchall()
    if not rows:
        return {"task_id": None, "queued": 0, "message": "No photos to process"}

    scope = f"album {album_path}" if album_path else ("selection" if photo_ids else "all photos")
    task_id = await task_manager.create_task(
        "refresh_metadata",
        f"Queued {len(rows):,} photos ({scope}) for full metadata refresh…",
    )

    async def run_refresh():
        import time as _time
        from datetime import datetime, timezone
        from fernkam.db.session import async_session_factory
        from fernkam.importers.filesystem import update_photo_metadata
        from fernkam.metadata_sync import read_many_metadata_async
        from fernkam.thumbnails import photo_disk_path

        t_start = _time.time()
        total = len(rows)
        ok = errors = skipped = 0
        BATCH = 100

        for batch_start in range(0, total, BATCH):
            task = await task_manager.get_task(task_id)
            if task and task.status == "cancelled":
                break

            batch_rows = rows[batch_start: batch_start + BATCH]
            # Resolve paths + filter to those that exist on disk.
            present: list = []  # (row, file_path, file_mtime)
            paths: list = []
            for row in batch_rows:
                fp = photo_disk_path(row.album_path, row.filename)
                if not fp.exists():
                    skipped += 1
                    continue
                mtime = datetime.fromtimestamp(fp.stat().st_mtime, tz=timezone.utc)
                present.append((row, fp, mtime))
                paths.append(fp)

            # One persistent-exiftool batch read (~13x faster than per-file).
            meta_map = await read_many_metadata_async(paths, chunk_size=BATCH)

            async with async_session_factory() as bdb:
                for row, fp, mtime in present:
                    try:
                        async with bdb.begin_nested():  # savepoint isolates per-photo failures
                            await update_photo_metadata(
                                bdb, row.id, fp, mtime, metadata=meta_map.get(fp, {})
                            )
                        ok += 1
                    except Exception as exc:
                        logger.warning("refresh-metadata error photo %d: %s", row.id, exc)
                        errors += 1
                await bdb.commit()

            done = min(batch_start + BATCH, total)
            elapsed = _time.time() - t_start
            rate = done / elapsed if elapsed > 0 else 0
            eta = int((total - done) / rate) if rate > 0 else 0
            await task_manager.update_task(
                task_id,
                message=f"Refreshing… {done:,}/{total:,} | ✓ {ok} ✗ {errors} skipped {skipped} | ETA {eta}s",
                progress={"done": done, "total": total, "ok": ok, "errors": errors, "skipped": skipped},
            )

        t_total = _time.time() - t_start
        await task_manager.update_task(
            task_id,
            status="completed",
            message=f"Done: {ok:,} refreshed, {errors} errors, {skipped} missing files ({t_total:.0f}s)",
            progress={"done": total, "total": total, "ok": ok, "errors": errors, "skipped": skipped},
        )

    asyncio.create_task(run_refresh(), name=f"fernkam-refresh-meta-{task_id}")
    return {"status": "running", "task_id": task_id, "queued": len(rows),
            "message": f"Metadata refresh started for {len(rows):,} photos"}


@router.post("/write-metadata")
async def write_metadata_all(
    db: DB,
    dirty_only: bool = Body(False),
    album_path: Optional[str] = Body(None),
    batch_size: int = Body(200),
    concurrency: int = Body(4),
) -> dict:
    """Write all confirmed face regions and tags to image files (XMP).

    Runs as a cancellable background task. Progress is visible via /sync/tasks.

    - dirty_only: only process photos flagged file_sync_dirty (fast incremental mode)
    - album_path: restrict to one album subtree
    - batch_size: photos per exiftool call (default 200; tune up for speed)
    - concurrency: parallel exiftool workers (default 4)
    """
    import asyncio
    import os as _os
    from fernkam.task_manager import task_manager

    q = (
        select(Photo.id)
        .where(Photo.status == 1)
        .where(Photo.media_type == "image")
        .order_by(Photo.id.asc())
    )
    if dirty_only:
        q = q.where(Photo.file_sync_dirty == True)  # noqa: E712
    if album_path:
        q = q.where(Photo.album_path.like(f"{album_path}%"))

    photo_ids = [r[0] for r in (await db.execute(q)).fetchall()]
    if not photo_ids:
        return {"task_id": None, "queued": 0, "message": "No photos to process"}

    scope = f"album {album_path}" if album_path else ("dirty" if dirty_only else "all")
    task_id = await task_manager.create_task(
        "write_metadata",
        f"Queued {len(photo_ids):,} photos ({scope}) for metadata write-back…",
    )

    async def run_write():
        import time as _time
        from fernkam.db.session import async_session_factory
        from fernkam.metadata_sync import build_photo_payload, write_metadata_batch

        t_start = _time.time()
        total = len(photo_ids)
        ok = errors = 0
        sem = asyncio.Semaphore(concurrency)
        loop = asyncio.get_event_loop()

        async def process_batch(batch_ids: list[int]) -> tuple[int, int]:
            async with sem:
                async with async_session_factory() as bdb:
                    photos = (await bdb.execute(
                        select(Photo)
                        .where(Photo.id.in_(batch_ids))
                        .options(
                            selectinload(Photo.photo_tags).selectinload(PhotoTag.tag),
                            selectinload(Photo.faces).selectinload(Face.person_tag),
                        )
                    )).scalars().all()

                    payloads: list[dict] = []
                    written_ids: list[int] = []
                    for photo in photos:
                        tags = [pt.tag for pt in photo.photo_tags if pt.tag]
                        # Only write named faces: confirmed with person_tag or DK-imported region_name
                        named_faces = [
                            f for f in photo.faces
                            if f.x is not None
                            and (f.person_tag is not None or (f.region_name or "").strip())
                        ]
                        p = build_photo_payload(photo, tags, named_faces)
                        if p:
                            payloads.append(p)
                            written_ids.append(photo.id)

                    if not payloads:
                        return 0, 0

                    b_ok, b_err = await loop.run_in_executor(
                        None, write_metadata_batch, payloads
                    )

                    if b_ok and written_ids:
                        now = datetime.now(timezone.utc)
                        await bdb.execute(
                            update(Photo)
                            .where(Photo.id.in_(written_ids))
                            .values(meta_synced_at=now, file_sync_dirty=False)
                        )
                        await bdb.commit()

                    return b_ok, b_err

        batches = [
            photo_ids[i : i + batch_size]
            for i in range(0, total, batch_size)
        ]

        for wave_start in range(0, len(batches), concurrency * 2):
            task = await task_manager.get_task(task_id)
            if task and task.status == "cancelled":
                print(f"[write-metadata] Cancelled at {ok}/{total}", flush=True)
                return

            wave = batches[wave_start : wave_start + concurrency * 2]
            results = await asyncio.gather(*[process_batch(b) for b in wave], return_exceptions=True)

            for r in results:
                if isinstance(r, Exception):
                    errors += batch_size
                    logger.warning("[write-metadata] batch error: %s", r)
                else:
                    ok += r[0]
                    errors += r[1]

            elapsed = _time.time() - t_start
            done = ok + errors
            rate = done / elapsed if elapsed > 0 else 0
            eta = int((total - done) / rate) if rate > 0 else 0
            await task_manager.update_task(
                task_id,
                message=(
                    f"Writing… {done:,}/{total:,} "
                    f"({rate:.0f}/s · ETA {eta}s)"
                ),
            )

        t_total = _time.time() - t_start
        print(
            f"[write-metadata] Done: {ok:,} ok, {errors} errors, "
            f"{len(batches)} batches, {t_total:.1f}s",
            flush=True,
        )
        await task_manager.update_task(
            task_id,
            status="completed",
            message=f"Done: {ok:,} photos written, {errors} errors ({t_total:.0f}s)",
            progress={
                "ok": ok,
                "errors": errors,
                "total": total,
                "elapsed_s": round(t_total, 1),
                "batches": len(batches),
            },
        )

    asyncio.create_task(run_write(), name=f"fernkam-write-metadata-{task_id}")
    return {
        "task_id": task_id,
        "queued": len(photo_ids),
        "message": "running",
        "scope": scope,
    }


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


@router.post("/scan-library")
async def scan_library(db: DB, request: ScanLibraryRequest) -> dict:
    """Scan library root (or custom_path) for new/updated photos and import them.
    
    Runs in background and returns immediately.
    """
    import asyncio
    from fernkam.importers.filesystem import scan_library
    from fernkam.task_manager import task_manager

    import os as _os
    custom_path = request.custom_path
    print(f"[SCAN-LIBRARY] Received request with custom_path: {custom_path}", flush=True)

    # Create task
    task_id = await task_manager.create_task("scan_library", f"Scanning: {custom_path or 'library root'}")

    _cpus = _os.cpu_count() or 4
    FACE_DETECT_CONCURRENCY = int(_os.getenv("FERNKAM_FACE_CONCURRENCY", str(max(4, _cpus))))

    # Run scan as background asyncio task with its OWN db session
    # (request db session closes when handler returns)
    async def run_scan():
        import traceback as tb
        import time as _time
        from fernkam.db.session import async_session_factory
        from fernkam.api.routers.photos import _detect_and_suggest
        from fernkam.api.routers.faces import _auto_confirm_sweep

        async with async_session_factory() as bg_db:
            try:
                print(f"[SCAN-LIBRARY] Starting scan for: {custom_path}", flush=True)
                await task_manager.update_task(task_id, message="Scanning files...")

                # ── Pipeline setup ────────────────────────────────────────────
                # Face-detect tasks fire per batch as soon as photos are committed,
                # so detection runs concurrently with importing the next batch.
                face_tasks: list[asyncio.Task] = []
                face_sem = asyncio.Semaphore(FACE_DETECT_CONCURRENCY)

                async def detect_one(photo_id: int) -> int:
                    async with face_sem:
                        async with async_session_factory() as det_db:
                            try:
                                faces, _ = await _detect_and_suggest(photo_id, det_db)
                                return len(faces)
                            except Exception as fe:
                                logger.warning("[SCAN] Face error %d: %s", photo_id, fe)
                                return 0

                async def on_photo_imported(photo_id: int) -> None:
                    """Spawn face-detect task immediately; returns without waiting."""
                    t = asyncio.create_task(
                        detect_one(photo_id), name=f"face-{photo_id}"
                    )
                    face_tasks.append(t)

                async def on_progress(stats: dict) -> None:
                    queued = len(face_tasks)
                    done   = sum(1 for t in face_tasks if t.done())
                    await task_manager.update_task(task_id,
                        message=f"Importing… {stats['added']} added "
                                f"| faces: {done}/{queued} done")

                # ── Phase 1-2: import (face tasks fire per committed batch) ──
                t_start = _time.time()
                stats = await scan_library(
                    bg_db,
                    custom_path=custom_path,
                    photo_added_callback=on_photo_imported,
                    progress_callback=on_progress,
                )
                added     = stats.get('added', 0)
                errors    = stats.get('errors', 0)
                deleted   = stats.get('deleted', 0)
                added_ids = stats.get('added_ids', [])

                print(
                    f"[SCAN-LIBRARY] Import done: +{added} added, {deleted} deleted, "
                    f"{errors} errors | {len(face_tasks)} face tasks queued",
                    flush=True,
                )

                # ── Phase 3: drain remaining face-detect tasks ────────────────
                if face_tasks:
                    await task_manager.update_task(task_id,
                        message=f"Waiting for face detection "
                                f"({sum(1 for t in face_tasks if not t.done())} remaining)…")
                    results = await asyncio.gather(*face_tasks, return_exceptions=True)
                    face_count = sum(r for r in results if isinstance(r, int))
                else:
                    face_count = 0

                # ── Phase 4: single end-of-scan auto-confirm sweep ────────────
                try:
                    await task_manager.update_task(task_id, message="Auto-confirming faces…")
                    confirmed_n = await _auto_confirm_sweep(bg_db)
                    if confirmed_n:
                        print(f"[SCAN-LIBRARY] Sweep auto-confirmed {confirmed_n} faces", flush=True)
                except Exception as se:
                    print(f"[SCAN-LIBRARY] Sweep error: {se}", flush=True)

                t_total = _time.time() - t_start
                per_photo_ms = (t_total / max(1, added)) * 1000.0
                print(
                    f"[SCAN-LIBRARY] Done: +{added}, {face_count} faces, {errors} errors, "
                    f"total={t_total:.1f}s ({per_photo_ms:.0f} ms/photo)",
                    flush=True,
                )
                logger.info(
                    "Scan completed: added=%d faces=%d errors=%d total_s=%.1f ms_per=%0.f",
                    added, face_count, errors, t_total, per_photo_ms,
                )
                await task_manager.update_task(task_id, status="completed",
                    message=f"Done: +{added} imported, {stats.get('updated', 0)} refreshed, {face_count} faces detected",
                    progress={**stats, "faces_detected": face_count, "total_s": round(t_total, 1)})
            except Exception as e:
                print(f"[SCAN-LIBRARY] ERROR: {e}", flush=True)
                print(tb.format_exc(), flush=True)
                logger.error("Scan library error: %s", e, exc_info=True)
                await task_manager.update_task(task_id, status="failed", message=f"Error: {e}")
    
    # Start background task (named so lifespan can cancel selectively)
    asyncio.create_task(run_scan(), name=f"fernkam-scan-{task_id}")
    
    print(f"[SCAN-LIBRARY] Returning immediately with status: running", flush=True)
    return {"status": "running", "message": "Scan started in background", "task_id": task_id}


@router.post("/scan-faces")
async def scan_faces(db: DB, request: ScanFacesRequest) -> dict:
    """Run face detection on photos where faces_scanned_at IS NULL.

    Cancellable via /tasks/{task_id}/cancel. Runs in background.
    """
    import asyncio
    from fernkam.task_manager import task_manager

    # Pick the candidate photo IDs immediately so we can return a count.
    q = (
        select(Photo.id)
        .where(Photo.status == 1)
        .where(Photo.media_type == "image")
        .where(Photo.faces_scanned_at.is_(None))
    )
    if request.album_path:
        q = q.where(Photo.album_path.like(f"{request.album_path}%"))
    q = q.order_by(Photo.id.asc())
    if request.limit and request.limit > 0:
        q = q.limit(request.limit)

    photo_ids = [r[0] for r in (await db.execute(q)).fetchall()]
    if not photo_ids:
        return {"task_id": None, "queued": 0, "message": "No unscanned photos"}

    import os as _os
    task_id = await task_manager.create_task(
        "scan_faces",
        f"Scanning faces for {len(photo_ids)} photos…"
    )
    _cpus = _os.cpu_count() or 4
    FACE_DETECT_CONCURRENCY = int(_os.getenv("FERNKAM_FACE_CONCURRENCY", str(max(4, _cpus))))
    PROGRESS_EVERY = 50

    async def run_face_scan():
        from fernkam.db.session import async_session_factory
        from fernkam.api.routers.photos import _detect_and_suggest
        from fernkam.api.routers.faces import _auto_confirm_sweep
        import time as _time

        async with async_session_factory() as bg_db:
            try:
                t_start = _time.time()
                face_count = 0
                face_sem = asyncio.Semaphore(FACE_DETECT_CONCURRENCY)

                async def detect_one(pid: int) -> int:
                    async with face_sem:
                        async with async_session_factory() as det_db:
                            try:
                                faces, _ = await _detect_and_suggest(pid, det_db)
                                return len(faces)
                            except Exception as fe:
                                print(f"[SCAN-FACES] Photo {pid} error: {fe}", flush=True)
                                return 0

                for batch_start in range(0, len(photo_ids), PROGRESS_EVERY):
                    task = await task_manager.get_task(task_id)
                    if task and task.status == "cancelled":
                        print("[SCAN-FACES] Cancelled", flush=True)
                        return
                    batch = photo_ids[batch_start: batch_start + PROGRESS_EVERY]
                    res = await asyncio.gather(*[detect_one(pid) for pid in batch])
                    face_count += sum(res)
                    await task_manager.update_task(
                        task_id,
                        message=f"Scanning faces… {batch_start + len(batch)}/{len(photo_ids)} ({face_count} found)",
                    )

                try:
                    await task_manager.update_task(task_id, message="Auto-confirming faces…")
                    confirmed_n = await _auto_confirm_sweep(bg_db)
                except Exception as se:
                    print(f"[SCAN-FACES] Sweep error: {se}", flush=True)
                    confirmed_n = 0

                t_total = _time.time() - t_start
                ms_per = (t_total / max(1, len(photo_ids))) * 1000.0
                print(
                    f"[SCAN-FACES] Done: {len(photo_ids)} photos, {face_count} faces, "
                    f"{confirmed_n} auto-confirmed, {t_total:.1f}s ({ms_per:.0f} ms/photo)",
                    flush=True,
                )
                await task_manager.update_task(
                    task_id,
                    status="completed",
                    message=f"Done: {face_count} faces in {len(photo_ids)} photos ({confirmed_n} auto-confirmed)",
                    progress={
                        "photos": len(photo_ids),
                        "faces": face_count,
                        "auto_confirmed": confirmed_n,
                        "elapsed_s": round(t_total, 1),
                        "ms_per_photo": round(ms_per),
                    },
                )
            except Exception as e:
                logger.error("scan_faces error: %s", e, exc_info=True)
                await task_manager.update_task(task_id, status="failed", message=f"Error: {e}")

    asyncio.create_task(run_face_scan(), name=f"fernkam-scan-faces-{task_id}")
    return {"task_id": task_id, "queued": len(photo_ids), "message": "running"}


@router.get("/tasks")
async def get_tasks(running_only: bool = Query(False)) -> dict:
    """Get background tasks. Pass running_only=1 for the lightweight status-bar poll."""
    from fernkam.task_manager import task_manager
    tasks = (
        await task_manager.get_running_tasks()
        if running_only
        else await task_manager.get_all_tasks()
    )
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

@router.post("/tasks/{task_id}/cancel")
async def cancel_task(task_id: str) -> dict:
    """Cancel a running task."""
    from fernkam.task_manager import task_manager
    cancelled = await task_manager.cancel_task(task_id)
    return {"cancelled": cancelled}


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
