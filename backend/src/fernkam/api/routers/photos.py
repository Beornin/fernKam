from __future__ import annotations

import asyncio
from datetime import date
from typing import Optional

from fastapi import APIRouter, HTTPException, Query
from sqlalchemy import func, select, update
from sqlalchemy.orm import selectinload

from fernkam.api.deps import DB
from fernkam.api.schemas import BatchDetectResult, FaceOut, PhotoDetail, PhotoPage, PhotoSummary, PhotoUpdate, TagOut
from fernkam.db.models.photos import Face, Photo, PhotoTag, Tag

router = APIRouter()


async def _photo_query(
    album_path: Optional[str],
    tag_id: Optional[int],
    rating_min: Optional[int],
    media_type: Optional[str],
    search: Optional[str],
    date_from: Optional[date],
    date_to: Optional[date],
    db,
):
    q = select(Photo).where(Photo.status == 1)

    if album_path:
        q = q.where(Photo.album_path.like(f"{album_path}%"))
    if tag_id is not None:
        # Get the tag and its path to include all children
        tag = (await db.execute(select(Tag).where(Tag.id == tag_id))).scalar_one_or_none()
        if tag:
            # Use ltree operator to find all tags under this path (including itself)
            from sqlalchemy import text
            child_tags = await db.execute(
                select(Tag.id).where(text(f"path <@ '{tag.path}'"))
            )
            child_tag_ids = [row[0] for row in child_tags]
            if child_tag_ids:
                q = q.where(
                    Photo.id.in_(
                        select(PhotoTag.photo_id).where(PhotoTag.tag_id.in_(child_tag_ids))
                    )
                )
    if rating_min is not None:
        q = q.where(Photo.rating >= rating_min)
    if media_type:
        q = q.where(Photo.media_type == media_type)
    if search:
        q = q.where(Photo.filename.ilike(f"%{search}%"))
    if date_from:
        q = q.where(func.date(Photo.taken_at) >= date_from)
    if date_to:
        q = q.where(func.date(Photo.taken_at) <= date_to)

    return q


@router.get("/unscanned-count")
async def unscanned_count(db: DB) -> dict:
    """Count of photos not yet scanned for faces."""
    scanned = select(Face.photo_id).distinct()
    n = (await db.execute(
        select(func.count()).select_from(Photo)
        .where(Photo.status == 1)
        .where(Photo.media_type == "image")
        .where(Photo.id.not_in(scanned))
    )).scalar_one()
    return {"count": n}


@router.get("", response_model=PhotoPage)
async def list_photos(
    db: DB,
    album_path: Optional[str] = Query(None),
    tag_id: Optional[int] = Query(None),
    rating_min: Optional[int] = Query(None),
    media_type: Optional[str] = Query(None),
    search: Optional[str] = Query(None),
    date_from: Optional[date] = Query(None),
    date_to: Optional[date] = Query(None),
    sort: str = Query("taken_at_desc"),
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
) -> PhotoPage:
    q = await _photo_query(album_path, tag_id, rating_min, media_type, search, date_from, date_to, db)

    ORDER = {
        "taken_at_desc": Photo.taken_at.desc().nulls_last(),
        "taken_at_asc": Photo.taken_at.asc().nulls_last(),
        "rating_desc": Photo.rating.desc(),
        "filename_asc": Photo.filename.asc(),
        "imported_at_desc": Photo.imported_at.desc(),
    }
    q = q.order_by(ORDER.get(sort, Photo.taken_at.desc().nulls_last()))

    total = (await db.execute(select(func.count()).select_from(q.subquery()))).scalar_one()
    rows = (await db.execute(q.offset((page - 1) * page_size).limit(page_size))).scalars().all()

    return PhotoPage(
        total=total,
        page=page,
        page_size=page_size,
        items=[PhotoSummary.model_validate(r) for r in rows],
    )


@router.get("/{photo_id}", response_model=PhotoDetail)
async def get_photo(photo_id: int, db: DB) -> PhotoDetail:
    row = (
        await db.execute(
            select(Photo)
            .where(Photo.id == photo_id)
            .options(
                selectinload(Photo.camera),
                selectinload(Photo.lens),
                selectinload(Photo.photo_tags).selectinload(PhotoTag.tag),
                selectinload(Photo.faces).selectinload(Face.person_tag),
            )
        )
    ).scalar_one_or_none()

    if not row:
        raise HTTPException(404, "Photo not found")

    detail = PhotoDetail.model_validate(row)
    detail.tags = [pt.tag for pt in row.photo_tags if pt.tag]
    detail.faces = _enrich_faces(list(row.faces))
    return detail


def _enrich_faces(faces: list[Face]) -> list[FaceOut]:
    """Convert Face ORM objects to FaceOut, resolving person name."""
    out = []
    for f in faces:
        name: Optional[str] = None
        if f.person_tag:
            name = f.person_tag.name
        elif hasattr(f, "person") and f.person:
            name = f.person.name
        out.append(FaceOut(
            id=f.id,
            photo_id=f.photo_id,
            person_tag_id=f.person_tag_id,
            person_name=name,
            x=f.x,
            y=f.y,
            w=f.w,
            h=f.h,
            status=f.status,
            region_name=f.region_name,
        ))
    return out


