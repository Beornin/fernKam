from __future__ import annotations

from datetime import date
from typing import Optional

import asyncio

from fastapi import APIRouter, HTTPException, Query
from sqlalchemy import func, select, update
from sqlalchemy.orm import selectinload

from fernkam.api.deps import DB
from fernkam.api.schemas import FaceOut, PhotoDetail, PhotoPage, PhotoSummary, PhotoUpdate, TagOut
from fernkam.db.models.photos import Face, Photo, PhotoTag, Tag
from fernkam.metadata import write_photo_metadata

router = APIRouter()


def _photo_query(
    album_path: Optional[str],
    tag_id: Optional[int],
    rating_min: Optional[int],
    media_type: Optional[str],
    search: Optional[str],
    date_from: Optional[date],
    date_to: Optional[date],
):
    q = select(Photo).where(Photo.status == 1)

    if album_path:
        q = q.where(Photo.album_path.like(f"{album_path}%"))
    if tag_id is not None:
        q = q.where(
            Photo.id.in_(
                select(PhotoTag.photo_id).where(PhotoTag.tag_id == tag_id)
            )
        )
    if rating_min is not None:
        q = q.where(Photo.rating >= rating_min)
    if media_type:
        q = q.where(Photo.media_type == media_type)
    if search:
        q = q.where(Photo.filename.ilike(f"%{search}%"))
    if date_from:
        q = q.where(func.date(Photo.taken_at) >= date_from)
    if date_to:
        q = q.where(func.date(Photo.taken_at) <= date_to)

    return q


@router.get("", response_model=PhotoPage)
async def list_photos(
    db: DB,
    album_path: Optional[str] = Query(None),
    tag_id: Optional[int] = Query(None),
    rating_min: Optional[int] = Query(None),
    media_type: Optional[str] = Query(None),
    search: Optional[str] = Query(None),
    date_from: Optional[date] = Query(None),
    date_to: Optional[date] = Query(None),
    sort: str = Query("taken_at_desc"),
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
) -> PhotoPage:
    q = _photo_query(album_path, tag_id, rating_min, media_type, search, date_from, date_to)

    ORDER = {
        "taken_at_desc": Photo.taken_at.desc().nulls_last(),
        "taken_at_asc": Photo.taken_at.asc().nulls_last(),
        "rating_desc": Photo.rating.desc(),
        "filename_asc": Photo.filename.asc(),
        "imported_at_desc": Photo.imported_at.desc(),
    }
    q = q.order_by(ORDER.get(sort, Photo.taken_at.desc().nulls_last()))

    total = (await db.execute(select(func.count()).select_from(q.subquery()))).scalar_one()
    rows = (await db.execute(q.offset((page - 1) * page_size).limit(page_size))).scalars().all()

    return PhotoPage(
        total=total,
        page=page,
        page_size=page_size,
        items=[PhotoSummary.model_validate(r) for r in rows],
    )


@router.get("/{photo_id}", response_model=PhotoDetail)
async def get_photo(photo_id: int, db: DB) -> PhotoDetail:
    row = (
        await db.execute(
            select(Photo)
            .where(Photo.id == photo_id)
            .options(
                selectinload(Photo.camera),
                selectinload(Photo.lens),
                selectinload(Photo.photo_tags).selectinload(PhotoTag.tag),
                selectinload(Photo.faces).selectinload(Face.person_tag),
            )
        )
    ).scalar_one_or_none()

    if not row:
        raise HTTPException(404, "Photo not found")

    detail = PhotoDetail.model_validate(row)
    detail.tags = [pt.tag for pt in row.photo_tags if pt.tag]
    detail.faces = _enrich_faces(list(row.faces))
    return detail


def _enrich_faces(faces: list[Face]) -> list[FaceOut]:
    """Convert Face ORM objects to FaceOut, resolving person name."""
    out = []
    for f in faces:
        name: Optional[str] = None
        if f.person_tag:
            name = f.person_tag.name
        elif hasattr(f, "person") and f.person:
            name = f.person.name
        out.append(FaceOut(
            id=f.id,
            person_tag_id=f.person_tag_id,
            person_name=name,
            x=f.x,
            y=f.y,
            w=f.w,
            h=f.h,
            status=f.status,
            region_name=f.region_name,
        ))
    return out


