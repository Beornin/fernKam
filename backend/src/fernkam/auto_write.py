"""Hand edits reach the file by themselves, the way digiKam writes without "lazy synchronization".

An edit to a photo (a rating, a tag, a caption, a face name, the Flickr tag...) queues it; once the
photo has been left alone for DELAY seconds it is written exactly as DB → Files would
(sync.metadata.write_photos), one file at a time, since every write rewrites the whole file and the
library is on hard drives. Quick edits to one photo become one write.

The dirty flag stays the record: a photo not written yet (fernKam closed, a file job running, a file
busy in another program) is still pending for DB → Files. Bulk jobs (Tag Review, Learn all,
Portfolio keywords, tag renames) don't queue; they are written in batches by DB → Files.
"""
from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Iterable

logger = logging.getLogger(__name__)

DELAY = 8.0     # seconds after the last edit to a photo
MAX_IDS = 50    # an edit touching more photos than this is a bulk job, left to DB → Files

_due: dict[int, float] = {}
_writing: int | None = None
_task: asyncio.Task | None = None


def queue(photo_ids: Iterable[int]) -> None:
    """Write these photos' files a few seconds from now (after their edit is committed)."""
    ids = list(photo_ids)
    if not ids or len(ids) > MAX_IDS:
        return
    due = time.monotonic() + DELAY
    for i in ids:
        _due[i] = due
    global _task
    if _task is None or _task.done():
        try:
            _task = asyncio.get_running_loop().create_task(_run(), name="fernkam-auto-write")
        except RuntimeError:   # no event loop (a script): DB → Files writes it later
            pass


def is_queued(photo_id: int) -> bool:
    """Waiting for, or in the middle of, its automatic write."""
    return photo_id in _due or photo_id == _writing


async def _run() -> None:
    global _writing
    from fernkam.task_manager import TaskConflict, task_manager

    while _due:
        now = time.monotonic()
        ready = sorted(i for i, t in _due.items() if t <= now)
        if not ready:
            await asyncio.sleep(min(_due.values()) - now)
            continue
        try:
            task_manager.ensure_no_file_task()   # a scan or file job owns the disk now
        except TaskConflict:
            for i in ready:
                _due[i] = now + DELAY
            continue
        for pid in ready:
            if _due.get(pid, now) > time.monotonic():   # edited again meanwhile: wait for that one
                continue
            _writing = pid
            _due.pop(pid, None)
            try:
                await _write(pid)
            except Exception:  # noqa: BLE001 — it stays pending for DB → Files
                logger.warning("auto-write of photo %s failed", pid, exc_info=True)
            finally:
                _writing = None


async def _write(photo_id: int) -> None:
    from sqlalchemy import select
    from fernkam.api.routers.sync.metadata import WRITABLE, write_photos
    from fernkam.db.models.photos import Photo
    from fernkam.db.session import async_session_factory

    async with async_session_factory() as db:
        pending = (await db.execute(select(Photo.id).where(
            Photo.id == photo_id, Photo.file_sync_dirty == True, *WRITABLE))).first()  # noqa: E712
    if not pending:   # already written (DB → Files got there first), a video, or gone
        return
    n, failed, _ = await write_photos([photo_id])
    for src, why in failed.items():
        logger.warning("auto-write could not write %s: %s; it stays pending for DB → Files", src, why)
