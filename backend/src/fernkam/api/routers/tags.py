from __future__ import annotations

from typing import Optional
from fastapi import APIRouter, Body, HTTPException, Query
from sqlalchemy import func, select, delete, text

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
    if not label or label[0].isdigit():
        label = "_" + label
    path = parent_path + label

    result = await db.execute(
        text("INSERT INTO tags (name, path, parent_id, is_person) VALUES (:name, CAST(:path AS ltree), :parent_id, :is_person) RETURNING id"),
        {"name": name, "path": path, "parent_id": parent_id, "is_person": is_person},
    )
    tag_id = result.scalar_one()
    await db.commit()
    tag = (await db.execute(select(Tag).where(Tag.id == tag_id))).scalar_one()
    return TagOut(
        id=tag.id, digikam_id=tag.digikam_id, name=tag.name, path=str(tag.path),
        parent_id=tag.parent_id, icon=tag.icon, color=tag.color, is_person=tag.is_person,
    )


@router.patch("/{tag_id}", response_model=TagOut)
async def update_tag(
    tag_id: int,
    db: DB,
    name: Optional[str] = Body(None),
    parent_id: Optional[int] = Body(None),
) -> TagOut:
    """Update tag name and/or parent. Rebuilds path and updates all child paths."""
    tag = (await db.execute(select(Tag).where(Tag.id == tag_id))).scalar_one_or_none()
    if not tag:
        raise HTTPException(404, "Tag not found")
    
    # Update name if provided
    if name is not None:
        tag.name = name
    
    # Update parent and rebuild path if parent_id provided
    if parent_id is not None:
        parent = (await db.execute(select(Tag).where(Tag.id == parent_id))).scalar_one_or_none()
        if not parent:
            raise HTTPException(404, "Parent tag not found")
        tag.parent_id = parent_id
        
        # Rebuild path: parent_path + label
        import re
        label = re.sub(r"[^A-Za-z0-9_]", "_", name or tag.name)
        if not label or label[0].isdigit():
            label = "_" + label
        new_path = str(parent.path) + "." + label
        
        # Update this tag's path
        await db.execute(
            text("UPDATE tags SET path = CAST(:path AS ltree) WHERE id = :id"),
            {"path": new_path, "id": tag_id}
        )
        
        # Update all descendant paths
        old_path = str(tag.path)
        await db.execute(
            text("UPDATE tags SET path = CAST(:new_prefix || subpath(path, nlevel(:old_path)) AS ltree) WHERE path <@ :old_path AND id != :id"),
            {"new_prefix": new_path, "old_path": old_path, "id": tag_id}
        )
    
    await db.commit()
    tag = (await db.execute(select(Tag).where(Tag.id == tag_id))).scalar_one()
    return TagOut(
        id=tag.id, digikam_id=tag.digikam_id, name=tag.name, path=str(tag.path),
        parent_id=tag.parent_id, icon=tag.icon, color=tag.color, is_person=tag.is_person,
    )


@router.delete("/{tag_id}/from-photos", status_code=200)
async def remove_tag_from_photos(tag_id: int, db: DB) -> dict:
    """Remove this tag from all photos without deleting the tag itself."""
    result = await db.execute(delete(PhotoTag).where(PhotoTag.tag_id == tag_id))
    await db.commit()
    return {"removed": result.rowcount}


@router.delete("/{tag_id}", status_code=204)
async def delete_tag(tag_id: int, db: DB) -> None:
    await db.execute(delete(PhotoTag).where(PhotoTag.tag_id == tag_id))
    await db.execute(delete(Tag).where(Tag.id == tag_id))
    await db.commit()
