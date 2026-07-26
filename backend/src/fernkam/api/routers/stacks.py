"""RAW/JPG stack endpoints: list, detail, tree, detect, tag-sync, set-cover."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Body, HTTPException, Query
from sqlalchemy import func, select, update
from sqlalchemy.orm import selectinload

from fernkam.api.deps import DB
from fernkam.api.schemas import AlbumNode
from fernkam.db.models.photos import Face, Photo, PhotoStack, PhotoTag, Tag
from fernkam.media_types import is_raw

router = APIRouter()


@router.get("")
async def list_stacks(
    db: DB,
    album_path: Optional[str] = Query(None),
    limit: int = Query(60, le=500),
    offset: int = Query(0),
) -> dict:
    q = select(PhotoStack)
    if album_path:
        album_path = album_path.lstrip("/")
        q = q.where(PhotoStack.album_path.like(f"{album_path}%"))
    total = (await db.execute(select(func.count()).select_from(q.subquery()))).scalar_one()
    rows = (
        await db.execute(
            q.options(selectinload(PhotoStack.cover)).order_by(PhotoStack.album_path, PhotoStack.stem_key)
            .limit(limit).offset(offset)
        )
    ).scalars().all()

    items = []
    for s in rows:
        raw_count = 1 if s.has_raw else 0
        items.append({
            "id": s.id,
            "album_path": s.album_path,
            "stem_key": s.stem_key,
            "cover_photo_id": s.cover_photo_id,
            "member_count": s.member_count,
            "has_raw": s.has_raw,
        })
    return {"items": items, "total": total}


@router.get("/tree", response_model=list[AlbumNode])
async def stacks_tree(db: DB) -> list[AlbumNode]:
    """Album tree restricted to albums that contain at least one stack."""
    from fernkam.api.routers.albums import _build_tree

    rows = (
        await db.execute(
            select(PhotoStack.album_path, func.count().label("cnt"))
            .group_by(PhotoStack.album_path)
        )
    ).fetchall()
    return _build_tree([(r.album_path, r.cnt) for r in rows])


@router.get("/{stack_id}")
async def get_stack(stack_id: int, db: DB) -> dict:
    stack = (await db.execute(
        select(PhotoStack).where(PhotoStack.id == stack_id)
    )).scalar_one_or_none()
    if not stack:
        raise HTTPException(404, "Stack not found")

    members = (await db.execute(
        select(Photo).where(Photo.stack_id == stack_id).order_by(Photo.stack_role.desc(), Photo.filename)
    )).scalars().all()

    def _member_dict(p: Photo) -> dict:
        return {
            "id": p.id,
            "filename": p.filename,
            "album_path": p.album_path,
            "stack_role": p.stack_role,
            "media_type": p.media_type,
            "rating": p.rating,
            "is_cover": p.id == stack.cover_photo_id,
        }

    # Cover first, then derivatives, then RAW.
    ordered = sorted(
        members,
        key=lambda p: (0 if p.id == stack.cover_photo_id else (1 if p.stack_role == "derivative" else 2)),
    )

    return {
        "id": stack.id,
        "album_path": stack.album_path,
        "stem_key": stack.stem_key,
        "cover_photo_id": stack.cover_photo_id,
        "member_count": stack.member_count,
        "has_raw": stack.has_raw,
        "members": [_member_dict(p) for p in ordered],
    }


@router.post("/detect")
async def detect(db: DB, album_path: Optional[str] = Body(None, embed=True)) -> dict:
    from fernkam.services.stacks import detect_stacks

    if album_path:
        album_path = album_path.lstrip("/")
    result = await detect_stacks(db, album_path=album_path)
    return result


@router.post("/{stack_id}/set-cover")
async def set_cover(stack_id: int, db: DB, photo_id: int = Body(..., embed=True)) -> dict:
    stack = (await db.execute(select(PhotoStack).where(PhotoStack.id == stack_id))).scalar_one_or_none()
    if not stack:
        raise HTTPException(404, "Stack not found")
    member = (await db.execute(
        select(Photo).where(Photo.id == photo_id, Photo.stack_id == stack_id)
    )).scalar_one_or_none()
    if not member:
        raise HTTPException(400, "Photo is not a member of this stack")
    stack.cover_photo_id = photo_id
    stack.updated_at = datetime.now(timezone.utc)
    await db.commit()
    return {"stack_id": stack_id, "cover_photo_id": photo_id}


@router.post("/{stack_id}/sync-tags")
async def sync_stack_tags(stack_id: int, db: DB) -> dict:
    """Union tags/rating/color-label/face-regions across all members, write to every member."""
    from fernkam.metadata_sync import build_photo_payload, write_metadata_batch

    stack = (await db.execute(select(PhotoStack).where(PhotoStack.id == stack_id))).scalar_one_or_none()
    if not stack:
        raise HTTPException(404, "Stack not found")

    members = (await db.execute(
        select(Photo).where(Photo.stack_id == stack_id)
        .options(
            selectinload(Photo.photo_tags).selectinload(PhotoTag.tag),
            selectinload(Photo.faces).selectinload(Face.person_tag),
        )
    )).scalars().all()
    if not members:
        return {"synced": 0, "errors": 0}

    # Union tags across all members.
    tag_ids: set[int] = set()
    tags_by_id: dict[int, Tag] = {}
    for m in members:
        for pt in m.photo_tags:
            if pt.tag:
                tag_ids.add(pt.tag.id)
                tags_by_id[pt.tag.id] = pt.tag

    # Union rating (max) and color label across members. Face regions are NOT
    # unioned — bounding boxes are pixel coordinates specific to each file's
    # own dimensions/crop, so each member keeps writing only its own faces
    # (build_photo_payload already does this via each photo's own `.faces`).
    union_rating = max((m.rating or 0) for m in members)
    color_candidates = [m.color_label for m in members if m.color_label]
    union_color = color_candidates[0] if color_candidates else 0

    now = datetime.now(timezone.utc)
    payloads = []
    for m in members:
        # Apply the union in-DB first so build_photo_payload sees merged state.
        m.rating = union_rating
        if union_color:
            m.color_label = union_color
        existing_tag_ids = {pt.tag_id for pt in m.photo_tags}
        for tid in tag_ids - existing_tag_ids:
            db.add(PhotoTag(photo_id=m.id, tag_id=tid))

        named_faces = [
            f for f in m.faces
            if f.x is not None and (f.person_tag is not None or (f.region_name or "").strip())
        ]
        p = build_photo_payload(m, list(tags_by_id.values()), named_faces)
        if p:
            payloads.append(p)

    await db.flush()

    ok, errors = 0, 0
    if payloads:
        import asyncio
        loop = asyncio.get_event_loop()
        ok, errors = await loop.run_in_executor(None, write_metadata_batch, payloads)

    if ok:
        member_ids = [m.id for m in members]
        await db.execute(
            update(Photo).where(Photo.id.in_(member_ids))
            .values(meta_synced_at=now, file_sync_dirty=False)
        )

    await db.commit()
    return {"synced": ok, "errors": errors, "members": len(members)}
