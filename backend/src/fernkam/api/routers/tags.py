from __future__ import annotations

from typing import Optional
from fastapi import APIRouter, Body, HTTPException, Query
from sqlalchemy import select, delete, text

from fernkam.api.deps import DB
from fernkam.api.schemas import TagOut
from fernkam.db.models.photos import PhotoTag, Tag

router = APIRouter()


def _build_tag_tree(tags: list[Tag]) -> list[TagOut]:
    by_id: dict[int, TagOut] = {}
    for t in tags:
        by_id[t.id] = TagOut(
            id=t.id,
            name=t.name,
            path=str(t.path),
            parent_id=t.parent_id,
            is_person=t.is_person,
            is_fact=t.is_fact,
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
                id=t.id, name=t.name, path=str(t.path),
                parent_id=t.parent_id, is_person=t.is_person, is_fact=t.is_fact,
            )
            for t in tags
        ]
    return _build_tag_tree(list(tags))


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
        id=tag.id, name=tag.name, path=str(tag.path),
        parent_id=tag.parent_id, is_person=tag.is_person, is_fact=tag.is_fact,
    )


async def _mark_tagged_photos_dirty(db, tag_id: int) -> None:
    """Flag every photo carrying this tag or a descendant — as a tag or as a
    named face — for XMP write-back. Those files still hold the old Subject /
    HierarchicalSubject / region names; photo-level edits set this flag, but
    tag-level ones did not, so an incremental write-back never updated them."""
    await db.execute(text("""
        WITH subtree AS (
            SELECT id FROM tags WHERE path <@ (SELECT path FROM tags WHERE id = :id)
        )
        UPDATE photos SET file_sync_dirty = true
        WHERE id IN (SELECT photo_id FROM photo_tags WHERE tag_id IN (SELECT id FROM subtree))
           OR id IN (SELECT photo_id FROM faces WHERE person_tag_id IN (SELECT id FROM subtree))
    """), {"id": tag_id})


@router.patch("/{tag_id}", response_model=TagOut)
async def update_tag(
    tag_id: int,
    db: DB,
    name: Optional[str] = Body(None),
    parent_id: Optional[int] = Body(None),
    is_fact: Optional[bool] = Body(None),
) -> TagOut:
    """Update tag name and/or parent. Rebuilds path and updates all child paths."""
    tag = (await db.execute(select(Tag).where(Tag.id == tag_id))).scalar_one_or_none()
    if not tag:
        raise HTTPException(404, "Tag not found")
    
    # Update name if provided
    if name is not None:
        tag.name = name
    if is_fact is not None:
        tag.is_fact = is_fact

    # Update parent and rebuild path if parent_id provided
    if parent_id is not None:
        parent = (await db.execute(select(Tag).where(Tag.id == parent_id))).scalar_one_or_none()
        if not parent:
            raise HTTPException(404, "Parent tag not found")
        import re
        label = re.sub(r"[^A-Za-z0-9_]", "_", name or tag.name)
        if not label or label[0].isdigit():
            label = "_" + label
        await _set_parent(db, tag_id, parent_id, label)

    if name is not None or parent_id is not None:
        await _mark_tagged_photos_dirty(db, tag_id)
    await db.commit()
    tag = (await db.execute(select(Tag).where(Tag.id == tag_id).execution_options(populate_existing=True))).scalar_one()
    return TagOut(
        id=tag.id, name=tag.name, path=str(tag.path),
        parent_id=tag.parent_id, is_person=tag.is_person, is_fact=tag.is_fact,
    )


async def _set_parent(db, tag_id: int, parent_id: int, label: str) -> None:
    """Put a tag, and its subtree, under parent_id as <parent path>.<label>."""
    await db.execute(text("""
        WITH p AS (SELECT path FROM tags WHERE id = :parent),
             me AS (SELECT path FROM tags WHERE id = :id)
        UPDATE tags SET
            path = CASE WHEN id = :id THEN (SELECT path FROM p) || CAST(:label AS ltree)
                        ELSE (SELECT path FROM p) || CAST(:label AS ltree) || subpath(path, nlevel((SELECT path FROM me)))
                   END,
            parent_id = CASE WHEN id = :id THEN :parent ELSE parent_id END
        WHERE path <@ (SELECT path FROM me)
    """), {"id": tag_id, "parent": parent_id, "label": label})


