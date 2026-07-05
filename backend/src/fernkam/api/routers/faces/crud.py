"""Face CRUD endpoints: list, get, update, delete, batch ops."""
from __future__ import annotations

from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Body, HTTPException, Query
from sqlalchemy import select, update
from sqlalchemy.orm import selectinload

from fernkam.api.deps import DB
from fernkam.api.schemas import FaceOut, FaceUpdate
from fernkam.db.models.photos import Face, Photo, Tag

from ._helpers import _already_confirmed_in_photo, _auto_confirm_similar, _make_face_out

router = APIRouter()


@router.post("/batch-assign", status_code=204)
async def batch_assign_faces(
    db: DB,
    face_ids: list[str] = Body(...),
    person_tag_id: Optional[int] = Body(None),
    status: str = Body("confirmed"),
) -> None:
    """Assign (or unassign) multiple faces to a person in one call.

    Auto-confirm similar runs in a background task so the request returns immediately.
    """
    import asyncio as _asyncio
    uuids = [UUID(fid) for fid in face_ids]

    if status == "confirmed" and person_tag_id is not None:
        # TWINS bypass: persons named "…- TWINS" may appear multiple times per photo.
        tag_name = (await db.execute(select(Tag.name).where(Tag.id == person_tag_id))).scalar_one_or_none()
        is_twins = tag_name is not None and "- TWINS" in tag_name

        if is_twins:
            await db.execute(
                update(Face).where(Face.id.in_(uuids))
                .values(person_tag_id=person_tag_id, status=status)
            )
        else:
            # Duplicate guard: fetch (face_id, photo_id) for all affected faces.
            ph_rows = (await db.execute(
                select(Face.id, Face.photo_id).where(Face.id.in_(uuids))
            )).fetchall()
            photo_map = {r[0]: r[1] for r in ph_rows}

            affected_photos = list(set(photo_map.values()))
            existing_pairs: set = set()
            if affected_photos:
                ex_rows = (await db.execute(
                    select(Face.photo_id, Face.person_tag_id)
                    .where(Face.photo_id.in_(affected_photos))
                    .where(Face.status == "confirmed")
                    .where(Face.person_tag_id == person_tag_id)
                    .where(Face.id.not_in(uuids))
                )).fetchall()
                existing_pairs = {r[0] for r in ex_rows}  # photo_ids already taken

            ok_ids = []
            conflict_ids = []
            seen_photos: set = set()
            for uid in uuids:
                phid = photo_map.get(uid)
                if phid in existing_pairs or phid in seen_photos:
                    conflict_ids.append(uid)
                else:
                    ok_ids.append(uid)
                    if phid is not None:
                        seen_photos.add(phid)

            if conflict_ids:
                await db.execute(
                    update(Face).where(Face.id.in_(conflict_ids))
                    .values(status="unconfirmed", person_tag_id=None)
                )
            if ok_ids:
                await db.execute(
                    update(Face).where(Face.id.in_(ok_ids))
                    .values(person_tag_id=person_tag_id, status=status)
                )
    else:
        await db.execute(
            update(Face).where(Face.id.in_(uuids))
            .values(person_tag_id=person_tag_id, status=status)
        )
    await db.commit()

    # Only run _auto_confirm_similar for small batches (single/few-face manual confirms).
    # Large bulk assigns (e.g. acceptAllSuggested) skip it to avoid hammering the DB;
    # the explicit "Batch Assign" sweep handles those instead.
    if status == "confirmed" and person_tag_id and len(uuids) <= 5:
        async def _bg_similar(ptid: int, seeds: list[UUID]):
            from fernkam.db.session import async_session_factory
            try:
                async with async_session_factory() as bg_db:
                    await _auto_confirm_similar(bg_db, ptid, seeds)
            except Exception as _e:
                logger_ = __import__("logging").getLogger(__name__)
                logger_.warning("background _auto_confirm_similar failed: %s", _e)

        _asyncio.create_task(
            _bg_similar(person_tag_id, list(uuids)),
            name="fernkam-auto-confirm-similar",
        )

    photo_ids = [r[0] for r in (await db.execute(
        select(Face.photo_id).where(Face.id.in_(uuids)).distinct()
    )).fetchall()]
    if photo_ids:
        await db.execute(update(Photo).where(Photo.id.in_(photo_ids)).values(file_sync_dirty=True))
        await db.commit()


@router.delete("/{face_id}", status_code=204)
async def delete_face(face_id: UUID, db: DB) -> None:
    """Permanently remove a face record (not just unassign)."""
    row = (await db.execute(select(Face).where(Face.id == face_id))).scalar_one_or_none()
    if not row:
        raise HTTPException(404, "Face not found")
    photo_id = row.photo_id
    await db.delete(row)
    await db.execute(update(Photo).where(Photo.id == photo_id).values(file_sync_dirty=True))
    await db.commit()


@router.get("/", response_model=list[FaceOut])
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
    updates = payload.model_dump(exclude_unset=True)
    if not updates:
        raise HTTPException(400, "Nothing to update")

    if updates.get("status") == "confirmed" and updates.get("person_tag_id") is not None:
        face = (await db.execute(select(Face).where(Face.id == face_id))).scalar_one_or_none()
        if not face:
            raise HTTPException(404)
        if await _already_confirmed_in_photo(db, face.photo_id, updates["person_tag_id"], exclude_face_id=face_id):
            raise HTTPException(409, "Person already confirmed in this photo")

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

    await db.execute(update(Photo).where(Photo.id == row.photo_id).values(file_sync_dirty=True))
    await db.commit()

    if updates.get("status") == "confirmed" and row.person_tag_id:
        import asyncio as _asyncio

        async def _bg(ptid: int, seeds: list):
            from fernkam.db.session import async_session_factory
            try:
                async with async_session_factory() as bg_db:
                    await _auto_confirm_similar(bg_db, ptid, seeds)
            except Exception as _e:
                __import__("logging").getLogger(__name__).warning(
                    "bg _auto_confirm_similar failed: %s", _e
                )

        _asyncio.create_task(
            _bg(row.person_tag_id, [face_id]),
            name="fernkam-auto-confirm-similar",
        )

    return _make_face_out(row)


@router.post("/batch-delete", status_code=204)
async def batch_delete_faces(
    db: DB,
    face_ids: list[str] = Body(...),
) -> None:
    """Permanently delete multiple face records (e.g. a junk cluster)."""
    from sqlalchemy import delete as _delete
    uuids = [UUID(fid) for fid in face_ids]
    if not uuids:
        return
    photo_ids = [r[0] for r in (await db.execute(
        select(Face.photo_id).where(Face.id.in_(uuids)).distinct()
    )).fetchall()]
    await db.execute(_delete(Face).where(Face.id.in_(uuids)))
    if photo_ids:
        await db.execute(update(Photo).where(Photo.id.in_(photo_ids)).values(file_sync_dirty=True))
    await db.commit()
