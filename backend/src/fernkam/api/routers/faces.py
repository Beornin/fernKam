from __future__ import annotations

from typing import Optional
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query
from sqlalchemy import func, select, update

from fernkam.api.deps import DB
from fernkam.api.schemas import FaceOut
from fernkam.db.models.photos import Face, Tag

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
    q = select(Face)
    if photo_id is not None:
        q = q.where(Face.photo_id == photo_id)
    if person_tag_id is not None:
        q = q.where(Face.person_tag_id == person_tag_id)
    if status:
        q = q.where(Face.status == status)
    q = q.order_by(Face.created_at.desc()).offset(offset).limit(limit)
    return (await db.execute(q)).scalars().all()


@router.patch("/{face_id}")
async def update_face(
    face_id: UUID,
    db: DB,
    person_tag_id: Optional[int] = None,
    status: Optional[str] = None,
) -> FaceOut:
    updates: dict = {}
    if person_tag_id is not None:
        updates["person_tag_id"] = person_tag_id
    if status is not None:
        updates["status"] = status
    if not updates:
        raise HTTPException(400, "Nothing to update")

    await db.execute(update(Face).where(Face.id == face_id).values(**updates))
    await db.commit()
    row = (await db.execute(select(Face).where(Face.id == face_id))).scalar_one_or_none()
    if not row:
        raise HTTPException(404)
    return row
