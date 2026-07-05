from __future__ import annotations

import asyncio
import re
from datetime import date, datetime, timezone
from typing import Optional

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import func, select, update
from sqlalchemy.orm import selectinload

from fernkam.api.deps import DB
from fernkam.api.schemas import BatchDetectResult, FaceOut, PhotoDetail, PhotoPage, PhotoSummary, PhotoUpdate, TagOut
from fernkam.db.models.photos import Face, Photo, PhotoTag, Tag
from fernkam.services.photo_query import PhotoFilters, apply_cursor, apply_sort, build_photo_query, count_photos, encode_cursor

router = APIRouter()


@router.get("/unscanned-count")
async def unscanned_count(db: DB) -> dict:
    """Count of photos not yet run through InsightFace detection."""
    n = (await db.execute(
        select(func.count()).select_from(Photo)
        .where(Photo.status == 1)
        .where(Photo.media_type == "image")
        .where(Photo.faces_scanned_at.is_(None))
    )).scalar_one()
    return {"count": n}


@router.get("", response_model=PhotoPage)
async def list_photos(
    db: DB,
    album_path: Optional[str] = Query(None),
    tag_id: Optional[int] = Query(None),
    person_tag_id: Optional[int] = Query(None),
    rating_min: Optional[int] = Query(None),
    color_label: Optional[int] = Query(None),
    media_type: Optional[str] = Query(None),
    camera_id: Optional[int] = Query(None),
    lens_id: Optional[int] = Query(None),
    has_gps: Optional[bool] = Query(None),
    has_faces: Optional[bool] = Query(None),
    no_date: Optional[bool] = Query(None),
    search: Optional[str] = Query(None),
    date_from: Optional[date] = Query(None),
    date_to: Optional[date] = Query(None),
    country_code: Optional[str] = Query(None),
    sort: str = Query("taken_at_desc"),
    page: int = Query(1, ge=1),
    page_size: int = Query(200, ge=1, le=2000),
    cursor: Optional[str] = Query(None, description="Keyset cursor from previous page's next_cursor"),
) -> PhotoPage:
    filters = PhotoFilters(
        album_path=album_path, tag_id=tag_id, person_tag_id=person_tag_id,
        rating_min=rating_min, color_label=color_label, media_type=media_type,
        camera_id=camera_id, lens_id=lens_id, has_gps=has_gps,
        has_faces=has_faces, no_date=no_date, search=search,
        date_from=date_from, date_to=date_to, country_code=country_code,
    )
    base_q = await build_photo_query(filters, db)
    q = apply_sort(base_q, sort)

    total = await count_photos(filters, db, base_q=base_q)

    if cursor:
        q_page = apply_cursor(q, cursor).limit(page_size)
    else:
        q_page = q.offset((page - 1) * page_size).limit(page_size)

    rows = (await db.execute(q_page)).scalars().all()
    next_cur = encode_cursor(rows[-1], sort) if rows and len(rows) == page_size else None

    return PhotoPage(
        total=total,
        page=page,
        page_size=page_size,
        items=[PhotoSummary.model_validate(r) for r in rows],
        next_cursor=next_cur,
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


# ── Date inference helpers ──────────────────────────────────────────────────

# Regex patterns tried in priority order against the filename stem.
_DATE_PATTERNS: list[tuple[str, re.Pattern]] = [
    # YYYY-MM-DD or YYYY_MM_DD anywhere in name
    ("filename_ymd", re.compile(r"(?<!\d)(\d{4})[-_](\d{2})[-_](\d{2})(?!\d)")),
    # YYYYMMDD compact (only if year 1900-2099 and valid month/day)
    ("filename_yyyymmdd", re.compile(r"(?<!\d)((?:19|20)\d{2})(0[1-9]|1[0-2])(0[1-9]|[12]\d|3[01])(?!\d)")),
]

_YEAR_RE = re.compile(r"(?<!\d)((?:19|20)\d{2})(?!\d)")


def _infer_date(filename: str, album_path: str) -> tuple[datetime, str] | None:
    """Try to infer a UTC taken_at from filename then album_path.

    Returns (datetime, source_label) or None.
    """
    stem = filename.rsplit(".", 1)[0]

    for label, pat in _DATE_PATTERNS:
        m = pat.search(stem)
        if m:
            try:
                y, mo, d = int(m.group(1)), int(m.group(2)), int(m.group(3))
                dt = datetime(y, mo, d, tzinfo=timezone.utc)
                return dt, label
            except ValueError:
                continue

    # Folder year: look for YYYY component in album_path
    parts = [p for p in album_path.replace("\\", "/").split("/") if p]
    for part in reversed(parts):
        ym = _YEAR_RE.fullmatch(part)
        if ym:
            y = int(ym.group(1))
            return datetime(y, 1, 1, tzinfo=timezone.utc), "folder_year"
        # YYYY/MM
        ym2 = re.fullmatch(r"((?:19|20)\d{2})[/-](0[1-9]|1[0-2])", part)
        if ym2:
            try:
                return datetime(int(ym2.group(1)), int(ym2.group(2)), 1, tzinfo=timezone.utc), "folder_year_month"
            except ValueError:
                pass

    return None


@router.get("/infer-dates/preview")
async def infer_dates_preview(
    db: DB,
    limit: int = Query(200, ge=1, le=2000),
    offset: int = Query(0, ge=0),
) -> dict:
    """Return a paginated list of undated photos with their inferred dates.

    Does NOT write anything to the DB.
    """
    rows = (await db.execute(
        select(Photo.id, Photo.filename, Photo.album_path)
        .where(Photo.status == 1)
        .where(Photo.taken_at.is_(None))
        .order_by(Photo.id)
        .offset(offset)
        .limit(limit)
    )).fetchall()

    candidates = []
    for photo_id, filename, album_path in rows:
        result = _infer_date(filename, album_path)
        if result:
            dt, source = result
            candidates.append({
                "id": photo_id,
                "filename": filename,
                "album_path": album_path,
                "inferred_date": dt.isoformat(),
                "source": source,
            })

    total_undated = (await db.execute(
        select(func.count()).select_from(Photo)
        .where(Photo.status == 1)
        .where(Photo.taken_at.is_(None))
    )).scalar_one()

    return {
        "total_undated": total_undated,
        "offset": offset,
        "limit": limit,
        "candidates": candidates,
    }


class InferDatesApplyRequest(BaseModel):
    ids: list[int]


@router.post("/infer-dates/apply")
async def infer_dates_apply(payload: InferDatesApplyRequest, db: DB) -> dict:
    """Apply inferred dates to the given photo IDs (must still be undated).

    Re-computes the inference server-side so stale client data is harmless.
    Only updates photos that are still undated and have an inferable date.
    Marks updated photos as file_sync_dirty.
    """
    if not payload.ids:
        return {"updated": 0}

    rows = (await db.execute(
        select(Photo.id, Photo.filename, Photo.album_path)
        .where(Photo.id.in_(payload.ids))
        .where(Photo.taken_at.is_(None))
        .where(Photo.status == 1)
    )).fetchall()

    applied = 0
    for photo_id, filename, album_path in rows:
        result = _infer_date(filename, album_path)
        if result:
            dt, _ = result
            await db.execute(
                update(Photo).where(Photo.id == photo_id)
                .values(taken_at=dt, file_sync_dirty=True)
            )
            applied += 1

    await db.commit()
    return {"updated": applied, "skipped": len(rows) - applied}


@router.get("/timeline")
async def timeline(db: DB) -> dict:
    """Return photo counts bucketed by year and year+month, for the timeline page.

    Returns:
      years: [{year, count, months: [{month, count}]}]
    """
    from sqlalchemy import text as _text
    rows = (await db.execute(_text("""
        SELECT
            EXTRACT(YEAR  FROM taken_at)::int AS yr,
            EXTRACT(MONTH FROM taken_at)::int AS mo,
            COUNT(*)                          AS cnt
        FROM photos
        WHERE status = 1 AND taken_at IS NOT NULL
        GROUP BY yr, mo
        ORDER BY yr DESC, mo DESC
    """))).fetchall()

    years_dict: dict[int, dict] = {}
    for yr, mo, cnt in rows:
        if yr not in years_dict:
            years_dict[yr] = {"year": yr, "count": 0, "months": []}
        years_dict[yr]["count"] += cnt
        years_dict[yr]["months"].append({"month": mo, "count": cnt})

    return {"years": list(years_dict.values())}


@router.get("/cameras")
async def list_cameras(db: DB) -> list[dict]:
    """All cameras that have at least one photo, for filter dropdowns."""
    from fernkam.db.models.photos import Camera
    rows = (await db.execute(
        select(Camera.id, Camera.make, Camera.model)
        .where(Camera.id.in_(select(Photo.camera_id).where(Photo.camera_id.is_not(None)).distinct()))
        .order_by(Camera.make.asc(), Camera.model.asc())
    )).fetchall()
    return [{"id": r[0], "make": r[1], "model": r[2],
             "label": f"{r[1] or ''} {r[2] or ''}".strip()} for r in rows]


@router.get("/lenses")
async def list_lenses(db: DB) -> list[dict]:
    """All lenses that have at least one photo, for filter dropdowns."""
    from fernkam.db.models.photos import Lens
    rows = (await db.execute(
        select(Lens.id, Lens.make, Lens.model)
        .where(Lens.id.in_(select(Photo.lens_id).where(Photo.lens_id.is_not(None)).distinct()))
        .order_by(Lens.make.asc(), Lens.model.asc())
    )).fetchall()
    return [{"id": r[0], "make": r[1], "model": r[2],
             "label": f"{r[1] or ''} {r[2] or ''}".strip()} for r in rows]


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
    limit: int = Query(5000, le=200000),
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
    """Core detect + similarity-suggest logic. Returns (face_outs, suggested_count).

    Pipeline:
      1. Decode image in default thread pool (parallelisable across scan slots).
      2. Run GPU inference in single-worker FACE_EXECUTOR (serialised).
      3. Update embeddings for overlapping existing faces (e.g. XMP-restored).
      4. Batch ignored + confirmed similarity queries for all new detections.
      5. Persist Face records with crop thumbnails.
    """
    from fernkam.face_processor import (
        FACE_EXECUTOR, decode_image, run_face_inference,
        embedding_to_bytes, embedding_to_pgvector,
        find_similar_pg, find_similar_pg_batch,
    )
    from fernkam.thumbnails import photo_disk_path, RAW_EXTENSIONS
    from fernkam.config import get_settings
    import cv2 as _cv2
    from datetime import datetime, timezone

    settings = get_settings()
    AUTO_CONFIRM_THRESH = settings.auto_confirm_thresh
    SUGGEST_THRESH = settings.suggest_thresh

    photo = (await db.execute(select(Photo).where(Photo.id == photo_id))).scalar_one_or_none()
    if not photo:
        raise HTTPException(404, "Photo not found")

    if photo.media_type != "image":
        return [], 0

    src = photo_disk_path(photo.album_path, photo.filename)
    if not src.exists():
        await db.execute(update(Photo).where(Photo.id == photo_id).values(faces_scanned_at=datetime.now(timezone.utc)))
        await db.commit()
        return [], 0

    if src.suffix.lower() in RAW_EXTENSIONS:
        await db.execute(update(Photo).where(Photo.id == photo_id).values(faces_scanned_at=datetime.now(timezone.utc)))
        await db.commit()
        return [], 0

    loop = asyncio.get_event_loop()

    # ── Step 1: Decode image (default pool — runs in parallel with other decodes) ──
    img_bgr = await loop.run_in_executor(None, decode_image, src)
    if img_bgr is None:
        await db.execute(update(Photo).where(Photo.id == photo_id).values(faces_scanned_at=datetime.now(timezone.utc)))
        await db.commit()
        return [], 0

    # ── Step 2: GPU inference (single-worker FACE_EXECUTOR — keeps VRAM predictable) ──
    detections = await loop.run_in_executor(FACE_EXECUTOR, run_face_inference, img_bgr)
    if not detections:
        await db.execute(update(Photo).where(Photo.id == photo_id).values(faces_scanned_at=datetime.now(timezone.utc)))
        await db.commit()
        return [], 0

    # Load existing faces on this photo so we can update embeddings on XMP-restored records.
    existing_faces = (await db.execute(
        select(Face).where(Face.photo_id == photo_id)
    )).scalars().all()

    def _overlaps(det: dict, face) -> bool:
        if face.x is None:
            return False
        ix1 = max(det["x"], face.x)
        iy1 = max(det["y"], face.y)
        ix2 = min(det["x"] + det["w"], face.x + face.w)
        iy2 = min(det["y"] + det["h"], face.y + face.h)
        inter = max(0, ix2 - ix1) * max(0, iy2 - iy1)
        if inter == 0:
            return False
        union = det["w"] * det["h"] + face.w * face.h - inter
        return (inter / union) > 0.4

    # ── Step 3: Handle detections that overlap existing face records ──
    MIN_DET_SCORE = settings.min_det_score
    new_dets: list[dict] = []
    for det in detections:
        if MIN_DET_SCORE > 0 and det["score"] < MIN_DET_SCORE:
            continue
        matched = next((f for f in existing_faces if _overlaps(det, f)), None)
        if matched is not None:
            matched.det_score = round(float(det["score"]), 4)
            if matched.embedding is None:
                matched.embedding = embedding_to_bytes(det["embedding"])
                matched.embedding_v = embedding_to_pgvector(det["embedding"])
                if matched.person_tag_id is None:
                    m = await find_similar_pg(
                        db, det["embedding"], confirmed_only=True,
                        k=1, min_score=SUGGEST_THRESH,
                    )
                    if m:
                        matched.person_tag_id = m[0]["person_tag_id"]
                        matched.status = "confirmed" if m[0]["score"] >= AUTO_CONFIRM_THRESH else "suggested"
            await db.flush()
        else:
            new_dets.append(det)

    if not new_dets:
        await db.commit()
        await db.execute(update(Photo).where(Photo.id == photo_id).values(faces_scanned_at=datetime.now(timezone.utc)))
        await db.commit()
        return [], 0

    # ── Step 4: Batch similarity lookups — 2 queries total, not 2N ──
    new_embs = [d["embedding"] for d in new_dets]

    ign_batch = await find_similar_pg_batch(
        db, new_embs, ignored_only=True, k=1, min_score=AUTO_CONFIRM_THRESH,
    )

    non_ign_idx = [i for i, r in enumerate(ign_batch) if not r]
    conf_for_non_ign = await find_similar_pg_batch(
        db, [new_embs[i] for i in non_ign_idx],
        confirmed_only=True, k=1, min_score=SUGGEST_THRESH,
    ) if non_ign_idx else []

    conf_batch: list[list[dict]] = [[] for _ in new_dets]
    for pos, orig_i in enumerate(non_ign_idx):
        conf_batch[orig_i] = conf_for_non_ign[pos]

    # ── Step 5: Create Face records ──
    new_faces: list[tuple[Face, float | None]] = []
    suggested = 0

    for i, det in enumerate(new_dets):
        if ign_batch[i]:
            status = "ignored"
            person_tag_id = None
            score: float | None = round(float(det["score"]), 2)
        elif conf_batch[i]:
            best = conf_batch[i][0]
            person_tag_id = best["person_tag_id"]
            match_score = best["score"]
            status = "confirmed" if match_score >= AUTO_CONFIRM_THRESH else "suggested"
            score = round(match_score, 2)
            suggested += 1
        else:
            status = "unconfirmed"
            person_tag_id = None
            score = round(float(det["score"]), 2)

        crop_bytes: bytes | None = None
        h_img, w_img = img_bgr.shape[:2]
        pad = int(max(det["w"], det["h"]) * 0.2)
        x1 = max(0, det["x"] - pad)
        y1 = max(0, det["y"] - pad)
        x2 = min(w_img, det["x"] + det["w"] + pad)
        y2 = min(h_img, det["y"] + det["h"] + pad)
        if x2 > x1 and y2 > y1:
            crop = _cv2.resize(img_bgr[y1:y2, x1:x2], (200, 200), interpolation=_cv2.INTER_AREA)
            ok, buf = _cv2.imencode(".webp", crop, [_cv2.IMWRITE_WEBP_QUALITY, 85])
            if ok:
                crop_bytes = bytes(buf)

        face = Face(
            photo_id=photo_id,
            x=det["x"], y=det["y"], w=det["w"], h=det["h"],
            embedding=embedding_to_bytes(det["embedding"]),
            embedding_v=embedding_to_pgvector(det["embedding"]),
            status=status,
            person_tag_id=person_tag_id,
            crop_data=crop_bytes,
            det_score=round(float(det["score"]), 4),
            blur_score=round(float(det.get("blur_score", 0.0)), 2),
            best_match_score=round(float(score), 4) if score is not None and status in ("suggested", "confirmed") else None,
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

    await db.execute(update(Photo).where(Photo.id == photo_id).values(faces_scanned_at=datetime.now(timezone.utc)))
    await db.commit()
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


@router.post("/batch-detect-all")
async def batch_detect_all_faces(db: DB) -> dict:
    """Detect faces in all unscanned photos.

    Delegates to the /sync/scan-faces background task and returns immediately
    with a task_id. The blocking serial implementation has been removed to
    prevent long-running HTTP connections on large libraries.
    """
    from fernkam.api.routers.sync import scan_faces as _scan_faces
    from fernkam.api.routers.sync import ScanFacesRequest

    result = await _scan_faces(db, ScanFacesRequest())
    return result


@router.post("/{photo_id}/trash")
async def trash_photo(photo_id: int, db: DB) -> dict:
    """Move the photo file to system Trash and mark it inactive in the DB."""
    from fernkam.thumbnails import photo_disk_path

    row = (await db.execute(select(Photo).where(Photo.id == photo_id))).scalar_one_or_none()
    if not row:
        raise HTTPException(404, "Photo not found")

    src = photo_disk_path(row.album_path, row.filename)
    if src.exists():
        try:
            from send2trash import send2trash
            loop = asyncio.get_event_loop()
            await loop.run_in_executor(None, send2trash, str(src))
        except Exception as exc:
            raise HTTPException(500, f"Failed to trash file: {exc}")

    await db.execute(update(Photo).where(Photo.id == photo_id).values(status=0))
    await db.commit()
    return {"ok": True, "filename": row.filename}


class BatchEditRequest(PhotoUpdate):
    ids: list[int]


@router.post("/batch-edit", response_model=dict)
async def batch_edit_photos(payload: BatchEditRequest, db: DB) -> dict:
    """Apply the same metadata changes to multiple photos at once.

    Only non-null fields (besides ``ids``) are written. Marks all affected
    photos as ``file_sync_dirty`` so the next XMP sync picks them up.
    """
    updates = payload.model_dump(exclude_none=True, exclude={"ids"})
    if not updates:
        raise HTTPException(400, "No fields to update")
    if not payload.ids:
        raise HTTPException(400, "No photo IDs provided")

    updates["file_sync_dirty"] = True
    await db.execute(
        update(Photo).where(Photo.id.in_(payload.ids)).values(**updates)
    )
    await db.commit()
    return {"updated": len(payload.ids)}


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