async def _merge(db, src_id: int, dst_id: int) -> None:
    """Move src's photos, rejections and children onto dst, then delete src.
    A child whose label dst already has is merged into dst's child the same way."""
    kids_sql = text("SELECT id, subpath(path, nlevel(path) - 1)::text AS label FROM tags WHERE parent_id = :t")
    dst_kids = {r.label.lower(): r.id for r in (await db.execute(kids_sql, {"t": dst_id})).all()}
    for child in (await db.execute(kids_sql, {"t": src_id})).all():
        if child.label.lower() in dst_kids:
            await _merge(db, child.id, dst_kids[child.label.lower()])
        else:
            await _set_parent(db, child.id, dst_id, child.label)
    p = {"s": src_id, "d": dst_id}
    await db.execute(text("""
        INSERT INTO photo_tags (photo_id, tag_id, verified_at)
        SELECT photo_id, :d, verified_at FROM photo_tags WHERE tag_id = :s
        ON CONFLICT (photo_id, tag_id) DO UPDATE
            SET verified_at = COALESCE(photo_tags.verified_at, EXCLUDED.verified_at)
    """), p)
    await db.execute(text("""
        INSERT INTO tag_rejections (photo_id, tag_id, was_tagged)
        SELECT photo_id, :d, was_tagged FROM tag_rejections r WHERE tag_id = :s
          AND NOT EXISTS (SELECT 1 FROM photo_tags WHERE photo_id = r.photo_id AND tag_id = :d)
        ON CONFLICT DO NOTHING
    """), p)
    # Suggestions, checks and the trained model go with src (ON DELETE
    # CASCADE); dst's are recomputed from its combined labels.
    await db.execute(text("DELETE FROM tags WHERE id = :s"), p)


@router.post("/{tag_id}/merge", response_model=TagOut)
async def merge_tag(tag_id: int, db: DB, into: int = Body(..., embed=True)) -> TagOut:
    """Merge a tag into another: the same species under two parents, say.
    Its photos, rejections and child tags move to `into` (children named the
    same are merged too), and the tag is deleted."""
    src, dst = await db.get(Tag, tag_id), await db.get(Tag, into)
    if not src or not dst:
        raise HTTPException(404, "Tag not found")
    if src.is_person or dst.is_person:
        raise HTTPException(400, "People are merged on the People page")
    if str(dst.path) == str(src.path) or str(dst.path).startswith(str(src.path) + "."):
        raise HTTPException(400, "A tag can't be merged into itself or its own child")
    await _mark_tagged_photos_dirty(db, tag_id)   # their keywords change
    await _merge(db, tag_id, into)
    await db.commit()
    dst = (await db.execute(select(Tag).where(Tag.id == into).execution_options(populate_existing=True))).scalar_one()
    return TagOut(id=dst.id, name=dst.name, path=str(dst.path), parent_id=dst.parent_id,
                  is_person=dst.is_person, is_fact=dst.is_fact)


@router.delete("/{tag_id}/from-photos", status_code=200)
async def remove_tag_from_photos(tag_id: int, db: DB) -> dict:
    """Remove this tag from all photos without deleting the tag itself."""
    await db.execute(text(
        "UPDATE photos SET file_sync_dirty = true "
        "WHERE id IN (SELECT photo_id FROM photo_tags WHERE tag_id = :id)"
    ), {"id": tag_id})
    result = await db.execute(delete(PhotoTag).where(PhotoTag.tag_id == tag_id))
    await db.commit()
    return {"removed": result.rowcount}


@router.delete("/{tag_id}", status_code=204)
async def delete_tag(tag_id: int, db: DB) -> None:
    await _mark_tagged_photos_dirty(db, tag_id)
    await db.execute(delete(PhotoTag).where(PhotoTag.tag_id == tag_id))
    await db.execute(delete(Tag).where(Tag.id == tag_id))
    await db.commit()
