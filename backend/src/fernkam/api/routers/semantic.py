"""Semantic search, tag propagation and visual near-duplicates (Roadmap phase 2).

All four features ride on one column — `photos.embedding_v`, the 512-d CLIP
vector — and one HNSW index. The text tower projects into the same space, so
"otter in snow" is just another vector to compare against.

Tag propagation deliberately *suggests* rather than writes: the library already
has 1,415 hand-curated tags across 55k photos, and that curation is the thing
worth protecting.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Optional

from fastapi import APIRouter, Body, HTTPException, Query
from sqlalchemy import text as _sql

from fernkam.api.deps import DB

logger = logging.getLogger(__name__)
router = APIRouter()

# Embedding reads the cached 480px thumbnail, not the original: CLIP crops to
# 224px anyway, so decoding a 100 MB RAW to feed it would be pure waste.
_THUMB_SIZE = "md"
_BATCH = 64


@router.get("/status")
async def embed_status(db: DB) -> dict:
    row = (await db.execute(_sql("""
        SELECT COUNT(*) FILTER (WHERE embedding_v IS NOT NULL) AS done,
               COUNT(*) FILTER (WHERE status = 1) AS total
        FROM photos
    """))).first()
    done, total = int(row[0] or 0), int(row[1] or 0)
    return {"embedded": done, "total": total, "remaining": max(0, total - done)}


@router.post("/embed")
async def embed_photos(db: DB, limit: Optional[int] = Query(None, ge=1)) -> dict:
    """Backfill CLIP embeddings for photos that lack one. Cancellable task."""
    from fernkam.task_manager import task_manager

    q = """
        SELECT id, album_path, filename FROM photos
        WHERE status = 1 AND embedding_v IS NULL AND media_type IN ('image', 'video')
        ORDER BY id
    """
    if limit:
        q += f" LIMIT {int(limit)}"
    rows = (await db.execute(_sql(q))).fetchall()
    if not rows:
        return {"task_id": None, "queued": 0, "message": "All photos already embedded"}

    task_id = await task_manager.create_task(
        "embed_photos", f"Queued {len(rows):,} photos for CLIP embedding…"
    )

    async def _run() -> None:
        import time
        import numpy as np
        from PIL import Image
        from fernkam.db.session import async_session_factory
        from fernkam import clip_embed as ce
        from fernkam.thumbnails import read_thumbnail_from_disk, photo_disk_path, generate_thumbnail_bytes

        loop = asyncio.get_event_loop()
        t0 = time.time()
        ok = skipped = 0
        total = len(rows)

        def _load(pid: int, album: str, fname: str):
            """Thumbnail first; fall back to generating one for photos the
            thumbnail backfill never reached. Returns a PIL image or None."""
            import io
            data = read_thumbnail_from_disk(pid, _THUMB_SIZE)
            if data is None:
                data = generate_thumbnail_bytes(photo_disk_path(album, fname), _THUMB_SIZE)
            if not data:
                return None
            try:
                return Image.open(io.BytesIO(data))
            except Exception:
                return None

        try:
            async with async_session_factory() as bdb:
                for start in range(0, total, _BATCH):
                    task = await task_manager.get_task(task_id)
                    if task and task.status == "cancelled":
                        break
                    chunk = rows[start:start + _BATCH]
                    imgs = await loop.run_in_executor(
                        None, lambda c=chunk: [_load(r[0], r[1], r[2]) for r in c]
                    )
                    idx = [i for i, im in enumerate(imgs) if im is not None]
                    skipped += len(chunk) - len(idx)
                    if idx:
                        vecs = await loop.run_in_executor(
                            None, ce.embed_images, [imgs[i] for i in idx]
                        )
                        params = [
                            {"pid": chunk[i][0], "v": ce.to_pgvector(v)}
                            for i, v in zip(idx, vecs) if v is not None
                        ]
                        if params:
                            await bdb.execute(_sql(
                                "UPDATE photos SET embedding_v = CAST(:v AS vector),"
                                " embedded_at = now() WHERE id = :pid"
                            ), params)
                            await bdb.commit()
                            ok += len(params)

                    done = min(start + _BATCH, total)
                    rate = done / max(time.time() - t0, 0.001)
                    eta = int((total - done) / rate) if rate > 0 else 0
                    await task_manager.update_task(
                        task_id,
                        message=f"Embedding… {done:,}/{total:,} ({ok:,} ok, {skipped} skipped · {rate:.0f}/s · ETA {eta}s)",
                        progress={"done": done, "total": total, "ok": ok, "skipped": skipped},
                    )

            await task_manager.update_task(
                task_id, status="completed",
                message=f"Embedded {ok:,} photos ({skipped} unreadable) in {time.time()-t0:.0f}s",
                progress={"done": total, "total": total, "ok": ok, "skipped": skipped},
            )
        except Exception as exc:  # noqa: BLE001
            logger.exception("embed task failed")
            await task_manager.update_task(task_id, status="failed", message=str(exc))

    asyncio.create_task(_run(), name=f"fernkam-embed-{task_id}")
    return {"task_id": task_id, "queued": len(rows), "message": "running"}


@router.get("/search")
async def semantic_search(
    db: DB,
    q: str = Query(..., min_length=1),
    limit: int = Query(100, ge=1, le=500),
    min_score: float = Query(0.20, ge=0.0, le=1.0),
) -> dict:
    """Free-text -> photos, via CLIP. Finds untagged photos by what is in them."""
    from fernkam import clip_embed as ce

    loop = asyncio.get_event_loop()
    vec = await loop.run_in_executor(None, ce.embed_text, q)
    rows = (await db.execute(_sql("""
        SELECT p.id, p.filename, p.album_path, p.taken_at, p.media_type, p.rating,
               1 - (p.embedding_v <=> CAST(:v AS vector)) AS score
        FROM photos p
        WHERE p.embedding_v IS NOT NULL AND p.status = 1
        ORDER BY p.embedding_v <=> CAST(:v AS vector)
        LIMIT :lim
    """), {"v": ce.to_pgvector(vec), "lim": limit})).fetchall()
    out = [
        {"id": r[0], "filename": r[1], "album_path": r[2],
         "taken_at": r[3].isoformat() if r[3] else None,
         "media_type": r[4], "rating": r[5], "score": round(float(r[6]), 4)}
        for r in rows if float(r[6]) >= min_score
    ]
    return {"query": q, "count": len(out), "results": out}


@router.get("/similar/{photo_id}")
async def similar_photos(
    db: DB,
    photo_id: int,
    limit: int = Query(50, ge=1, le=200),
    min_score: float = Query(0.80, ge=0.0, le=1.0),
) -> dict:
    """Visually similar photos — near-duplicates that sha256 cannot catch
    (re-exports, crops, different JPEG quality, resized copies)."""
    rows = (await db.execute(_sql("""
        WITH src AS (SELECT embedding_v FROM photos WHERE id = :pid AND embedding_v IS NOT NULL)
        SELECT p.id, p.filename, p.album_path, p.file_size, p.taken_at,
               1 - (p.embedding_v <=> src.embedding_v) AS score
        FROM photos p CROSS JOIN src
        WHERE p.embedding_v IS NOT NULL AND p.status = 1 AND p.id <> :pid
        ORDER BY p.embedding_v <=> src.embedding_v
        LIMIT :lim
    """), {"pid": photo_id, "lim": limit})).fetchall()
    if not rows:
        exists = (await db.execute(_sql(
            "SELECT embedding_v IS NOT NULL FROM photos WHERE id = :pid"
        ), {"pid": photo_id})).scalar()
        if exists is None:
            raise HTTPException(404, "Photo not found")
        if not exists:
            raise HTTPException(409, "Photo has no embedding yet — run /semantic/embed")
    return {
        "photo_id": photo_id,
        "results": [
            {"id": r[0], "filename": r[1], "album_path": r[2], "file_size": r[3],
             "taken_at": r[4].isoformat() if r[4] else None, "score": round(float(r[5]), 4)}
            for r in rows if float(r[5]) >= min_score
        ],
    }


@router.get("/suggest-tags/{photo_id}")
async def suggest_tags_for_photo(
    db: DB,
    photo_id: int,
    k: int = Query(20, ge=3, le=100),
    min_score: float = Query(0.75, ge=0.0, le=1.0),
    limit: int = Query(8, ge=1, le=30),
) -> dict:
    """Propose tags for one photo from its nearest already-tagged neighbours.

    This is the "actually smart" half of phase 2: it learns the existing
    taxonomy instead of imposing a generic label set. No second model — just a
    kNN query against the index built in 2.1, weighted by similarity so a very
    close neighbour outvotes several distant ones.
    """
    rows = (await db.execute(_sql("""
        WITH src AS (SELECT embedding_v FROM photos WHERE id = :pid AND embedding_v IS NOT NULL),
        nb AS (
            SELECT p.id, 1 - (p.embedding_v <=> src.embedding_v) AS score
            FROM photos p CROSS JOIN src
            WHERE p.embedding_v IS NOT NULL AND p.status = 1 AND p.id <> :pid
              AND EXISTS (SELECT 1 FROM photo_tags pt WHERE pt.photo_id = p.id)
            ORDER BY p.embedding_v <=> src.embedding_v
            LIMIT :k
        )
        SELECT t.id, t.name, t.path::text, SUM(nb.score) AS weight, COUNT(*) AS votes
        FROM nb
        JOIN photo_tags pt ON pt.photo_id = nb.id
        JOIN tags t ON t.id = pt.tag_id
        WHERE nb.score >= :min_score
          AND NOT EXISTS (SELECT 1 FROM photo_tags mine
                          WHERE mine.photo_id = :pid AND mine.tag_id = t.id)
        GROUP BY t.id, t.name, t.path
        ORDER BY weight DESC
        LIMIT :lim
    """), {"pid": photo_id, "k": k, "min_score": min_score, "lim": limit})).fetchall()
    return {
        "photo_id": photo_id,
        "suggestions": [
            {"tag_id": r[0], "name": r[1], "path": r[2],
             "weight": round(float(r[3]), 3), "votes": int(r[4])}
            for r in rows
        ],
    }


@router.post("/apply-tags")
async def apply_suggested_tags(
    db: DB,
    photo_ids: list[int] = Body(...),
    tag_ids: list[int] = Body(...),
) -> dict:
    """Attach the accepted tags. Explicit, because suggestions stay suggestions
    until a human says otherwise."""
    if not photo_ids or not tag_ids:
        return {"linked": 0}
    res = await db.execute(_sql("""
        INSERT INTO photo_tags (photo_id, tag_id)
        SELECT p, t FROM unnest(CAST(:pids AS int[])) p
        CROSS JOIN unnest(CAST(:tids AS int[])) t
        ON CONFLICT DO NOTHING
    """), {"pids": photo_ids, "tids": tag_ids})
    await db.execute(_sql(
        "UPDATE photos SET file_sync_dirty = true WHERE id = ANY(CAST(:pids AS int[]))"
    ), {"pids": photo_ids})
    await db.commit()
    return {"linked": res.rowcount or 0}


@router.get("/suggest-tags-bulk")
async def suggest_tags_bulk(
    db: DB,
    album_path: str = Query(..., min_length=1),
    k: int = Query(20, ge=3, le=100),
    min_score: float = Query(0.75, ge=0.0, le=1.0),
    per_photo: int = Query(5, ge=1, le=20),
    limit: int = Query(300, ge=1, le=2000),
    untagged_only: bool = Query(True),
) -> dict:
    """Tag suggestions for a whole folder — Roadmap 3.3.

    Same kNN as /suggest-tags, run across every photo in a folder in one query
    instead of one round-trip per photo. Intended for freshly imported wildlife
    folders: the curated portfolio is the densest tagged region of the library,
    so new imports land near it and inherit its vocabulary.

    Returns per-photo suggestions plus a folder-level rollup, so an obvious
    subject for the whole shoot can be accepted in a single action.
    """
    tag_filter = (
        "AND NOT EXISTS (SELECT 1 FROM photo_tags pt WHERE pt.photo_id = p.id)"
        if untagged_only else ""
    )
    rows = (await db.execute(_sql(f"""
        WITH src AS (
            SELECT p.id, p.filename, p.embedding_v
            FROM photos p
            WHERE p.status = 1 AND p.embedding_v IS NOT NULL
              AND p.album_path LIKE :ap {tag_filter}
            ORDER BY p.id
            LIMIT :lim
        ),
        nb AS (
            SELECT s.id AS src_id, s.filename, n.id AS nb_id,
                   1 - (n.embedding_v <=> s.embedding_v) AS score
            FROM src s
            CROSS JOIN LATERAL (
                SELECT o.id, o.embedding_v FROM photos o
                WHERE o.embedding_v IS NOT NULL AND o.status = 1 AND o.id <> s.id
                  AND EXISTS (SELECT 1 FROM photo_tags pt WHERE pt.photo_id = o.id)
                ORDER BY o.embedding_v <=> s.embedding_v
                LIMIT :k
            ) n
        ),
        scored AS (
            SELECT nb.src_id, nb.filename, t.id AS tag_id, t.name, t.path::text AS path,
                   SUM(nb.score) AS weight, COUNT(*) AS votes,
                   ROW_NUMBER() OVER (PARTITION BY nb.src_id ORDER BY SUM(nb.score) DESC) AS rn
            FROM nb
            JOIN photo_tags pt ON pt.photo_id = nb.nb_id
            JOIN tags t ON t.id = pt.tag_id
            WHERE nb.score >= :min_score
            GROUP BY nb.src_id, nb.filename, t.id, t.name, t.path
        )
        SELECT src_id, filename, tag_id, name, path, weight, votes
        FROM scored WHERE rn <= :per ORDER BY src_id, weight DESC
    """), {"ap": f"{album_path}%", "k": k, "min_score": min_score,
           "per": per_photo, "lim": limit})).fetchall()

    photos: dict = {}
    rollup: dict = {}
    for src_id, filename, tag_id, name, path, weight, votes in rows:
        photos.setdefault(src_id, {"photo_id": src_id, "filename": filename, "suggestions": []})
        photos[src_id]["suggestions"].append({
            "tag_id": tag_id, "name": name, "path": path,
            "weight": round(float(weight), 3), "votes": int(votes),
        })
        agg = rollup.setdefault(tag_id, {"tag_id": tag_id, "name": name, "path": path,
                                         "photos": 0, "weight": 0.0})
        agg["photos"] += 1
        agg["weight"] += float(weight)

    top = sorted(rollup.values(), key=lambda r: r["photos"], reverse=True)[:15]
    for r in top:
        r["weight"] = round(r["weight"], 2)
    return {
        "album_path": album_path,
        "photos_with_suggestions": len(photos),
        "folder_rollup": top,
        "photos": list(photos.values()),
    }
