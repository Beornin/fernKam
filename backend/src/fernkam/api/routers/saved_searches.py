"""Saved searches (smart albums) — CRUD + execute."""
from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Any, Optional

from fastapi import APIRouter, Body, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import func, select, update

from fernkam.api.deps import DB
from fernkam.api.schemas import PhotoPage, PhotoSummary
from fernkam.db.models.photos import SavedSearch
from fernkam.services.photo_query import (
    PhotoFilters, apply_cursor, apply_sort, build_photo_query, encode_cursor,
)

router = APIRouter()


# ── Pydantic schemas ─────────────────────────────────────────────────────────

class SavedSearchOut(BaseModel):
    id: int
    name: str
    description: Optional[str]
    filters: dict
    sort: str
    pin_to_sidebar: bool
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class SavedSearchIn(BaseModel):
    name: str
    description: Optional[str] = None
    filters: dict = {}
    sort: str = "taken_at_desc"
    pin_to_sidebar: bool = False


# ── Helpers ──────────────────────────────────────────────────────────────────

def _parse_date(v: Any) -> Optional[date]:
    if not v:
        return None
    if isinstance(v, date):
        return v
    try:
        return date.fromisoformat(str(v))
    except ValueError:
        return None


def _filters_from_dict(d: dict) -> PhotoFilters:
    return PhotoFilters(
        album_path=d.get("album_path"),
        tag_id=d.get("tag_id"),
        person_tag_id=d.get("person_tag_id"),
        rating_min=d.get("rating_min"),
        color_label=d.get("color_label"),
        media_type=d.get("media_type"),
        camera_id=d.get("camera_id"),
        lens_id=d.get("lens_id"),
        has_gps=d.get("has_gps"),
        has_faces=d.get("has_faces"),
        no_date=d.get("no_date"),
        search=d.get("search"),
        date_from=_parse_date(d.get("date_from")),
        date_to=_parse_date(d.get("date_to")),
        country_code=d.get("country_code"),
    )


# ── CRUD endpoints ────────────────────────────────────────────────────────────

@router.get("", response_model=list[SavedSearchOut])
async def list_saved_searches(
    db: DB,
    pinned_only: bool = Query(False),
) -> list[SavedSearchOut]:
    q = select(SavedSearch).order_by(SavedSearch.pin_to_sidebar.desc(), SavedSearch.name)
    if pinned_only:
        q = q.where(SavedSearch.pin_to_sidebar == True)
    rows = (await db.execute(q)).scalars().all()
    return [SavedSearchOut.model_validate(r) for r in rows]


@router.post("", response_model=SavedSearchOut, status_code=201)
async def create_saved_search(db: DB, body: SavedSearchIn) -> SavedSearchOut:
    ss = SavedSearch(
        name=body.name,
        description=body.description,
        filters=body.filters,
        sort=body.sort,
        pin_to_sidebar=body.pin_to_sidebar,
    )
    db.add(ss)
    await db.commit()
    await db.refresh(ss)
    return SavedSearchOut.model_validate(ss)


@router.get("/{search_id}", response_model=SavedSearchOut)
async def get_saved_search(search_id: int, db: DB) -> SavedSearchOut:
    ss = (await db.execute(select(SavedSearch).where(SavedSearch.id == search_id))).scalar_one_or_none()
    if not ss:
        raise HTTPException(404, "Saved search not found")
    return SavedSearchOut.model_validate(ss)


@router.patch("/{search_id}", response_model=SavedSearchOut)
async def update_saved_search(
    search_id: int,
    db: DB,
    name: Optional[str] = Body(None),
    description: Optional[str] = Body(None),
    filters: Optional[dict] = Body(None),
    sort: Optional[str] = Body(None),
    pin_to_sidebar: Optional[bool] = Body(None),
) -> SavedSearchOut:
    ss = (await db.execute(select(SavedSearch).where(SavedSearch.id == search_id))).scalar_one_or_none()
    if not ss:
        raise HTTPException(404, "Saved search not found")
    if name is not None:
        ss.name = name
    if description is not None:
        ss.description = description
    if filters is not None:
        ss.filters = filters
    if sort is not None:
        ss.sort = sort
    if pin_to_sidebar is not None:
        ss.pin_to_sidebar = pin_to_sidebar
    ss.updated_at = datetime.now(timezone.utc)
    await db.commit()
    await db.refresh(ss)
    return SavedSearchOut.model_validate(ss)


@router.delete("/{search_id}", status_code=204)
async def delete_saved_search(search_id: int, db: DB) -> None:
    ss = (await db.execute(select(SavedSearch).where(SavedSearch.id == search_id))).scalar_one_or_none()
    if not ss:
        raise HTTPException(404, "Saved search not found")
    await db.delete(ss)
    await db.commit()


# ── Execute (run the saved search as a photo query) ───────────────────────────

@router.get("/{search_id}/photos", response_model=PhotoPage)
async def run_saved_search(
    search_id: int,
    db: DB,
    page: int = Query(1, ge=1),
    page_size: int = Query(200, ge=1, le=2000),
    cursor: Optional[str] = Query(None),
) -> PhotoPage:
    """Execute the saved search and return a paginated PhotoPage."""
    ss = (await db.execute(select(SavedSearch).where(SavedSearch.id == search_id))).scalar_one_or_none()
    if not ss:
        raise HTTPException(404, "Saved search not found")

    filters = _filters_from_dict(ss.filters)
    base_q = await build_photo_query(filters, db)
    q = apply_sort(base_q, ss.sort)

    total = (await db.execute(select(func.count()).select_from(base_q.subquery()))).scalar_one()

    if cursor:
        q_page = apply_cursor(q, cursor).limit(page_size)
    else:
        q_page = q.offset((page - 1) * page_size).limit(page_size)

    rows = (await db.execute(q_page)).scalars().all()
    next_cur = encode_cursor(rows[-1], ss.sort) if rows and len(rows) == page_size else None

    return PhotoPage(
        total=total,
        page=page,
        page_size=page_size,
        items=[PhotoSummary.model_validate(r) for r in rows],
        next_cursor=next_cur,
    )
