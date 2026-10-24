"""Reverse geocoding: GPS -> country / state / city (offline via reverse_geocoder) and, in the US,
county by boundary (us_counties)."""
from __future__ import annotations

import asyncio
import logging

from fastapi import APIRouter, Query
from sqlalchemy import false, select, text

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


def _county(geo: dict, hit, with_counties: bool):
    """The county-level place: in the US the boundary's county; abroad the nearest town's district
    (GeoNames admin2, e.g. "Cotswold District"); "" when there is none; NULL when the US boundaries
    aren't available yet, so the next run tries again."""
    if hit:
        return hit[0]
    if geo.get("cc") != "US":
        return geo.get("admin2") or ""
    return "" if with_counties else None


async def geocode_pending(db, force: bool = False, limit: int = 0, progress=None) -> int:
    """Fill country / state / city / county for live photos with GPS not looked up yet (every one
    with GPS when force): the Geocode button, and each library scan for what it brought in. Offline,
    so a few thousand take a second. The town is the nearest listed one (reverse_geocoder); inside
    the US the county and state come from the boundary the point falls in (us_counties), which is
    right where the nearest town is across a line. progress(done, total) is awaited after each
    batch. Returns how many."""
    from fernkam import us_counties

    with_counties = await asyncio.get_event_loop().run_in_executor(None, us_counties.available)
    q = (
        select(Photo.id, Photo.latitude, Photo.longitude)
        .where(Photo.status == 1)
        .where(Photo.latitude.is_not(None))
        .order_by(Photo.id)
    )
    if not force:
        q = q.where(Photo.city.is_(None) | (Photo.county.is_(None) if with_counties else false()))
    if limit:
        q = q.limit(limit)

    rows = (await db.execute(q)).fetchall()
    total, done = len(rows), 0
    BATCH = 5000
    for i in range(0, total, BATCH):
        batch = rows[i:i + BATCH]
        coords = [(float(r.latitude), float(r.longitude)) for r in batch]
        results = await asyncio.get_event_loop().run_in_executor(None, _lookup_batch, coords)
        in_us = [geo.get("cc") == "US" for geo in results]   # only these may take a county from offshore
        counties = (await asyncio.get_event_loop().run_in_executor(None, us_counties.lookup, coords, in_us)
                    if with_counties else [None] * len(batch))

        # Single executemany-style call per batch (was one round-trip
        # per photo — up to 120k individual UPDATEs). In a county: the US, its
        # state and county; otherwise the nearest town's.
        params = [
            {"cc": "US" if hit else geo.get("cc", ""), "state": hit[1] if hit else geo.get("admin1", ""),
             "city": geo.get("name", ""), "county": _county(geo, hit, with_counties), "id": row.id}
            for row, geo, hit in zip(batch, results, counties)
        ]
        await db.execute(text(
            "UPDATE photos SET country_code=:cc, state=:state, city=:city, county=:county WHERE id=:id"
        ), params)
        done += len(batch)
        await db.commit()
        if progress:
            await progress(done, total)
    return done


@router.get("/stats", response_model=dict)
async def geocode_stats(db: DB) -> dict:
    row = (await db.execute(text("""
        SELECT
            COUNT(*) FILTER (WHERE latitude IS NOT NULL)                           AS with_gps,
            COUNT(*) FILTER (WHERE latitude IS NOT NULL AND city IS NOT NULL)      AS geocoded,
            COUNT(*) FILTER (WHERE latitude IS NOT NULL AND (city IS NULL OR county IS NULL)) AS pending
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
        try:
            async with _factory() as bg_db:
                async def progress(done: int, total: int) -> None:
                    await task_manager.update_task(task_id, message=f"Geocoded {done}/{total}…",
                                                   progress={"done": done, "total": total})
                done = await geocode_pending(bg_db, force, limit, progress)
                await task_manager.update_task(
                    task_id, status="completed",
                    message=f"Done: {done} photos geocoded",
                    progress={"done": done, "total": done},
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
