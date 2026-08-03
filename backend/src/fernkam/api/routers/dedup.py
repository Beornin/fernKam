"""Duplicate detection: sha256 hash computation + duplicate group listing."""
from __future__ import annotations

import asyncio
import hashlib
import logging
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Query
from sqlalchemy import func, select, text, update as sa_update

from fernkam.api.deps import DB
from fernkam.config import get_settings
from fernkam.db.models.photos import Photo, PhotoTag, Tag

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


def _dedup_tier(album_path: str) -> int:
    """Folder-priority tier for auto-clean: 0 = staging (delete first),
    1 = the default chronological archive, 2 = any manually-organized
    (special-event) folder — always outranks the other two."""
    settings = get_settings()
    ap = (album_path or "").lstrip("/")
    staging = [s.strip() for s in settings.dedup_staging_folders.split(",") if s.strip()]
    for prefix in staging:
        if ap == prefix or ap.startswith(prefix + "/"):
            return 0
    archive = settings.dedup_archive_folder.strip()
    if archive and (ap == archive or ap.startswith(archive + "/")):
        return 1
    return 2


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
                   Photo.taken_at, Photo.file_size, Photo.media_type)
            .where(Photo.sha256.in_(shas))
            .where(Photo.status == 1)
            .order_by(Photo.sha256, Photo.imported_at.asc())
        )).fetchall()
        for r in photo_rows:
            by_sha.setdefault(r.sha256, []).append(r)

    groups = []
    for sha, cnt in groups_rows:
        # Highest folder-priority tier first (matches the auto-clean "keep"
        # choice). Python's sort is stable, and the SQL query above already
        # ordered by imported_at ascending, so that stays the tie-break
        # within a tier — the "keep" marker in the manual list now agrees
        # with what Auto-Clean would actually do.
        photos_sorted = sorted(by_sha.get(sha, []), key=lambda r: -_dedup_tier(r.album_path))
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
                    "media_type": r.media_type,
                    "tier": _dedup_tier(r.album_path),
                }
                for r in photos_sorted
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


# ── folder-priority auto-clean ──────────────────────────────────────────────
#
# A duplicate group only gets an auto-clean decision when its copies span
# *different* folder-priority tiers (see _dedup_tier). Within a group, every
# copy sitting in a lower tier than the group's highest tier present is a
# delete candidate — unless it's part of a photo stack, or it has confirmed
# face tags the surviving (kept) copies don't, in which case it's pulled out
# and surfaced as "skipped" for manual review instead. Groups where every
# copy shares the same tier are left alone entirely (no unambiguous winner).

