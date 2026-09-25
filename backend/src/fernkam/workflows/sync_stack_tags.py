"""
Detect RAW/JPG stacks across the library, then union-sync tags/rating/color
label across every stack's members (writing merged metadata back to each
member's file via XMP).
"""
from __future__ import annotations

import asyncio
import time
from typing import Optional

from fernkam.workflows.shared import format_elapsed


def run(album_path: Optional[str] = None, dry_run: bool = True) -> None:
    """dry_run defaults to True — the sync half writes XMP into every member
    file, so it is opt-in. The detect half is read-only and always runs."""
    start = time.perf_counter()
    asyncio.run(_run_async(album_path, dry_run))
    print(f"Process took: {format_elapsed(start)}")


async def _run_async(album_path: Optional[str], dry_run: bool = True) -> None:
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
    from sqlalchemy.pool import NullPool
    from fernkam.config import get_settings

    # run() executes in a worker thread under its own asyncio.run() loop (see
    # api/routers/workflows.py). The app's shared engine pools asyncpg
    # connections bound to the server's loop, and touching them from this loop
    # fails with "got Future attached to a different loop" — so this run gets
    # its own unpooled engine, disposed before the loop closes.
    engine = create_async_engine(get_settings().pg_url, poolclass=NullPool)
    try:
        await _sync(async_sessionmaker(engine, expire_on_commit=False), album_path, dry_run)
    finally:
        await engine.dispose()


async def _sync(async_session_factory, album_path: Optional[str], dry_run: bool) -> None:
    from fernkam.db.models.photos import PhotoStack
    from fernkam.services.stacks import detect_stacks
    from sqlalchemy import select

    async with async_session_factory() as db:
        print("Detecting stacks…")
        stats = await detect_stacks(db, album_path=album_path)
        print(
            f"Detected: {stats['raw_photos_scanned']} RAW photos scanned, "
            f"{stats['stacks_created']} created, {stats['stacks_updated']} updated, "
            f"{stats['stacks_removed']} removed, {stats['photos_grouped']} photos grouped."
        )

        q = select(PhotoStack.id)
        if album_path:
            clean_album_path = album_path.lstrip("/")
            q = q.where(PhotoStack.album_path.like(f"{clean_album_path}%"))
        stack_ids = [r[0] for r in (await db.execute(q)).fetchall()]

    if dry_run:
        print(f"DRY RUN — {len(stack_ids)} stack(s) would have tags/rating/colour "
              f"unioned and written back to each member's XMP. Nothing was written.")
        return

    print(f"Syncing tags for {len(stack_ids)} stacks…")
    from fernkam.api.routers.stacks import sync_stack_tags as _sync_one_stack

    synced = 0
    errors = 0
    for i, stack_id in enumerate(stack_ids, start=1):
        async with async_session_factory() as db:
            try:
                result = await _sync_one_stack(stack_id, db)
                synced += result.get("synced", 0)
                errors += result.get("errors", 0)
            except Exception as e:
                print(f"ERROR syncing stack {stack_id}: {e}")
                errors += 1
        if i % 25 == 0 or i == len(stack_ids):
            print(f"  …{i}/{len(stack_ids)} stacks processed")

    print(f"Done: {synced} files synced, {errors} errors across {len(stack_ids)} stacks.")


if __name__ == "__main__":
    run()
