from __future__ import annotations

import asyncio
from typing import Optional
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query
from sqlalchemy import select, update
from sqlalchemy.orm import selectinload

from fernkam.api.deps import DB
from fernkam.api.schemas import FaceOut, FaceUpdate
from fernkam.db.models.photos import Face, Photo, PhotoTag, Tag
from fernkam.metadata import write_photo_metadata

router = APIRouter()


@router.get("", response_model=list[FaceOut])
async def list_faces(
    db: DB,
    photo_id: Optional[int] = Query(None),
    person_tag_id: Optional[int] = Query(None),
    status: Optional[str] = Query(None),
    limit: int = Query(100, le=500),
    offset: int = Query(0, ge=0),
) -> list[FaceOut]:
    q = select(Face).options(selectinload(Face.person_tag))
    if photo_id is not None:
        q = q.where(Face.photo_id == photo_id)
    if person_tag_id is not None:
        q = q.where(Face.person_tag_id == person_tag_id)
    if status:
        q = q.where(Face.status == status)
    q = q.order_by(Face.created_at.desc()).offset(offset).limit(limit)
    faces = (await db.execute(q)).scalars().all()
    return [_make_face_out(f) for f in faces]


@router.patch("/{face_id}", response_model=FaceOut)
async def update_face(face_id: UUID, payload: FaceUpdate, db: DB) -> FaceOut:
    updates = payload.model_dump(exclude_none=True)
    if not updates:
        raise HTTPException(400, "Nothing to update")

    await db.execute(update(Face).where(Face.id == face_id).values(**updates))
    await db.commit()

    row = (
        await db.execute(
            select(Face)
            .where(Face.id == face_id)
            .options(
                selectinload(Face.person_tag),
                selectinload(Face.photo),
            )
        )
    ).scalar_one_or_none()
    if not row:
        raise HTTPException(404)

    asyncio.create_task(_write_face_regions(db, row.photo_id))
    return _make_face_out(row)


def _make_face_out(f: Face) -> FaceOut:
    return FaceOut(
        id=f.id,
        person_tag_id=f.person_tag_id,
        person_name=f.person_tag.name if f.person_tag else None,
        x=f.x,
        y=f.y,
        w=f.w,
        h=f.h,
        status=f.status,
        region_name=f.region_name,
    )


async def _write_face_regions(db: DB, photo_id: int) -> None:
    photo = (
        await db.execute(
            select(Photo)
            .where(Photo.id == photo_id)
            .options(selectinload(Photo.faces).selectinload(Face.person_tag))
        )
    ).scalar_one_or_none()
    if not photo:
        return
    regions = [
        {
            "x": f.x, "y": f.y, "w": f.w, "h": f.h,
            "name": f.person_tag.name if f.person_tag else (f.region_name or ""),
        }
        for f in photo.faces if f.x is not None
    ]
    await write_photo_metadata(
        photo_id, photo.album_path, photo.filename,
        face_regions=regions,
        img_width=photo.width,
        img_height=photo.height,
    )