async def _compute_auto_clean_plan(db) -> dict:
    """Read-only: derive the full auto-clean plan. Shared by the preview and
    apply endpoints so the two can never drift apart — apply re-derives this
    from scratch rather than trusting a client-supplied id list, so it can't
    go stale between when you reviewed it and when you confirmed it."""
    group_rows = (await db.execute(text("""
        SELECT sha256 FROM photos
        WHERE sha256 IS NOT NULL AND status = 1
        GROUP BY sha256 HAVING COUNT(*) >= 2
    """))).fetchall()
    shas = [r.sha256 for r in group_rows]
    if not shas:
        return {"groups": [], "skipped": [], "total_delete_count": 0, "total_reclaim_bytes": 0}

    photo_rows = (await db.execute(
        select(
            Photo.id, Photo.sha256, Photo.album_path, Photo.filename,
            Photo.file_size, Photo.media_type, Photo.rating, Photo.color_label,
            Photo.title, Photo.caption, Photo.stack_id,
        )
        .where(Photo.sha256.in_(shas))
        .where(Photo.status == 1)
        .order_by(Photo.sha256, Photo.imported_at.asc())
    )).fetchall()
    all_ids = [r.id for r in photo_rows]

    confirmed_face_counts: dict[int, int] = {}
    if all_ids:
        rows = (await db.execute(text("""
            SELECT photo_id, COUNT(*) FILTER (WHERE status = 'confirmed') AS confirmed_count
            FROM faces WHERE photo_id = ANY(:ids) GROUP BY photo_id
        """), {"ids": all_ids})).fetchall()
        confirmed_face_counts = {r.photo_id: r.confirmed_count for r in rows}

    tag_names_by_photo: dict[int, set] = {}
    if all_ids:
        rows = (await db.execute(text("""
            SELECT pt.photo_id, t.name FROM photo_tags pt
            JOIN tags t ON t.id = pt.tag_id
            WHERE pt.photo_id = ANY(:ids)
        """), {"ids": all_ids})).fetchall()
        for r in rows:
            tag_names_by_photo.setdefault(r.photo_id, set()).add(r.name)

    by_sha: dict[str, list] = {}
    for r in photo_rows:
        by_sha.setdefault(r.sha256, []).append(r)

    groups_out: list[dict] = []
    skipped_out: list[dict] = []
    total_delete = 0
    total_bytes = 0

    for sha, photos in by_sha.items():
        tiers = {p.id: _dedup_tier(p.album_path) for p in photos}
        max_tier = max(tiers.values())
        keepers = [p for p in photos if tiers[p.id] == max_tier]
        candidates = [p for p in photos if tiers[p.id] < max_tier]
        if not candidates:
            continue  # every copy is in the same tier — no unambiguous winner

        keeper_has_confirmed = any(confirmed_face_counts.get(k.id, 0) > 0 for k in keepers)

        delete_list = []
        for c in candidates:
            if c.stack_id is not None:
                skipped_out.append({
                    "id": c.id, "sha256": sha, "filename": c.filename,
                    "album_path": c.album_path, "media_type": c.media_type,
                    "reason": "part of a photo stack",
                })
                continue
            if confirmed_face_counts.get(c.id, 0) > 0 and not keeper_has_confirmed:
                skipped_out.append({
                    "id": c.id, "sha256": sha, "filename": c.filename,
                    "album_path": c.album_path, "media_type": c.media_type,
                    "reason": "has confirmed face tags the kept copy doesn't",
                })
                continue
            delete_list.append(c)

        if not delete_list:
            continue

        # Metadata to propagate onto every surviving copy before deleting —
        # never silently lose a rating/label/tag because it landed on the
        # copy that happened to be sorted into the lower-priority folder.
        merged_rating = max([k.rating for k in keepers] + [c.rating for c in delete_list])
        merged_color = next((k.color_label for k in keepers if k.color_label), None)
        if merged_color is None:
            merged_color = next((c.color_label for c in delete_list if c.color_label), None)
        merged_title = next((k.title for k in keepers if k.title), None) \
            or next((c.title for c in delete_list if c.title), None)
        merged_caption = next((k.caption for k in keepers if k.caption), None) \
            or next((c.caption for c in delete_list if c.caption), None)
        merged_tags: set = set()
        for p in keepers + delete_list:
            merged_tags |= tag_names_by_photo.get(p.id, set())

        total_delete += len(delete_list)
        total_bytes += sum((c.file_size or 0) for c in delete_list)

        groups_out.append({
            "sha256": sha,
            "keep": [
                {"id": k.id, "filename": k.filename, "album_path": k.album_path,
                 "file_size": k.file_size, "media_type": k.media_type, "tier": max_tier}
                for k in keepers
            ],
            "delete": [
                {"id": c.id, "filename": c.filename, "album_path": c.album_path,
                 "file_size": c.file_size, "media_type": c.media_type, "tier": tiers[c.id]}
                for c in delete_list
            ],
            "merge": {
                "rating": merged_rating,
                "color_label": merged_color,
                "title": merged_title,
                "caption": merged_caption,
                "tags": sorted(merged_tags),
            },
        })

    return {
        "groups": groups_out,
        "skipped": skipped_out,
        "total_delete_count": total_delete,
        "total_reclaim_bytes": total_bytes,
    }


@router.get("/auto-clean/preview", response_model=dict)
async def auto_clean_preview(db: DB) -> dict:
    """Read-only preview of the folder-priority auto-clean plan — nothing is
    changed. Review this before calling /auto-clean/apply."""
    return await _compute_auto_clean_plan(db)


