from __future__ import annotations

from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Body, HTTPException, Query
from sqlalchemy import func, select, text, update
from sqlalchemy.orm import selectinload

from fernkam.api.deps import DB
from fernkam.api.schemas import FaceOut, PersonOut
from fernkam.db.models.photos import Face, Photo, Tag

router = APIRouter()


def _make_face_out(f: Face) -> FaceOut:
    return FaceOut(
        id=f.id,
        photo_id=f.photo_id,
        person_tag_id=f.person_tag_id,
        person_name=f.person_tag.name if f.person_tag else None,
        x=f.x, y=f.y, w=f.w, h=f.h,
        status=f.status,
        region_name=f.region_name,
    )


@router.get("", response_model=list[PersonOut])
async def list_people(
    db: DB,
    search: Optional[str] = Query(None),
    limit: int = Query(500, le=2000),
) -> list[PersonOut]:
    q = (
        select(Tag.id, Tag.name, func.count(Face.id).label("face_count"))
        .outerjoin(Face, Face.person_tag_id == Tag.id)
        .where(Tag.is_person == True)
        .group_by(Tag.id)
        .order_by(Tag.name)
    )
    if search:
        q = q.where(Tag.name.ilike(f"%{search}%"))
    rows = (await db.execute(q.limit(limit))).fetchall()
    return [PersonOut(id=r.id, name=r.name, tag_id=r.id, face_count=r.face_count) for r in rows]


@router.post("", response_model=PersonOut, status_code=201)
async def create_person(db: DB, name: str = Body(...), parent_id: Optional[int] = Body(None)) -> PersonOut:
    import re
    existing = (await db.execute(
        select(Tag).where(Tag.name == name, Tag.is_person == True)
    )).scalar_one_or_none()
    if existing:
        raise HTTPException(409, f"Person '{name}' already exists (id={existing.id})")

    label = re.sub(r"[^A-Za-z0-9_]", "_", name)
    if parent_id:
        parent = (await db.execute(select(Tag).where(Tag.id == parent_id))).scalar_one_or_none()
        path = f"{parent.path}.{label}" if parent else label
    else:
        path = label

    result = await db.execute(
        text("INSERT INTO tags (name, path, is_person, parent_id) VALUES (:name, CAST(:path AS ltree), :is_person, :parent_id) RETURNING id"),
        {"name": name, "path": path, "is_person": True, "parent_id": parent_id},
    )
    tag_id = result.scalar_one()
    await db.commit()
    return PersonOut(id=tag_id, name=name, tag_id=tag_id, face_count=0)


@router.patch("/{person_id}", response_model=PersonOut)
async def rename_person(person_id: int, db: DB, name: str = Body(...)) -> PersonOut:
    tag = (await db.execute(
        select(Tag).where(Tag.id == person_id, Tag.is_person == True)
    )).scalar_one_or_none()
    if not tag:
        raise HTTPException(404, "Person not found")
    await db.execute(update(Tag).where(Tag.id == person_id).values(name=name))
    await db.commit()
    count = (await db.execute(
        select(func.count()).where(Face.person_tag_id == person_id)
    )).scalar_one()
    return PersonOut(id=person_id, name=name, tag_id=person_id, face_count=count)


@router.delete("/{person_id}", status_code=204)
async def delete_person(person_id: int, db: DB) -> None:
    tag = (await db.execute(
        select(Tag).where(Tag.id == person_id, Tag.is_person == True)
    )).scalar_one_or_none()
    if not tag:
        raise HTTPException(404, "Person not found")
    await db.execute(
        update(Face).where(Face.person_tag_id == person_id)
        .values(person_tag_id=None, status="unknown")
    )
    await db.delete(tag)
    await db.commit()


@router.get("/{person_id}/faces", response_model=list[FaceOut])
async def get_person_faces(
    person_id: int,
    db: DB,
    limit: int = Query(200, le=1000),
    offset: int = Query(0, ge=0),
) -> list[FaceOut]:
    rows = (await db.execute(
        select(Face)
        .where(Face.person_tag_id == person_id)
        .options(selectinload(Face.person_tag))
        .order_by(Face.created_at.desc())
        .limit(limit).offset(offset)
    )).scalars().all()
    return [_make_face_out(f) for f in rows]