async def _get_photo_for_write(db: DB, photo_id: int) -> Optional[Photo]:
    return (
        await db.execute(
            select(Photo)
            .where(Photo.id == photo_id)
            .options(selectinload(Photo.photo_tags).selectinload(PhotoTag.tag))
        )
    ).scalar_one_or_none()


async def _write_tags_metadata(db: DB, photo_id: int) -> None:
    photo = await _get_photo_for_write(db, photo_id)
    if not photo:
        return
    tag_names = [pt.tag.name for pt in photo.photo_tags if pt.tag]
    tag_paths = [str(pt.tag.path).replace(".", "/") for pt in photo.photo_tags if pt.tag]
    asyncio.create_task(
        write_photo_metadata(
            photo_id, photo.album_path, photo.filename,
            tags=tag_names, tag_paths=tag_paths,
        )
    )


@router.get("/{photo_id}/tags", response_model=list[TagOut])
async def get_photo_tags(photo_id: int, db: DB) -> list[TagOut]:
    rows = (await db.execute(
        select(Tag).join(PhotoTag, PhotoTag.tag_id == Tag.id).where(PhotoTag.photo_id == photo_id)
    )).scalars().all()
    return rows


@router.post("/{photo_id}/tags/{tag_id}", status_code=204)
async def add_photo_tag(photo_id: int, tag_id: int, db: DB) -> None:
    existing = (await db.execute(
        select(PhotoTag).where(PhotoTag.photo_id == photo_id, PhotoTag.tag_id == tag_id)
    )).scalar_one_or_none()
    if not existing:
        db.add(PhotoTag(photo_id=photo_id, tag_id=tag_id))
        await db.commit()
    await _write_tags_metadata(db, photo_id)


@router.delete("/{photo_id}/tags/{tag_id}", status_code=204)
async def remove_photo_tag(photo_id: int, tag_id: int, db: DB) -> None:
    pt = (await db.execute(
        select(PhotoTag).where(PhotoTag.photo_id == photo_id, PhotoTag.tag_id == tag_id)
    )).scalar_one_or_none()
    if pt:
        await db.delete(pt)
        await db.commit()
    await _write_tags_metadata(db, photo_id)


@router.get("/map/points")
async def map_points(
    db: DB,
    album_path: Optional[str] = Query(None),
    tag_id: Optional[int] = Query(None),
    limit: int = Query(5000, le=20000),
) -> list[dict]:
    """Return lat/lon + id for photos with GPS — used by the map view."""
    q = (
        select(Photo.id, Photo.latitude, Photo.longitude, Photo.filename, Photo.taken_at)
        .where(Photo.status == 1)
        .where(Photo.latitude.is_not(None))
        .where(Photo.longitude.is_not(None))
    )
    if album_path:
        q = q.where(Photo.album_path.like(f"{album_path}%"))
    if tag_id is not None:
        q = q.where(Photo.id.in_(select(PhotoTag.photo_id).where(PhotoTag.tag_id == tag_id)))
    q = q.limit(limit)
    rows = (await db.execute(q)).fetchall()
    return [
        {"id": r.id, "lat": float(r.latitude), "lon": float(r.longitude),
         "filename": r.filename, "taken_at": r.taken_at.isoformat() if r.taken_at else None}
        for r in rows
    ]


@router.patch("/{photo_id}", response_model=PhotoSummary)
async def update_photo(photo_id: int, payload: PhotoUpdate, db: DB) -> PhotoSummary:
    updates = payload.model_dump(exclude_none=True)
    if not updates:
        raise HTTPException(400, "No fields to update")

    await db.execute(update(Photo).where(Photo.id == photo_id).values(**updates))
    await db.commit()

    row = (await db.execute(select(Photo).where(Photo.id == photo_id))).scalar_one_or_none()
    if not row:
        raise HTTPException(404, "Photo not found")

    asyncio.create_task(
        write_photo_metadata(
            photo_id, row.album_path, row.filename,
            rating=payload.rating,
            caption=payload.caption,
            title=payload.title,
            color_label=payload.color_label,
        )
    )
    return PhotoSummary.model_validate(row)