@router.post("/auto-clean/apply", response_model=dict)
async def auto_clean_apply() -> dict:
    """Re-derive the auto-clean plan and execute it: merge metadata onto
    surviving copies, then move every redundant copy to the Recycle Bin
    (same recoverable trash as the manual duplicate list).

    Runs as a background task — poll /api/sync/tasks/<task_id> for progress.
    """
    from fernkam.task_manager import task_manager
    from fernkam.db.session import async_session_factory as _factory
    from fernkam.thumbnails import photo_disk_path

    task_id = await task_manager.create_task(
        "dedup_auto_clean", "Computing duplicate auto-clean plan…"
    )

    async def _run() -> None:
        deleted = 0
        merged_photos = 0
        errors = 0
        freed_bytes = 0
        try:
            async with _factory() as bg_db:
                plan = await _compute_auto_clean_plan(bg_db)
                groups = plan["groups"]
                total = sum(len(g["delete"]) for g in groups)
                if not total:
                    await task_manager.update_task(
                        task_id, status="completed",
                        message="Nothing to clean — no duplicate group currently matches the folder-priority rule.",
                        progress={"done": 0, "total": 0},
                    )
                    return

                # ── Phase 1: merge metadata onto survivors (batched) ──
                for group in groups:
                    keep_ids = [k["id"] for k in group["keep"]]
                    if not keep_ids:
                        continue
                    merge = group["merge"]
                    upd: dict = {}
                    if merge["rating"] is not None:
                        upd["rating"] = merge["rating"]
                    if merge["color_label"]:
                        upd["color_label"] = merge["color_label"]
                    if merge["title"]:
                        upd["title"] = merge["title"]
                    if merge["caption"]:
                        upd["caption"] = merge["caption"]
                    if upd:
                        await bg_db.execute(
                            sa_update(Photo).where(Photo.id.in_(keep_ids)).values(**upd)
                        )
                        merged_photos += len(keep_ids)

                all_tag_names = {name for g in groups for name in g["merge"]["tags"]}
                if all_tag_names:
                    tag_name_to_id = dict((await bg_db.execute(
                        select(Tag.name, Tag.id).where(Tag.name.in_(all_tag_names))
                    )).all())
                    tag_pairs = [
                        {"pid": pid, "tid": tag_name_to_id[name]}
                        for g in groups
                        for name in g["merge"]["tags"] if name in tag_name_to_id
                        for pid in (k["id"] for k in g["keep"])
                    ]
                    if tag_pairs:
                        await bg_db.execute(text(
                            "INSERT INTO photo_tags (photo_id, tag_id) VALUES (:pid, :tid) "
                            "ON CONFLICT DO NOTHING"
                        ), tag_pairs)
                await bg_db.commit()

                # ── Phase 2: trash each redundant copy ──
                loop = asyncio.get_event_loop()
                for group in groups:
                    for cand in group["delete"]:
                        row = (await bg_db.execute(
                            select(Photo).where(Photo.id == cand["id"])
                        )).scalar_one_or_none()
                        if not row:
                            continue
                        src = photo_disk_path(row.album_path, row.filename)
                        try:
                            if src.exists():
                                from send2trash import send2trash
                                await loop.run_in_executor(None, send2trash, str(src))
                            await bg_db.execute(
                                sa_update(Photo).where(Photo.id == row.id).values(status=0)
                            )
                            await bg_db.commit()
                            deleted += 1
                            freed_bytes += row.file_size or 0
                        except Exception as exc:
                            logger.warning("auto-clean trash failed for %s: %s", src, exc)
                            errors += 1

                        if (deleted + errors) % 25 == 0:
                            await task_manager.update_task(
                                task_id,
                                message=f"Cleaned {deleted}/{total} ({errors} errors)…",
                                progress={"done": deleted + errors, "total": total},
                            )

                await task_manager.update_task(
                    task_id, status="completed",
                    message=(
                        f"Done: {deleted} duplicate file(s) trashed, "
                        f"{merged_photos} kept-copy update(s) applied, "
                        f"{errors} errors ({freed_bytes:,} bytes freed)."
                    ),
                    progress={"done": deleted + errors, "total": total,
                              "deleted": deleted, "errors": errors, "freed_bytes": freed_bytes},
                )
        except Exception as exc:
            logger.exception("auto-clean task failed")
            await task_manager.update_task(task_id, status="failed", message=str(exc))

    asyncio.create_task(_run())
    return {"task_id": task_id, "status": "started"}
