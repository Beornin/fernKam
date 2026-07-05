"""Library scan endpoints: import new photos and run face detection."""
from __future__ import annotations

import logging
from typing import Optional

from fastapi import APIRouter
from pydantic import BaseModel
from sqlalchemy import select

from fernkam.api.deps import DB
from fernkam.db.models.photos import Photo

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


@router.post("/backfill-video-duration")
async def backfill_video_duration(db: DB) -> dict:
    """Probe duration for all videos that have duration_secs IS NULL.

    Runs in background; returns a task_id immediately.
    """
    import asyncio
    from fernkam.task_manager import task_manager
    from fernkam.db.session import async_session_factory as _factory
    from fernkam.db.models.photos import Photo as _Photo
    from fernkam.thumbnails import photo_disk_path, probe_video_duration
    from sqlalchemy import select as _sel, update as _up

    rows = (await db.execute(
        _sel(_Photo.id, _Photo.album_path, _Photo.filename)
        .where(_Photo.status == 1)
        .where(_Photo.media_type == "video")
        .where(_Photo.duration_secs.is_(None))
        .order_by(_Photo.id)
    )).fetchall()
    total = len(rows)
    task_id = await task_manager.create_task(
        "backfill_video_duration",
        f"Probing duration for {total} videos…",
    )

    async def _run() -> None:
        done = 0
        try:
            async with _factory() as bg_db:
                loop = asyncio.get_event_loop()
                BATCH = 200
                for start in range(0, total, BATCH):
                    chunk = rows[start:start + BATCH]
                    for photo_id, album_path, filename in chunk:
                        src = photo_disk_path(album_path, filename)
                        dur = await loop.run_in_executor(None, probe_video_duration, src)
                        if dur is not None:
                            await bg_db.execute(
                                _up(_Photo).where(_Photo.id == photo_id)
                                .values(duration_secs=dur)
                            )
                        done += 1
                    await bg_db.commit()
                    await task_manager.update_task(
                        task_id, message=f"Probed {done}/{total}…",
                        progress={"done": done, "total": total},
                    )
            await task_manager.update_task(
                task_id, status="completed",
                message=f"Done: {done} videos probed",
                progress={"done": done, "total": total},
            )
        except Exception as exc:
            import logging
            logging.getLogger(__name__).exception("backfill_video_duration failed")
            await task_manager.update_task(task_id, status="failed", message=str(exc))

    asyncio.create_task(_run())
    return {"task_id": task_id, "status": "started", "total": total}


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
