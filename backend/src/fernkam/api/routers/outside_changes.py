"""The "Changed outside fernKam" list.

fernKam honours edits made to library files by other programs: the file's
values are taken (see sync_merge). Each time that happens an entry is
recorded here, so the user knows what changed. Where the same field also had
an unsaved fernKam edit, the entry keeps the replaced value and it can be
restored.
"""
from __future__ import annotations

import json
from typing import Optional

from fastapi import APIRouter, Body, HTTPException, Query
from sqlalchemy import text

from fernkam.api.deps import DB
from fernkam.sync_merge import SCALAR_FIELDS

router = APIRouter()


@router.get("")
async def list_outside_changes(
    db: DB,
    include_dismissed: bool = Query(False),
    limit: int = Query(200, ge=1, le=2000),
) -> dict:
    rows = (await db.execute(text(f"""
        SELECT c.id, c.photo_id, c.detected_at, c.kind, c.details, c.dismissed,
               p.filename, p.album_path, p.media_type
        FROM outside_changes c JOIN photos p ON p.id = c.photo_id
        {"" if include_dismissed else "WHERE NOT c.dismissed"}
        ORDER BY c.detected_at DESC, c.id DESC
        LIMIT :limit
    """), {"limit": limit})).all()
    return {"changes": [
        {"id": r.id, "photo_id": r.photo_id, "detected_at": r.detected_at.isoformat(),
         "kind": r.kind, "details": r.details, "dismissed": r.dismissed,
         "filename": r.filename, "album_path": r.album_path, "media_type": r.media_type}
        for r in rows
    ]}


@router.get("/count")
async def count_outside_changes(db: DB) -> dict:
    # Photos, not entries: one photo edited three times is one thing to look at.
    n = (await db.execute(text(
        "SELECT count(DISTINCT photo_id) FROM outside_changes WHERE NOT dismissed"))).scalar_one()
    return {"open": n}


@router.post("/{change_id}/restore")
async def restore_fernkam_values(change_id: int, db: DB) -> dict:
    """Put back the fernKam values an outside edit replaced, and flag the
    photo so the next write-back puts them into the file."""
    row = (await db.execute(text(
        "SELECT photo_id, details FROM outside_changes WHERE id = :id"
    ), {"id": change_id})).first()
    if not row:
        raise HTTPException(404, "Change not found")
    conflicts = (row.details or {}).get("conflicts") or {}
    values = {f: v["fernkam"] for f, v in conflicts.items() if f in SCALAR_FIELDS}
    if not values:
        raise HTTPException(400, "Nothing to restore: no fernKam edit was replaced")
    sets = ", ".join(f"{f} = :{f}" for f in values)
    await db.execute(text(f"UPDATE photos SET {sets}, file_sync_dirty = true WHERE id = :pid"),
                     {**values, "pid": row.photo_id})
    await db.execute(text(
        "UPDATE outside_changes SET dismissed = true, "
        "details = details || CAST(:note AS jsonb) WHERE id = :id"
    ), {"id": change_id, "note": json.dumps({"restored": values})})
    await db.commit()
    return {"photo_id": row.photo_id, "restored": values}


@router.post("/dismiss")
async def dismiss_outside_changes(
    db: DB,
    ids: Optional[list[int]] = Body(None, embed=True),
    all: bool = Body(False, embed=True),  # noqa: A002 — the JSON field name
) -> dict:
    if all:
        result = await db.execute(text("UPDATE outside_changes SET dismissed = true WHERE NOT dismissed"))
    elif ids:
        result = await db.execute(text(
            "UPDATE outside_changes SET dismissed = true WHERE id = ANY(:ids)"), {"ids": ids})
    else:
        raise HTTPException(400, "Pass ids or all=true")
    await db.commit()
    return {"dismissed": result.rowcount}


@router.get("/watch-interval")
async def get_watch_interval() -> dict:
    """Minutes between scans for changes made outside fernKam (0 = off)."""
    from fernkam import library_watch
    return {"minutes": library_watch.interval_s / 60}


@router.post("/watch-interval")
async def set_watch_interval(db: DB, minutes: float = Body(..., embed=True, ge=0, le=1440)) -> dict:
    from fernkam import library_watch
    from fernkam.db.app_settings import set_setting
    await set_setting(db, "watch_interval_min", f"{minutes:g}")
    library_watch.interval_s = minutes * 60
    return {"minutes": minutes}
