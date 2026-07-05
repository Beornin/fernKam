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
    if not label or label[0].isdigit():
        label = "_" + label

    if parent_id:
        parent = (await db.execute(select(Tag).where(Tag.id == parent_id))).scalar_one_or_none()
        if not parent:
            raise HTTPException(404, "Parent tag not found")
        path = f"{parent.path}.{label}"
    else:
        people_root = (await db.execute(
            select(Tag).where(Tag.name == "People", Tag.is_person == False)
        )).scalar_one_or_none()
        if people_root:
            parent_id = people_root.id
            path = f"{people_root.path}.{label}"
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
async def rename_person(person_id: int, db: DB, name: str = Body(..., embed=True)) -> PersonOut:
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
        .values(person_tag_id=None, status="unconfirmed")
    )
    await db.delete(tag)
    await db.commit()


@router.post("/{person_id}/merge")
async def merge_person(
    person_id: int,
    db: DB,
    into_id: int = Body(..., embed=True),
) -> dict:
    """Merge person_id into into_id.

    All confirmed/suggested faces from person_id are re-assigned to into_id.
    Person centroids for into_id are rebuilt. person_id tag is deleted.
    """
    if person_id == into_id:
        raise HTTPException(400, "Cannot merge a person into themselves")

    src = (await db.execute(select(Tag).where(Tag.id == person_id, Tag.is_person == True))).scalar_one_or_none()
    dst = (await db.execute(select(Tag).where(Tag.id == into_id, Tag.is_person == True))).scalar_one_or_none()
    if not src:
        raise HTTPException(404, f"Source person {person_id} not found")
    if not dst:
        raise HTTPException(404, f"Target person {into_id} not found")

    result = await db.execute(
        update(Face)
        .where(Face.person_tag_id == person_id)
        .values(person_tag_id=into_id)
        .returning(Face.id)
    )
    moved = len(result.fetchall())

    await db.execute(
        text("DELETE FROM person_centroids WHERE person_tag_id = :pid"),
        {"pid": person_id},
    )
    await db.delete(src)
    await db.commit()

    new_count = (await db.execute(
        select(func.count()).where(Face.person_tag_id == into_id)
    )).scalar_one()

    return {"merged": moved, "into": dst.name, "face_count": new_count}


@router.post("/{person_id}/split", status_code=201)
async def split_person(
    person_id: int,
    db: DB,
    face_ids: list[str] = Body(...),
    new_name: Optional[str] = Body(None),
    into_id: Optional[int] = Body(None),
) -> dict:
    """Split selected face_ids out of person_id into a new or existing person.

    Provide either `new_name` (creates a new person tag) or `into_id` (re-assigns to
    an existing person). The source person keeps its remaining faces.
    """
    import re

    if not face_ids:
        raise HTTPException(400, "face_ids must not be empty")
    if not new_name and not into_id:
        raise HTTPException(400, "Provide either new_name or into_id")

    src = (await db.execute(select(Tag).where(Tag.id == person_id, Tag.is_person == True))).scalar_one_or_none()
    if not src:
        raise HTTPException(404, f"Source person {person_id} not found")

    from uuid import UUID as _UUID
    uuids = [_UUID(fid) for fid in face_ids]

    if into_id:
        dst = (await db.execute(select(Tag).where(Tag.id == into_id, Tag.is_person == True))).scalar_one_or_none()
        if not dst:
            raise HTTPException(404, f"Target person {into_id} not found")
        target_id = into_id
        target_name = dst.name
    else:
        label = re.sub(r"[^A-Za-z0-9_]", "_", new_name or "")
        if not label or label[0].isdigit():
            label = "_" + label
        people_root = (await db.execute(
            select(Tag).where(Tag.name == "People", Tag.is_person == False)
        )).scalar_one_or_none()
        path = f"{people_root.path}.{label}" if people_root else label
        result = await db.execute(
            text("INSERT INTO tags (name, path, is_person, parent_id) VALUES (:name, CAST(:path AS ltree), TRUE, :pid) RETURNING id"),
            {"name": new_name, "path": path, "pid": people_root.id if people_root else None},
        )
        target_id = result.scalar_one()
        target_name = new_name

    await db.execute(
        update(Face).where(Face.id.in_(uuids)).values(person_tag_id=target_id, status="confirmed")
    )
    await db.commit()

    return {"split": len(uuids), "into": target_name, "target_id": target_id}


@router.get("/{person_id}/faces", response_model=list[FaceOut])
async def get_person_faces(
    person_id: int,
    db: DB,
    limit: int = Query(200, le=1000),
    offset: int = Query(0, ge=0),
    sort: str = Query("created", regex="^(created|photo_date|score)$"),
    dir: str = Query("desc", regex="^(asc|desc)$"),
) -> list[FaceOut]:
    from sqlalchemy import asc, desc, nulls_last

    q = select(Face).where(Face.person_tag_id == person_id).options(selectinload(Face.person_tag))

    if sort == "photo_date":
        q = q.join(Photo, Face.photo_id == Photo.id)
        col = Photo.taken_at
    elif sort == "score":
        col = Face.best_match_score
    else:
        col = Face.created_at

    order = (nulls_last(asc(col)) if dir == "asc" else nulls_last(desc(col)))
    q = q.order_by(order)

    rows = (await db.execute(q.limit(limit).offset(offset))).scalars().all()
    return [_make_face_out(f) for f in rows]
