"""Reverse geocoding: GPS -> country / state / city (offline via reverse_geocoder)."""
from __future__ import annotations

import asyncio
import logging
from typing import Optional

from fastapi import APIRouter, Query
from sqlalchemy import select, text

from fernkam.api.deps import DB
from fernkam.db.models.photos import Photo

logger = logging.getLogger(__name__)
router = APIRouter()

_rg = None


def _get_rg():
    global _rg
    if _rg is None:
        import reverse_geocoder as rg
        _rg = rg
    return _rg


def _lookup_batch(coords: list[tuple[float, float]]) -> list[dict]:
    rg = _get_rg()
    return rg.search(coords, mode=1, verbose=False)


@router.get("/stats", response_model=dict)
async def geocode_stats(db: DB) -> dict:
    row = (await db.execute(text("""
        SELECT
            COUNT(*) FILTER (WHERE latitude IS NOT NULL)                           AS with_gps,
            COUNT(*) FILTER (WHERE latitude IS NOT NULL AND city IS NOT NULL)      AS geocoded,
            COUNT(*) FILTER (WHERE latitude IS NOT NULL AND city IS NULL)          AS pending
        FROM photos WHERE status = 1
    """))).fetchone()
    return {"with_gps": row.with_gps, "geocoded": row.geocoded, "pending": row.pending}


@router.post("/run", response_model=dict)
async def run_geocoding(
    limit: int = Query(0, ge=0, description="Max photos to geocode (0=all pending)"),
    force: bool = Query(False, description="Re-geocode already geocoded photos"),
) -> dict:
    """Offline reverse geocoding via bundled GeoNames dataset (~100K lookups/s)."""
    from fernkam.task_manager import task_manager
    from fernkam.db.session import async_session_factory as _factory

    task_id = await task_manager.create_task("geocode", "Reverse geocoding GPS coordinates…")

    async def _run() -> None:
        done = 0
        try:
            async with _factory() as bg_db:
                q = (
                    select(Photo.id, Photo.latitude, Photo.longitude)
                    .where(Photo.status == 1)
                    .where(Photo.latitude.is_not(None))
                    .order_by(Photo.id)
                )
                if not force:
                    q = q.where(Photo.city.is_(None))
                if limit:
                    q = q.limit(limit)

                rows = (await bg_db.execute(q)).fetchall()
                total = len(rows)
                BATCH = 5000

                for i in range(0, total, BATCH):
                    batch = rows[i:i + BATCH]
                    coords = [(float(r.latitude), float(r.longitude)) for r in batch]
                    results = await asyncio.get_event_loop().run_in_executor(None, _lookup_batch, coords)

                    # Single executemany-style call per batch (was one round-trip
                    # per photo — up to 120k individual UPDATEs).
                    params = [
                        {"cc": geo.get("cc", ""), "state": geo.get("admin1", ""),
                         "city": geo.get("name", ""), "id": row.id}
                        for row, geo in zip(batch, results)
                    ]
                    await bg_db.execute(text(
                        "UPDATE photos SET country_code=:cc, state=:state, city=:city WHERE id=:id"
                    ), params)
                    done += len(batch)

                    await bg_db.commit()
                    await task_manager.update_task(
                        task_id, message=f"Geocoded {done}/{total}…",
                        progress={"done": done, "total": total},
                    )

                await task_manager.update_task(
                    task_id, status="completed",
                    message=f"Done: {done} photos geocoded",
                    progress={"done": done, "total": total},
                )
        except Exception as exc:
            logger.exception("geocode task failed")
            await task_manager.update_task(task_id, status="failed", message=str(exc))

    asyncio.create_task(_run())
    return {"task_id": task_id, "status": "started"}


@router.get("/countries", response_model=list[dict])
async def list_countries(db: DB) -> list[dict]:
    """Distinct country codes that have geocoded photos, sorted by count desc."""
    rows = (await db.execute(text("""
        SELECT country_code, state, COUNT(*) AS photo_count
        FROM photos
        WHERE status = 1 AND country_code IS NOT NULL AND country_code <> ''
        GROUP BY country_code, state
        ORDER BY photo_count DESC
        LIMIT 500
    """))).fetchall()
    return [{"country_code": r.country_code, "state": r.state, "photo_count": r.photo_count}
            for r in rows]
