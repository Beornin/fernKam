"""Read-only API for the centralized application log table."""
from __future__ import annotations

import logging
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, HTTPException, Query
from sqlalchemy import text

from fernkam.api.deps import DB

router = APIRouter()

_PREVIEW = 1200   # characters of a traceback in the list


_LEVEL_MAP = {
    "DEBUG": logging.DEBUG,
    "INFO": logging.INFO,
    "WARNING": logging.WARNING,
    "ERROR": logging.ERROR,
    "CRITICAL": logging.CRITICAL,
}


@router.get("")
async def list_logs(
    db: DB,
    level: Optional[str] = Query(None, description="Min level: DEBUG/INFO/WARNING/ERROR/CRITICAL"),
    source: Optional[str] = Query(None, description="python | stderr | request"),
    q: Optional[str] = Query(None, description="Substring match on message"),
    since: Optional[datetime] = Query(None, description="Only rows with last_seen_at >= since (ISO 8601)"),
    before: Optional[datetime] = Query(None, description="Only rows with ts < before"),
    limit: int = Query(200, le=500, ge=1),
    offset: int = Query(0, ge=0),
) -> dict:
    where = []
    params: dict = {"limit": limit, "offset": offset}
    if level:
        lv = _LEVEL_MAP.get(level.upper())
        if lv is not None:
            where.append("level >= :lv")
            params["lv"] = lv
    if source:
        where.append("source = :src")
        params["src"] = source
    if q:
        where.append("message ILIKE :q")
        params["q"] = f"%{q}%"
    if since:
        where.append("last_seen_at >= :since")
        params["since"] = since
    if before:
        where.append("ts < :before")
        params["before"] = before

    where_sql = (" WHERE " + " AND ".join(where)) if where else ""
    rows = (await db.execute(text(
        "SELECT id, ts, level, level_name, source, logger_name, message, "
        "       file, line, func, exc_info, fingerprint, occurrences, last_seen_at "
        "FROM app_logs" + where_sql +
        " ORDER BY last_seen_at DESC LIMIT :limit OFFSET :offset"
    ), params)).fetchall()

    items = [
        {
            "id": r.id,
            "ts": r.ts.isoformat(),
            "level": r.level,
            "level_name": r.level_name,
            "source": r.source,
            "logger_name": r.logger_name,
            "message": r.message[:2000],
            "file": r.file,
            "line": r.line,
            "func": r.func,
            # A list of 200 rows carried 5.6 MB of traceback text: the first lines here, all of it
            # from GET /api/logs/{id} when a row is opened.
            "exc_info": (r.exc_info[:_PREVIEW] if r.exc_info else None),
            "exc_truncated": bool(r.exc_info and len(r.exc_info) > _PREVIEW),
            "fingerprint": r.fingerprint,
            "occurrences": r.occurrences,
            "last_seen_at": r.last_seen_at.isoformat(),
        }
        for r in rows
    ]
    return {"items": items, "limit": limit, "offset": offset}


@router.get("/{log_id:int}")
async def get_log(log_id: int, db: DB) -> dict:
    """One row with its whole message and traceback."""
    r = (await db.execute(text(
        "SELECT id, message, exc_info FROM app_logs WHERE id = :i"), {"i": log_id})).first()
    if not r:
        raise HTTPException(404, "Log row not found")
    return {"id": r.id, "message": r.message, "exc_info": r.exc_info}


@router.get("/count")
async def count_logs(
    db: DB,
    level: Optional[str] = Query(None),
    source: Optional[str] = Query(None),
    q: Optional[str] = Query(None),
    since: Optional[datetime] = Query(None),
    before: Optional[datetime] = Query(None),
) -> dict:
    where = []
    params: dict = {}
    if level:
        lv = _LEVEL_MAP.get(level.upper())
        if lv is not None:
            where.append("level >= :lv")
            params["lv"] = lv
    if source:
        where.append("source = :src")
        params["src"] = source
    if q:
        where.append("message ILIKE :q")
        params["q"] = f"%{q}%"
    if since:
        where.append("last_seen_at >= :since")
        params["since"] = since
    if before:
        where.append("ts < :before")
        params["before"] = before
    where_sql = (" WHERE " + " AND ".join(where)) if where else ""
    n = (await db.execute(text("SELECT count(*) FROM app_logs" + where_sql), params)).scalar_one()
    return {"count": int(n or 0)}


@router.get("/levels")
async def levels(db: DB) -> dict:
    rows = (await db.execute(text(
        "SELECT level_name, count(*) FROM app_logs GROUP BY level_name ORDER BY 2 DESC"
    ))).fetchall()
    return {"levels": [{"level_name": r[0], "count": int(r[1])} for r in rows]}


@router.get("/sources")
async def sources(db: DB) -> dict:
    rows = (await db.execute(text(
        "SELECT source, count(*) FROM app_logs GROUP BY source ORDER BY 2 DESC"
    ))).fetchall()
    return {"sources": [{"source": r[0], "count": int(r[1])} for r in rows]}


@router.delete("")
async def clear_logs(
    db: DB,
    level: Optional[str] = Query(None, description="Only delete rows >= this level"),
    before: Optional[datetime] = Query(None, description="Only delete rows older than this"),
) -> dict:
    where = []
    params: dict = {}
    if level:
        lv = _LEVEL_MAP.get(level.upper())
        if lv is not None:
            where.append("level >= :lv")
            params["lv"] = lv
    if before:
        where.append("ts < :before")
        params["before"] = before
    where_sql = (" WHERE " + " AND ".join(where)) if where else ""
    res = await db.execute(text("DELETE FROM app_logs" + where_sql), params)
    await db.commit()
    return {"deleted": int(res.rowcount or 0)}


@router.get("/sink-stats")
async def sink_stats() -> dict:
    from fernkam.logs_sink import sink_stats as _stats
    return _stats()
