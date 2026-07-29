"""Duplicate detection: sha256 hash computation + duplicate group listing."""
from __future__ import annotations

import asyncio
import hashlib
import logging
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Query
from sqlalchemy import func, select, text

from fernkam.api.deps import DB
from fernkam.config import get_settings
from fernkam.db.models.photos import Photo

logger = logging.getLogger(__name__)
router = APIRouter()


# ── helpers ──────────────────────────────────────────────────────────────────

def _sha256_file(path: str) -> Optional[str]:
    """Compute SHA-256 of a file; return None on any error."""
    try:
        h = hashlib.sha256()
        with open(path, "rb") as fh:
            for chunk in iter(lambda: fh.read(65536), b""):
                h.update(chunk)
        return h.hexdigest()
    except Exception as exc:
        logger.debug("sha256 failed for %s: %s", path, exc)
        return None


# ── background hash computation ───────────────────────────────────────────────

@router.post("/compute-hashes", response_model=dict)
async def compute_missing_hashes(
    limit: int = Query(0, ge=0, description="Max photos to hash (0 = all)"),
) -> dict:
    """Background task: compute sha256 for photos where it is NULL.

    Returns a task_id immediately; poll /api/sync/tasks/<task_id> for progress.
    Typical throughput: 300–1000 files/s on SSD depending on file size.
    """
    from fernkam.task_manager import task_manager
    from fernkam.db.session import async_session_factory as _factory

    task_id = await task_manager.create_task(
        "compute_hashes",
        "Computing sha256 hashes for unprocessed photos…",
    )

    async def _run() -> None:
        done = 0
        errors = 0
        try:
            async with _factory() as bg_db:
                q = (
                    select(Photo.id, Photo.album_path, Photo.filename)
                    .where(Photo.sha256.is_(None))
                    .where(Photo.status == 1)
                    .order_by(Photo.id)
                )
                if limit:
                    q = q.limit(limit)
                rows = (await bg_db.execute(q)).fetchall()
                total = len(rows)
                lib_root = get_settings().library_root

                for i, (pid, album, fname) in enumerate(rows):
                    full = str(Path(lib_root) / album.lstrip("/") / fname)
                    digest = await asyncio.get_event_loop().run_in_executor(None, _sha256_file, full)
                    if digest:
                        await bg_db.execute(
                            text("UPDATE photos SET sha256 = :h WHERE id = :id"),
                            {"h": digest, "id": pid},
                        )
                        done += 1
                    else:
                        errors += 1

                    if (i + 1) % 500 == 0:
                        await bg_db.commit()
                        await task_manager.update_task(
                            task_id,
                            message=f"Hashed {done}/{total} ({errors} errors)…",
                            progress={"done": done, "errors": errors, "total": total},
                        )

                await bg_db.commit()
                await task_manager.update_task(
                    task_id,
                    status="completed",
                    message=f"Done: {done} hashed, {errors} errors (of {total} total)",
                    progress={"done": done, "errors": errors, "total": total},
                )
        except Exception as exc:
            await task_manager.update_task(task_id, status="failed", message=str(exc))

    asyncio.create_task(_run())
    return {"task_id": task_id, "status": "started"}


# ── duplicate listing ─────────────────────────────────────────────────────────

@router.get("/groups", response_model=dict)
async def list_duplicate_groups(
    db: DB,
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=500),
    min_count: int = Query(2, ge=2),
) -> dict:
    """Return groups of photos that share the same sha256 (exact duplicates).

    Each group contains all Photo ids with that hash, sorted by imported_at so
    the "original" (oldest import) is first.
    """
    offset = (page - 1) * page_size

    # Single GROUP BY pass instead of two — the previous version scanned/grouped
    # the whole photos table twice per request (once for the total group count,
    # once more for the page of rows). COUNT(*) OVER() gets both from one scan.
    page_result = (await db.execute(text("""
        WITH dup_groups AS (
            SELECT sha256, COUNT(*) AS cnt
            FROM photos
            WHERE sha256 IS NOT NULL AND status = 1
            GROUP BY sha256
            HAVING COUNT(*) >= :min_count
        )
        SELECT sha256, cnt, COUNT(*) OVER() AS total_groups
        FROM dup_groups
        ORDER BY cnt DESC, sha256
        LIMIT :lim OFFSET :off
    """), {"min_count": min_count, "lim": page_size, "off": offset})).fetchall()

    groups_rows = [(r.sha256, r.cnt) for r in page_result]
    total_groups = page_result[0].total_groups if page_result else 0
    # COUNT(*) OVER() reflects only this page's window when the page is empty
    # (e.g. requesting an offset past the end) — fall back to a real count.
    if not page_result and offset > 0:
        total_groups = (await db.execute(text("""
            SELECT COUNT(*) FROM (
                SELECT sha256 FROM photos
                WHERE sha256 IS NOT NULL AND status = 1
                GROUP BY sha256 HAVING COUNT(*) >= :min_count
            ) g
        """), {"min_count": min_count})).scalar_one()

    # Single batched fetch for every group on this page instead of one SELECT
    # per group (was up to `page_size` round-trips per request).
    shas = [sha for sha, _cnt in groups_rows]
    by_sha: dict[str, list] = {}
    if shas:
        photo_rows = (await db.execute(
            select(Photo.sha256, Photo.id, Photo.filename, Photo.album_path,
                   Photo.taken_at, Photo.file_size)
            .where(Photo.sha256.in_(shas))
            .where(Photo.status == 1)
            .order_by(Photo.sha256, Photo.imported_at.asc())
        )).fetchall()
        for r in photo_rows:
            by_sha.setdefault(r.sha256, []).append(r)

    groups = []
    for sha, cnt in groups_rows:
        groups.append({
            "sha256": sha,
            "count": cnt,
            "photos": [
                {
                    "id": r.id,
                    "filename": r.filename,
                    "album_path": r.album_path,
                    "taken_at": r.taken_at.isoformat() if r.taken_at else None,
                    "file_size": r.file_size,
                }
                for r in by_sha.get(sha, [])
            ],
        })

    return {
        "total_groups": total_groups,
        "page": page,
        "page_size": page_size,
        "groups": groups,
    }


@router.get("/stats", response_model=dict)
async def dedup_stats(db: DB) -> dict:
    """Summary: how many photos have sha256, how many duplicates exist."""
    row = (await db.execute(text("""
        SELECT
            COUNT(*) FILTER (WHERE sha256 IS NOT NULL) AS hashed,
            COUNT(*) FILTER (WHERE sha256 IS NULL)     AS unhashed,
            COUNT(*) AS total
        FROM photos WHERE status = 1
    """))).fetchone()

    dup_row = (await db.execute(text("""
        SELECT COUNT(*) FROM (
            SELECT sha256 FROM photos WHERE sha256 IS NOT NULL AND status = 1
            GROUP BY sha256 HAVING COUNT(*) > 1
        ) g
    """))).scalar_one()

    wasted_row = (await db.execute(text("""
        SELECT COALESCE(SUM(extra_size), 0) FROM (
            SELECT sha256, (COUNT(*) - 1) * MAX(file_size) AS extra_size
            FROM photos WHERE sha256 IS NOT NULL AND status = 1
            GROUP BY sha256 HAVING COUNT(*) > 1
        ) g
    """))).scalar_one()

    return {
        "hashed": row.hashed,
        "unhashed": row.unhashed,
        "total": row.total,
        "duplicate_groups": dup_row,
        "wasted_bytes": int(wasted_row or 0),
    }
