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


@router.get("/library-root")
async def library_root() -> dict:
    """The folder fernKam catalogues — scans and imports stay inside it."""
    from fernkam.config import get_settings
    return {"library_root": str(get_settings().library_root).replace("\\", "/")}


@router.post("/scan-library")
async def scan_library(db: DB, request: ScanLibraryRequest) -> dict:
    """Scan the library (or a folder inside it) for new, changed and removed files.

    Runs in background and returns immediately.
    """
    from fastapi import HTTPException
    from fernkam.importers.filesystem import ScanRootError, resolve_scan_root
    try:
        resolve_scan_root(request.custom_path)
    except ScanRootError as exc:
        raise HTTPException(400, str(exc))
    task_id = await start_library_scan(request.custom_path)
    return {"status": "running", "message": "Scan started in background", "task_id": task_id}


async def start_library_scan(custom_path: Optional[str] = None, label: Optional[str] = None) -> str:
    """Start a library scan as a background task and return its task id.

    Used by the Scan buttons, the refresh at startup and the library watcher.
    Raises TaskConflict while another job that changes files is running.
    """
    import asyncio
    from fernkam.importers.filesystem import scan_library
    from fernkam.task_manager import task_manager

    import os as _os
    print(f"[SCAN-LIBRARY] Starting scan of: {custom_path or 'library root'}", flush=True)
    task_id = await task_manager.create_task("scan_library", label or f"Scanning: {custom_path or 'library root'}")

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
        from fernkam.importers.filesystem import ScanCancelled

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
                    # Called during the walk and after every committed batch —
                    # the safe points to honour Cancel. The final call (after
                    # removals, carrying added_ids) is too late to stop anything.
                    if "added_ids" not in stats:
                        current = await task_manager.get_task(task_id)
                        if current and current.status == "cancelled":
                            raise ScanCancelled()
                    if stats.get("phase") == "scanning":
                        await task_manager.update_task(task_id,
                            message=f"Scanning… {stats['scanned']:,} files checked | {stats['current_dir']}")
                        return
                    queued = len(face_tasks)
                    done   = sum(1 for t in face_tasks if t.done())
                    total  = stats.get("total", 0)
                    processed = stats.get("added", 0) + stats.get("updated", 0)
                    pct = f" ({processed:,}/{total:,})" if total else ""
                    await task_manager.update_task(task_id,
                        message=f"Importing… {stats['added']} added, {stats.get('updated', 0)} refreshed{pct} "
                                f"| faces: {done}/{queued} done")

                # ── Phase 1-2: import (face tasks fire per committed batch) ──
                t_start = _time.time()
                stats = await scan_library(
                    bg_db,
                    custom_path=custom_path,
                    photo_added_callback=on_photo_imported,
                    progress_callback=on_progress,
                )
                if stats.get("error"):  # e.g. LIBRARY_ROOT missing — nothing was scanned
                    await task_manager.update_task(task_id, status="failed", message=stats["error"])
                    return
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
                    sweep_result = await _auto_confirm_sweep(bg_db)
                    confirmed_n = sweep_result.get("confirmed", 0)
                    if confirmed_n:
                        print(f"[SCAN-LIBRARY] Sweep auto-confirmed {confirmed_n} faces", flush=True)
                except Exception as se:
                    print(f"[SCAN-LIBRARY] Sweep error: {se}", flush=True)

                # ── Phase 5: keep semantic search current ────────────────────
                # New photos and ones whose picture changed get CLIP embeddings
                # now — but only if the model is already downloaded (someone
                # uses Discover); a scan must not trigger a 600 MB download.
                embedded_n = 0
                refresh_ids = added_ids + stats.get("pixel_changed_ids", [])
                if refresh_ids:
                    from fernkam import clip_embed
                    if clip_embed.models_downloaded():
                        try:
                            from sqlalchemy import text as _text
                            from fernkam.api.routers.semantic import embed_rows
                            await task_manager.update_task(task_id, message="Updating search index…")
                            rows = (await bg_db.execute(_text(
                                "SELECT id, album_path, filename FROM photos WHERE id = ANY(:ids) "
                                "AND status = 1 AND embedding_v IS NULL AND media_type IN ('image', 'video') "
                                "ORDER BY id"), {"ids": refresh_ids})).all()
                            embedded_n, _ = await embed_rows(bg_db, [tuple(r) for r in rows])
                        except Exception as ee:
                            logger.warning("[SCAN] search-index update failed: %s", ee)
                    # And with every Tag Review model the user has indexed.
                    try:
                        from fernkam import embed_index
                        await embed_index.refresh_photos(bg_db, refresh_ids)
                    except Exception as ee:
                        logger.warning("[SCAN] Tag Review model update failed: %s", ee)

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
                warnings = ""
                if stats.get("moved"):
                    warnings += f" — {stats['moved']} moved or renamed outside fernKam (tags kept)"
                if stats.get("pixels_changed"):
                    warnings += f" — {stats['pixels_changed']} edited outside fernKam (refreshed)"
                if embedded_n:
                    warnings += f" — {embedded_n} added to search"
                if stats.get("deletion_skipped"):
                    warnings += f" — removals skipped: {stats['deletion_skipped']}"
                if stats.get("unreadable_dirs"):
                    warnings += (f" — {stats['unreadable_dirs']} folder(s) could not be read; "
                                 f"their photos were kept")
                await task_manager.update_task(task_id, status="completed",
                    message=(f"Done: +{added} imported, {stats.get('updated', 0)} refreshed, "
                             f"{deleted} removed, {stats.get('skipped', 0):,} unchanged, "
                             f"{face_count} faces detected{warnings}"),
                    progress={**stats, "faces_detected": face_count, "total_s": round(t_total, 1)})
            except ScanCancelled:
                await bg_db.rollback()
                print("[SCAN-LIBRARY] Cancelled", flush=True)
                await task_manager.update_task(
                    task_id, status="cancelled",
                    message="Cancelled — photos imported before stopping were kept; nothing was removed")
            except Exception as e:
                print(f"[SCAN-LIBRARY] ERROR: {e}", flush=True)
                print(tb.format_exc(), flush=True)
                logger.error("Scan library error: %s", e, exc_info=True)
                await task_manager.update_task(task_id, status="failed", message=f"Error: {e}")
    
    # Start background task (named so lifespan can cancel selectively)
    asyncio.create_task(run_scan(), name=f"fernkam-scan-{task_id}")
    return task_id


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
        q = q.where(Photo.album_path.like(f"{request.album_path.lstrip('/')}%"))
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
                    sweep_result = await _auto_confirm_sweep(bg_db)
                    confirmed_n = sweep_result.get("confirmed", 0)
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