async def _get_photo_for_write(db: DB, photo_id: int) -> Optional[Photo]:
    return (
        await db.execute(
            select(Photo)
            .where(Photo.id == photo_id)
            .options(selectinload(Photo.photo_tags).selectinload(PhotoTag.tag))
        )
    ).scalar_one_or_none()


@router.get("/{photo_id}/tags", response_model=list[TagOut])
async def get_photo_tags(photo_id: int, db: DB) -> list[TagOut]:
    rows = (await db.execute(
        select(Tag).join(PhotoTag, PhotoTag.tag_id == Tag.id).where(PhotoTag.photo_id == photo_id)
    )).scalars().all()
    return rows


@router.post("/{photo_id}/tags/{tag_id}", status_code=204)
async def add_photo_tag(photo_id: int, tag_id: int, db: DB) -> None:
    existing = (await db.execute(
        select(PhotoTag).where(PhotoTag.photo_id == photo_id, PhotoTag.tag_id == tag_id)
    )).scalar_one_or_none()
    if not existing:
        db.add(PhotoTag(photo_id=photo_id, tag_id=tag_id))
    await db.execute(update(Photo).where(Photo.id == photo_id).values(file_sync_dirty=True))
    await db.commit()


@router.delete("/{photo_id}/tags/{tag_id}", status_code=204)
async def remove_photo_tag(photo_id: int, tag_id: int, db: DB) -> None:
    pt = (await db.execute(
        select(PhotoTag).where(PhotoTag.photo_id == photo_id, PhotoTag.tag_id == tag_id)
    )).scalar_one_or_none()
    if pt:
        await db.delete(pt)
    await db.execute(update(Photo).where(Photo.id == photo_id).values(file_sync_dirty=True))
    await db.commit()


@router.get("/map/points")
async def map_points(
    db: DB,
    album_path: Optional[str] = Query(None),
    tag_id: Optional[int] = Query(None),
    limit: int = Query(5000, le=20000),
) -> list[dict]:
    """Return lat/lon + id for photos with GPS — used by the map view."""
    q = (
        select(Photo.id, Photo.latitude, Photo.longitude, Photo.filename, Photo.taken_at)
        .where(Photo.status == 1)
        .where(Photo.latitude.is_not(None))
        .where(Photo.longitude.is_not(None))
    )
    if album_path:
        q = q.where(Photo.album_path.like(f"{album_path}%"))
    if tag_id is not None:
        q = q.where(Photo.id.in_(select(PhotoTag.photo_id).where(PhotoTag.tag_id == tag_id)))
    q = q.limit(limit)
    rows = (await db.execute(q)).fetchall()
    return [
        {"id": r.id, "lat": float(r.latitude), "lon": float(r.longitude),
         "filename": r.filename, "taken_at": r.taken_at.isoformat() if r.taken_at else None}
        for r in rows
    ]


async def _detect_and_suggest(photo_id: int, db: DB) -> tuple[list[FaceOut], int]:
    """Core detect + similarity-suggest logic. Returns (face_outs, suggested_count)."""
    from fernkam.face_processor import (
        bytes_to_embedding, detect_and_embed, embedding_to_bytes, find_similar_numpy,
    )
    from fernkam.thumbnails import photo_disk_path

    photo = (await db.execute(select(Photo).where(Photo.id == photo_id))).scalar_one_or_none()
    if not photo:
        raise HTTPException(404, "Photo not found")

    src = photo_disk_path(photo.album_path, photo.filename)
    if not src.exists():
        raise HTTPException(422, f"Source file not found: {src}")

    import cv2 as _cv2

    loop = asyncio.get_event_loop()
    detections = await loop.run_in_executor(None, detect_and_embed, src)
    if not detections:
        return [], 0

    # Read image once for cropping all face regions
    img_bgr = _cv2.imread(str(src))

    # Load existing faces on this photo to avoid duplicates
    existing_faces = (await db.execute(
        select(Face.x, Face.y, Face.w, Face.h)
        .where(Face.photo_id == photo_id)
    )).fetchall()

    # Load confirmed faces with embeddings for suggestion matching
    confirmed = (await db.execute(
        select(Face.id, Face.embedding, Face.person_tag_id)
        .where(Face.status == "confirmed", Face.embedding.isnot(None), Face.person_tag_id.isnot(None))
    )).fetchall()

    SUGGEST_THRESH = 0.55
    new_faces: list[tuple[Face, float | None]] = []
    suggested = 0

    for det in detections:
        # Skip if a face already exists at this location (within 5px tolerance)
        if any(
            abs(det["x"] - f.x) < 5 and abs(det["y"] - f.y) < 5
            for f in existing_faces
        ):
            continue
        emb_bytes = embedding_to_bytes(det["embedding"])
        status = "unconfirmed"
        person_tag_id = None
        score: float | None = round(float(det["score"]), 3)

        if confirmed:
            matches = find_similar_numpy(
                det["embedding"],
                [(r.id, r.embedding, r.person_tag_id) for r in confirmed],
                k=1,
                min_score=SUGGEST_THRESH,
            )
            if matches:
                person_tag_id = matches[0]["person_tag_id"]
                status = "suggested"
                score = round(matches[0]["score"], 3)
                suggested += 1

        # Generate face crop bytes while image is in memory
        crop_bytes: bytes | None = None
        if img_bgr is not None:
            h_img, w_img = img_bgr.shape[:2]
            pad = int(max(det["w"], det["h"]) * 0.2)
            x1 = max(0, det["x"] - pad)
            y1 = max(0, det["y"] - pad)
            x2 = min(w_img, det["x"] + det["w"] + pad)
            y2 = min(h_img, det["y"] + det["h"] + pad)
            crop = _cv2.resize(img_bgr[y1:y2, x1:x2], (200, 200), interpolation=_cv2.INTER_AREA)
            ok, buf = _cv2.imencode(".webp", crop, [_cv2.IMWRITE_WEBP_QUALITY, 85])
            if ok:
                crop_bytes = bytes(buf)

        face = Face(
            photo_id=photo_id,
            x=det["x"], y=det["y"], w=det["w"], h=det["h"],
            embedding=emb_bytes,
            status=status,
            person_tag_id=person_tag_id,
            crop_data=crop_bytes,
        )
        db.add(face)
        new_faces.append((face, score))

    await db.commit()

    results: list[FaceOut] = []
    for face, score in new_faces:
        await db.refresh(face)
        reloaded = (await db.execute(
            select(Face).options(selectinload(Face.person_tag)).where(Face.id == face.id)
        )).scalar_one()
        person_name = reloaded.person_tag.name if reloaded.person_tag else None
        results.append(FaceOut(
            id=reloaded.id,
            photo_id=reloaded.photo_id,
            person_tag_id=reloaded.person_tag_id,
            person_name=person_name,
            x=reloaded.x, y=reloaded.y, w=reloaded.w, h=reloaded.h,
            status=reloaded.status,
            region_name=reloaded.region_name,
            score=score,
        ))

    return results, suggested


