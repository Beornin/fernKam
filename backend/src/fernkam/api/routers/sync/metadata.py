"""Metadata sync endpoints: DB↔file XMP sync, full EXIF refresh, write-back."""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Body
from sqlalchemy import select, update
from sqlalchemy.orm import selectinload

from fernkam.api.deps import DB
from fernkam.db.models.photos import Face, Photo, PhotoTag, Tag

logger = logging.getLogger(__name__)
router = APIRouter()


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
