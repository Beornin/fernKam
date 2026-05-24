from __future__ import annotations

from typing import Optional
from fastapi import APIRouter, Query
from sqlalchemy import func, select

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