@router.post("/{photo_id}/detect-faces", response_model=list[FaceOut])
async def detect_faces(photo_id: int, db: DB) -> list[FaceOut]:
    """Run InsightFace detection + similarity suggestion on a single photo."""
    faces, _ = await _detect_and_suggest(photo_id, db)
    return faces


@router.post("/batch-detect", response_model=BatchDetectResult)
async def batch_detect_faces(photo_ids: list[int], db: DB) -> BatchDetectResult:
    """Run detect + identify on multiple photos. Pass photo IDs as a JSON array body."""
    processed = 0
    faces_found = 0
    suggested = 0
    errors = 0
    details: list[dict] = []

    for photo_id in photo_ids:
        try:
            faces, sug = await _detect_and_suggest(photo_id, db)
            processed += 1
            faces_found += len(faces)
            suggested += sug
            details.append({"photo_id": photo_id, "faces": len(faces), "suggested": sug})
        except Exception as exc:
            errors += 1
            details.append({"photo_id": photo_id, "error": str(exc)})

    return BatchDetectResult(
        processed=processed, faces_found=faces_found,
        suggested=suggested, errors=errors, details=details,
    )


@router.post("/batch-detect-all", response_model=BatchDetectResult)
async def batch_detect_all_faces(db: DB) -> BatchDetectResult:
    """Detect faces in all photos that have no face records yet (images only)."""
    from sqlalchemy import except_
    scanned = select(Face.photo_id).distinct()
    photo_ids_q = (
        select(Photo.id)
        .where(Photo.status == 1)
        .where(Photo.media_type == "image")
        .where(Photo.id.not_in(scanned))
        .order_by(Photo.id)
    )
    rows = (await db.execute(photo_ids_q)).fetchall()
    photo_ids = [r[0] for r in rows]

    processed = 0
    faces_found = 0
    suggested = 0
    errors = 0
    details: list[dict] = []
    for photo_id in photo_ids:
        try:
            faces, sug = await _detect_and_suggest(photo_id, db)
            processed += 1
            faces_found += len(faces)
            suggested += sug
            details.append({"photo_id": photo_id, "faces": len(faces), "suggested": sug})
        except Exception as exc:
            errors += 1
            details.append({"photo_id": photo_id, "error": str(exc)})
    return BatchDetectResult(
        processed=processed, faces_found=faces_found,
        suggested=suggested, errors=errors, details=details,
    )


@router.patch("/{photo_id}", response_model=PhotoSummary)
async def update_photo(photo_id: int, payload: PhotoUpdate, db: DB) -> PhotoSummary:
    updates = payload.model_dump(exclude_none=True)
    if not updates:
        raise HTTPException(400, "No fields to update")

    await db.execute(update(Photo).where(Photo.id == photo_id).values(**updates))
    await db.commit()

    row = (await db.execute(select(Photo).where(Photo.id == photo_id))).scalar_one_or_none()
    if not row:
        raise HTTPException(404, "Photo not found")

    await db.execute(update(Photo).where(Photo.id == photo_id).values(file_sync_dirty=True))
    await db.commit()
    return PhotoSummary.model_validate(row)
