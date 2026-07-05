"""Debug / DBA endpoints — EXPLAIN ANALYZE on hot queries.

Phase 0B verification: validate that indexes are hit and date filters are sargable.
Only enabled when FERNKAM_DEBUG=true (default: off).
"""
from __future__ import annotations

from datetime import date
from typing import Optional

from fastapi import APIRouter, HTTPException, Query
from sqlalchemy import func, select, text

from fernkam.api.deps import DB
from fernkam.config import get_settings
from fernkam.db.models.photos import Photo
from fernkam.services.photo_query import PhotoFilters, build_photo_query, count_photos

router = APIRouter()


def _require_debug():
    if not getattr(get_settings(), "debug", False):
        raise HTTPException(403, "Set FERNKAM_DEBUG=true to enable debug endpoints")


@router.get("/explain/photos")
async def explain_photos(
    db: DB,
    album_path: Optional[str] = Query(None),
    tag_id: Optional[int] = Query(None),
    person_tag_id: Optional[int] = Query(None),
    rating_min: Optional[int] = Query(None),
    media_type: Optional[str] = Query(None),
    has_gps: Optional[bool] = Query(None),
    has_faces: Optional[bool] = Query(None),
    no_date: Optional[bool] = Query(None),
    search: Optional[str] = Query(None),
    date_from: Optional[date] = Query(None),
    date_to: Optional[date] = Query(None),
    country_code: Optional[str] = Query(None),
    query: str = Query("count", description="count | list — which query to explain"),
    analyze: bool = Query(True, description="Include ANALYZE (actually executes the query)"),
) -> dict:
    """Run EXPLAIN (ANALYZE, BUFFERS) on the main photo query.

    Returns the Postgres query plan so you can verify index usage and sargability.
    Enable with FERNKAM_DEBUG=true.
    """
    _require_debug()
    filters = PhotoFilters(
        album_path=album_path, tag_id=tag_id, person_tag_id=person_tag_id,
        rating_min=rating_min, media_type=media_type,
        has_gps=has_gps, has_faces=has_faces, no_date=no_date,
        search=search, date_from=date_from, date_to=date_to,
        country_code=country_code,
    )
    base_q = await build_photo_query(filters, db)

    if query == "count":
        target_q = select(func.count()).select_from(base_q.subquery())
    else:
        target_q = base_q.order_by(Photo.taken_at.desc().nulls_last()).limit(200)

    opts = "ANALYZE, BUFFERS, FORMAT TEXT" if analyze else "FORMAT TEXT"
    compiled = target_q.compile(db.bind, compile_kwargs={"literal_binds": True})
    sql_str = str(compiled)

    rows = (await db.execute(text(f"EXPLAIN ({opts}) {sql_str}"))).fetchall()
    plan_lines = [r[0] for r in rows]
    return {
        "query_type": query,
        "analyze": analyze,
        "filters": {k: v for k, v in filters.__dict__.items() if v is not None},
        "plan": plan_lines,
    }


@router.get("/explain/face-sweep")
async def explain_face_sweep(
    db: DB,
    analyze: bool = Query(False),
) -> dict:
    """EXPLAIN the HNSW kNN LATERAL query used in the auto-confirm sweep."""
    _require_debug()
    from fernkam.config import get_settings as _gs
    s = _gs()
    sql = text(f"""
        EXPLAIN {'ANALYZE, BUFFERS,' if analyze else ''} FORMAT TEXT
        SELECT u.id, 1 - (u.embedding_v <=> c.embedding_v) AS score
        FROM (
            SELECT id, embedding_v FROM faces
            WHERE status IN ('unconfirmed','suggested') AND embedding_v IS NOT NULL
            LIMIT 100
        ) u
        CROSS JOIN LATERAL (
            SELECT embedding_v FROM faces
            WHERE status = 'confirmed' AND embedding_v IS NOT NULL
            ORDER BY embedding_v <=> u.embedding_v
            LIMIT {s.knn_k}
        ) c
    """)
    rows = (await db.execute(sql)).fetchall()
    return {"plan": [r[0] for r in rows], "knn_k": s.knn_k}


@router.get("/cache/counts")
async def cache_status() -> dict:
    """Show current state of the photo count TTL cache."""
    _require_debug()
    import time as _t
    from fernkam.services.photo_query import _COUNT_CACHE, _COUNT_TTL
    now = _t.monotonic()
    return {
        "ttl_seconds": _COUNT_TTL,
        "entries": [
            {"key": k, "count": v[0], "expires_in": round(v[1] - now, 1)}
            for k, v in _COUNT_CACHE.items()
        ],
    }
