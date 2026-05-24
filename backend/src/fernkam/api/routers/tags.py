from __future__ import annotations

from typing import Optional
from fastapi import APIRouter, Body, HTTPException, Query
from sqlalchemy import func, select, delete

from fernkam.api.deps import DB
from fernkam.api.schemas import TagOut
from fernkam.db.models.photos import Photo, PhotoTag, Tag

router = APIRouter()


def _build_tag_tree(tags: list[Tag]) -> list[TagOut]:
    by_id: dict[int, TagOut] = {}
    for t in tags:
        by_id[t.id] = TagOut(
            id=t.id,
            digikam_id=t.digikam_id,
            name=t.name,
            path=str(t.path),
            parent_id=t.parent_id,
            icon=t.icon,
            color=t.color,
            is_person=t.is_person,
        )
    roots: list[TagOut] = []
    for tag_out in by_id.values():
        if tag_out.parent_id and tag_out.parent_id in by_id:
            by_id[tag_out.parent_id].children.append(tag_out)
        else:
            roots.append(tag_out)
    return roots


@router.get("", response_model=list[TagOut])
async def list_tags(
    db: DB,
    flat: bool = Query(False, description="Return flat list instead of tree"),
    search: Optional[str] = Query(None),
) -> list[TagOut]:
    q = select(Tag).order_by(Tag.path)
    if search:
        q = q.where(Tag.name.ilike(f"%{search}%"))

    tags = (await db.execute(q)).scalars().all()
    if flat or search:
        return [
            TagOut(
                id=t.id, digikam_id=t.digikam_id, name=t.name, path=str(t.path),
                parent_id=t.parent_id, icon=t.icon, color=t.color, is_person=t.is_person,
            )
            for t in tags
        ]
    return _build_tag_tree(list(tags))


@router.get("/{tag_id}/photos/count")
async def tag_photo_count(tag_id: int, db: DB) -> dict:
    count = (
        await db.execute(
            select(func.count()).select_from(PhotoTag).where(PhotoTag.tag_id == tag_id)
        )
    ).scalar_one()
    return {"tag_id": tag_id, "photo_count": count}


@router.post("", response_model=TagOut, status_code=201)
async def create_tag(
    db: DB,
    name: str = Body(...),
    parent_id: Optional[int] = Body(None),
    is_person: bool = Body(False),
) -> TagOut:
    parent_path = ""
    if parent_id:
        parent = (await db.execute(select(Tag).where(Tag.id == parent_id))).scalar_one_or_none()
        if not parent:
            raise HTTPException(404, "Parent tag not found")
        parent_path = str(parent.path) + "."

    # Build ltree-safe label: replace spaces/special chars with underscore
    import re
    label = re.sub(r"[^A-Za-z0-9_]", "_", name)
    path = parent_path + label

    tag = Tag(name=name, path=path, parent_id=parent_id, is_person=is_person)
    db.add(tag)
    await db.commit()
    await db.refresh(tag)
    return TagOut(
        id=tag.id, digikam_id=tag.digikam_id, name=tag.name, path=str(tag.path),
        parent_id=tag.parent_id, icon=tag.icon, color=tag.color, is_person=tag.is_person,
    )


@router.delete("/{tag_id}", status_code=204)
async def delete_tag(tag_id: int, db: DB) -> None:
    await db.execute(delete(PhotoTag).where(PhotoTag.tag_id == tag_id))
    await db.execute(delete(Tag).where(Tag.id == tag_id))
    await db.commit()
