"""Metadata sync endpoints: DB↔file XMP sync, full EXIF refresh, write-back."""
from __future__ import annotations

import logging
from typing import Optional

from fastapi import APIRouter, Body
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from fernkam.api.deps import DB
from fernkam.db.models.photos import Face, Photo, PhotoTag
from fernkam.services.photo_query import in_album

logger = logging.getLogger(__name__)
router = APIRouter()

# What DB → Files writes: live still images. A tagged video is flagged too, but its tags stay in
# the database; the Maintenance count uses this same filter so it matches what a run will do.
WRITABLE = (Photo.status == 1, Photo.media_type == "image")


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
    from fernkam.task_manager import fmt_eta, task_manager

    q = select(Photo.id, Photo.album_path, Photo.filename, Photo.file_modified_at_sync).where(Photo.status == 1)
    if photo_ids:
        q = q.where(Photo.id.in_(photo_ids))
    elif album_path:
        q = q.where(in_album(Photo.album_path, album_path))
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
        from fernkam.sync_merge import load_states
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
            present: list = []
            paths: list = []
            for row in batch_rows:
                fp = photo_disk_path(row.album_path, row.filename)
                if not fp.exists():
                    skipped += 1
                    continue
                mtime = datetime.fromtimestamp(fp.stat().st_mtime, tz=timezone.utc)
                present.append((row, fp, mtime))
                paths.append(fp)

            meta_map = await read_many_metadata_async(paths, chunk_size=BATCH)
            # Rehash only files whose mtime moved since fernKam last saw them:
            # hashing reads the whole file, and this refresh covers every photo.
            from fernkam.importers.filesystem import MTIME_SLACK_SECONDS, _sha256_path
            loop = asyncio.get_running_loop()
            moved = [(row, fp) for row, fp, mtime in present
                     if row.file_modified_at_sync is None
                     or abs((mtime - row.file_modified_at_sync).total_seconds()) > MTIME_SLACK_SECONDS]
            digests = await asyncio.gather(*[loop.run_in_executor(None, _sha256_path, fp) for _, fp in moved])
            hash_of = {row.id: d for (row, _), d in zip(moved, digests)}

            async with async_session_factory() as bdb:
                states = await load_states(bdb, [row.id for row, _, _ in present])
                tag_cache: dict = {}
                for row, fp, mtime in present:
                    try:
                        async with bdb.begin_nested():  # savepoint isolates per-photo failures
                            merged = await update_photo_metadata(
                                bdb, row.id, fp, mtime, metadata=meta_map.get(fp, {}),
                                state=states.get(row.id), tag_cache=tag_cache,
                                sha256=hash_of.get(row.id),
                            )
                        if merged is None:
                            errors += 1  # unreadable
                        else:
                            ok += 1
                    except Exception as exc:
                        tag_cache.clear()
                        logger.warning("refresh-metadata error photo %d: %s", row.id, exc)
                        errors += 1
                await bdb.commit()

            done = min(batch_start + BATCH, total)
            elapsed = _time.time() - t_start
            rate = done / elapsed if elapsed > 0 else 0
            eta = int((total - done) / rate) if rate > 0 else 0
            await task_manager.update_task(
                task_id,
                message=f"Refreshing… {done:,}/{total:,} | ✓ {ok} ✗ {errors} skipped {skipped} | ETA {fmt_eta(eta)}",
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


async def write_photos(photo_ids: list[int]) -> tuple[int, dict[str, str], int]:
    """Write these photos' tags, rating, label, title, caption and named faces into their files:
    (files written, {file: why it failed}, files re-read first). DB → Files runs it per batch,
    auto_write per photo. Only files exiftool actually wrote are marked synced; a failed one stays
    "needs sync" and is all the next pending-only pass has to retry."""
    import asyncio
    from fernkam.db.session import async_session_factory
    from fernkam.importers.filesystem import reconcile_outdated
    from fernkam.metadata_sync import build_photo_payload, mark_files_synced, write_metadata_batch

    async with async_session_factory() as bdb:
        # Check before writing whether fernKam is out of date: files another program changed
        # since fernKam last read them are re-read and merged first, so their edits are carried
        # into the write instead of overwritten.
        n_reread = await reconcile_outdated(bdb, photo_ids)
        await bdb.commit()

        photos = (await bdb.execute(
            select(Photo)
            .where(Photo.id.in_(photo_ids))
            .options(
                selectinload(Photo.photo_tags).selectinload(PhotoTag.tag),
                selectinload(Photo.faces).selectinload(Face.person_tag),
            )
        )).scalars().all()

        payloads: list[dict] = []
        photo_for_file: dict[str, int] = {}
        for photo in photos:
            tags = [pt.tag for pt in photo.photo_tags if pt.tag]
            named_faces = [
                f for f in photo.faces
                if f.x is not None
                and (f.person_tag is not None or (f.region_name or "").strip())
            ]
            p = build_photo_payload(photo, tags, named_faces)
            if p:
                payloads.append(p)
                photo_for_file[p["SourceFile"]] = photo.id

        if not payloads:
            return 0, {}, n_reread

        written, failed = await asyncio.get_running_loop().run_in_executor(None, write_metadata_batch, payloads)
        if written:
            await mark_files_synced(bdb, [(photo_for_file[src], src) for src in written])
            await bdb.commit()
        return len(written), failed, n_reread


@router.post("/write-metadata")
async def write_metadata_all(
    db: DB,
    dirty_only: bool = Body(True),
    album_path: Optional[str] = Body(None),
    photo_ids: Optional[list[int]] = Body(None),
    batch_size: int = Body(200),
    concurrency: int = Body(1),
) -> dict:
    """Write tags, rating, label, title, caption and named face regions into
    the image files (XMP).

    Runs as a cancellable background task. Progress is visible via /sync/tasks.
    Files changed by another program since fernKam last read them are re-read
    and merged before writing. Files that fail stay "needs sync", so the next
    pending-only run retries just those.

    - dirty_only: only photos with changes not yet written (default); false
      rewrites every photo
    - photo_ids: exactly these photos, pending or not (right-click → Write
      metadata to file); overrides dirty_only
    - album_path: restrict to one album subtree
    - batch_size: photos per exiftool call (default 200; tune up for speed)
    - concurrency: parallel exiftool workers (default 1). Every write rewrites the whole file, and on a
      hard drive parallel rewrites fight over the disk head: 75 MB TIFFs took 0.77 s each with 1 worker,
      2.78 s with 4 (2026-10-14).
    """
    import asyncio
    from fernkam.task_manager import fmt_eta, task_manager

    q = select(Photo.id, Photo.file_size).where(*WRITABLE).order_by(Photo.id.asc())
    if photo_ids:
        q = q.where(Photo.id.in_(photo_ids))
    elif dirty_only:
        q = q.where(Photo.file_sync_dirty == True)  # noqa: E712
    if album_path:
        q = q.where(in_album(Photo.album_path, album_path))

    rows = (await db.execute(q)).fetchall()
    photo_ids = [r[0] for r in rows]
    if not photo_ids:
        return {"task_id": None, "queued": 0, "message": "No photos to process"}

    scope = ("selection" if photo_ids else f"album {album_path}" if album_path
             else "dirty" if dirty_only else "all")
    task_id = await task_manager.create_task(
        "write_metadata",
        f"Queued {len(photo_ids):,} photos ({scope}) for metadata write-back…",
    )

    async def run_write():
        import time as _time

        t_start = _time.time()
        total = len(photo_ids)
        ok = errors = reread = 0
        failures: list[dict] = []  # first few, for the task report
        sem = asyncio.Semaphore(concurrency)

        async def process_batch(batch_ids: list[int]) -> tuple[int, dict[str, str], int]:
            async with sem:
                return await write_photos(batch_ids)

        # A batch is at most batch_size photos and about BATCH_BYTES of files, so the files it just
        # wrote are still in the OS cache when they are read back and re-hashed. With 200 large TIFFs
        # a batch (~15 GB) they weren't, and every file was read from disk twice more.
        BATCH_BYTES = 2 * 1024 ** 3   # ponytail: fixed budget; tie it to free RAM if batches ever thrash
        batches: list[list[int]] = []
        cur: list[int] = []
        cur_bytes = 0
        for pid, size in rows:
            if cur and (len(cur) >= batch_size or cur_bytes + (size or 0) > BATCH_BYTES):
                batches.append(cur)
                cur, cur_bytes = [], 0
            cur.append(pid)
            cur_bytes += size or 0
        if cur:
            batches.append(cur)

        for wave_start in range(0, len(batches), concurrency * 2):
            task = await task_manager.get_task(task_id)
            if task and task.status == "cancelled":
                print(f"[write-metadata] Cancelled at {ok}/{total}", flush=True)
                return

            wave = batches[wave_start : wave_start + concurrency * 2]
            results = await asyncio.gather(*[process_batch(b) for b in wave], return_exceptions=True)

            for batch, r in zip(wave, results):
                if isinstance(r, Exception):
                    errors += len(batch)
                    logger.warning("[write-metadata] batch error: %s", r)
                    continue
                n_ok, failed, n_reread = r
                ok += n_ok
                errors += len(failed)
                reread += n_reread
                for src, why in failed.items():
                    if len(failures) < 50:
                        failures.append({"file": src, "error": why})

            elapsed = _time.time() - t_start
            done = sum(len(b) for b in batches[: wave_start + len(wave)])
            rate = done / elapsed if elapsed > 0 else 0
            eta = int((total - done) / rate) if rate > 0 else 0
            await task_manager.update_task(
                task_id,
                message=(
                    f"Writing… {done:,}/{total:,} "
                    f"({rate:.0f}/s · ETA {fmt_eta(eta)})"
                ),
            )

        t_total = _time.time() - t_start
        print(
            f"[write-metadata] Done: {ok:,} ok, {errors} failed, {reread} re-read first, "
            f"{len(batches)} batches, {t_total:.1f}s",
            flush=True,
        )
        message = f"Done: {ok:,} photos written ({t_total:.0f}s)"
        if reread:
            message += f"; {reread:,} changed on disk and were merged first"
        if errors:
            message += (f"; {errors:,} could not be written and are still pending "
                        f"(first: {failures[0]['file']} — {failures[0]['error']})" if failures
                        else f"; {errors:,} could not be written and are still pending")
        await task_manager.update_task(
            task_id,
            status="completed",
            message=message,
            progress={
                "ok": ok,
                "errors": errors,
                "reread": reread,
                "failures": failures,
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
