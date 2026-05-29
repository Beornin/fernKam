from __future__ import annotations

from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Body, HTTPException, Query
from sqlalchemy import select, update
from sqlalchemy.orm import selectinload

from fernkam.api.deps import DB
from fernkam.api.schemas import FaceOut, FaceUpdate
from fernkam.db.models.photos import Face, Photo, PhotoTag, Tag

router = APIRouter()


@router.get("/unassigned/count")
async def unassigned_count(db: DB) -> dict:
    """Count of unassigned non-ignored faces."""
    from sqlalchemy import func
    n = (await db.execute(
        select(func.count()).select_from(Face)
        .where(Face.person_tag_id.is_(None))
        .where(Face.status != "ignored")
    )).scalar_one()
    return {"count": n}


@router.get("/suggestions")
async def face_suggestions(
    db: DB,
    limit: int = Query(50, le=200),
    offset: int = Query(0, ge=0),
) -> list[dict]:
    """Unassigned faces with top-3 person suggestions from embedding similarity."""
    from fernkam.face_processor import bytes_to_embedding, find_similar_numpy

    unassigned = (await db.execute(
        select(Face)
        .options(selectinload(Face.person_tag))
        .where(Face.person_tag_id.is_(None))
        .where(Face.status != "ignored")
        .where(Face.embedding.is_not(None))
        .order_by(Face.created_at.desc())
        .offset(offset).limit(limit)
    )).scalars().all()

    if not unassigned:
        return []

    confirmed = (await db.execute(
        select(Face.id, Face.embedding, Face.person_tag_id)
        .where(Face.person_tag_id.is_not(None))
        .where(Face.embedding.is_not(None))
    )).fetchall()

    tag_names: dict = {}
    if confirmed:
        ptids = {r.person_tag_id for r in confirmed}
        tag_rows = (await db.execute(select(Tag).where(Tag.id.in_(ptids)))).scalars().all()
        tag_names = {t.id: t.name for t in tag_rows}

    results = []
    for face in unassigned:
        suggestions: list[dict] = []
        if confirmed:
            try:
                qe = bytes_to_embedding(face.embedding)
                matches = find_similar_numpy(qe, [(r.id, r.embedding, r.person_tag_id) for r in confirmed], k=10)
                best: dict[int, dict] = {}
                for m in matches:
                    pid = m["person_tag_id"]
                    if pid and (pid not in best or m["score"] > best[pid]["score"]):
                        best[pid] = m
                suggestions = sorted(
                    [{"person_id": pid, "person_name": tag_names.get(pid), "score": round(m["score"], 4)}
                     for pid, m in best.items()],
                    key=lambda x: x["score"], reverse=True
                )[:3]
            except (ValueError, Exception):
                pass
        results.append({"face": _make_face_out(face), "suggestions": suggestions})
    return results


@router.get("/unassigned", response_model=list[FaceOut])
async def unassigned_faces(
    db: DB,
    photo_id: Optional[int] = Query(None),
    limit: int = Query(100, le=500),
    offset: int = Query(0, ge=0),
    has_embedding: Optional[bool] = Query(None),
) -> list[FaceOut]:
    """Faces without a person assignment, newest first."""
    q = (
        select(Face)
        .options(selectinload(Face.person_tag))
        .where(Face.person_tag_id.is_(None))
        .where(Face.status != "ignored")
        .order_by(Face.created_at.desc())
        .offset(offset).limit(limit)
    )
    if photo_id is not None:
        q = q.where(Face.photo_id == photo_id)
    if has_embedding is True:
        q = q.where(Face.embedding.is_not(None))
    elif has_embedding is False:
        q = q.where(Face.embedding.is_(None))
    rows = (await db.execute(q)).scalars().all()
    return [_make_face_out(f) for f in rows]


@router.get("/{face_id}/similar")
async def similar_faces(
    face_id: UUID,
    db: DB,
    k: int = Query(10, le=50),
    confirmed_only: bool = Query(True),
) -> list[dict]:
    """Top-K most similar faces by cosine similarity of InsightFace embeddings."""
    from fernkam.face_processor import bytes_to_embedding, find_similar_numpy

    face = (await db.execute(select(Face).where(Face.id == face_id))).scalar_one_or_none()
    if not face:
        raise HTTPException(404, "Face not found")
    if not face.embedding:
        raise HTTPException(422, "Face has no embedding — run detect-faces first")

    try:
        query_emb = bytes_to_embedding(face.embedding)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc

    cq = select(Face.id, Face.embedding, Face.person_tag_id).where(
        Face.id != face_id, Face.embedding.is_not(None)
    )
    if confirmed_only:
        cq = cq.where(Face.person_tag_id.is_not(None))
    candidates = (await db.execute(cq)).fetchall()

    matches = find_similar_numpy(
        query_emb, [(r.id, r.embedding, r.person_tag_id) for r in candidates], k=k
    )

    ptids = {m["person_tag_id"] for m in matches if m.get("person_tag_id")}
    tag_names = {}
    if ptids:
        tag_rows = (await db.execute(select(Tag).where(Tag.id.in_(ptids)))).scalars().all()
        tag_names = {t.id: t.name for t in tag_rows}

    return [
        {
            "face_id": str(m["face_id"]),
            "person_tag_id": m["person_tag_id"],
            "person_name": tag_names.get(m["person_tag_id"]),
            "score": round(m["score"], 4),
        }
        for m in matches
    ]


@router.post("/batch-assign", status_code=204)
async def batch_assign_faces(
    db: DB,
    face_ids: list[str] = Body(...),
    person_tag_id: Optional[int] = Body(None),
    status: str = Body("confirmed"),
) -> None:
    """Assign (or unassign) multiple faces to a person in one call."""
    uuids = [UUID(fid) for fid in face_ids]
    await db.execute(
        update(Face).where(Face.id.in_(uuids))
        .values(person_tag_id=person_tag_id, status=status)
    )
    await db.commit()
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

    await db.execute(update(Photo).where(Photo.id == row.photo_id).values(file_sync_dirty=True))
    await db.commit()
    return _make_face_out(row)


def _make_face_out(f: Face) -> FaceOut:
    return FaceOut(
        id=f.id,
        photo_id=f.photo_id,
        person_tag_id=f.person_tag_id,
        person_name=f.person_tag.name if f.person_tag else None,
        x=f.x,
        y=f.y,
        w=f.w,
        h=f.h,
        status=f.status,
        region_name=f.region_name,
    )