@router.get("/db-stats")
async def db_stats() -> dict:
    """Postgres size/bloat stats for the Maintenance page."""
    from fernkam.db.session import get_async_engine
    from fernkam.db.maintenance import get_db_stats

    return await get_db_stats(get_async_engine())


@router.post("/vacuum-analyze")
async def vacuum_analyze() -> dict:
    """Run VACUUM (ANALYZE) over the whole database. Runs in background — can take a while on large tables."""
    import asyncio
    from fernkam.task_manager import task_manager
    from fernkam.db.session import get_async_engine
    from fernkam.db.maintenance import run_vacuum_analyze as _run_vacuum

    task_id = await task_manager.create_task("vacuum_analyze", "Running VACUUM ANALYZE…")

    async def _run():
        try:
            await _run_vacuum(get_async_engine())
            await task_manager.update_task(task_id, status="completed", message="VACUUM ANALYZE complete")
        except Exception as exc:
            logger.error("vacuum_analyze failed: %s", exc, exc_info=True)
            await task_manager.update_task(task_id, status="failed", message=f"Error: {exc}")

    asyncio.create_task(_run(), name=f"fernkam-vacuum-{task_id}")
    return {"task_id": task_id, "status": "running"}


@router.post("/reindex")
async def reindex() -> dict:
    """REINDEX CONCURRENTLY the handful of large/heavy-churn indexes. Runs in background."""
    import asyncio
    from fernkam.task_manager import task_manager
    from fernkam.db.session import get_async_engine
    from fernkam.db.maintenance import run_reindex_concurrently, REINDEX_CANDIDATES

    task_id = await task_manager.create_task("reindex", f"Reindexing {len(REINDEX_CANDIDATES)} indexes…")

    async def _run():
        try:
            results = await run_reindex_concurrently(get_async_engine(), REINDEX_CANDIDATES)
            ok = sum(1 for v in results.values() if v == "ok")
            await task_manager.update_task(task_id, status="completed",
                message=f"Reindexed {ok}/{len(results)}", progress=results)
        except Exception as exc:
            logger.error("reindex failed: %s", exc, exc_info=True)
            await task_manager.update_task(task_id, status="failed", message=f"Error: {exc}")

    asyncio.create_task(_run(), name=f"fernkam-reindex-{task_id}")
    return {"task_id": task_id, "status": "running"}


@router.post("/rebuild-indexes")
async def rebuild_indexes() -> dict:
    """Re-run idempotent index creation/cleanup (same routine as startup)."""
    from fernkam.db.session import get_async_engine
    from fernkam.db.index_setup import ensure_indexes

    await ensure_indexes(get_async_engine())
    return {"status": "ok"}
