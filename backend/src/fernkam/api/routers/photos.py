from __future__ import annotations

import asyncio
import re
import time as _time
from datetime import date, datetime, timezone
from typing import Optional

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import exists, func, select, text, update
from sqlalchemy.orm import selectinload

from fernkam.api.deps import DB
from fernkam.api.schemas import BatchDetectResult, FaceOut, PhotoDetail, PhotoPage, PhotoSummary, PhotoUpdate
from fernkam.db.models.photos import Face, Photo, PhotoTag
from fernkam.services.photo_query import PhotoFilters, apply_cursor, apply_sort, build_photo_query, count_photos, encode_cursor

router = APIRouter()


def outside_prep():
    """Faces aren't detected in AA_RAW/AB_TO_SORT/AC_SORTED (Settings.prep_folders)."""
    from fernkam.config import get_settings
    return func.split_part(func.btrim(Photo.album_path, "/"), "/", 1).not_in(get_settings().prep_folders)


@router.get("/unscanned-count")
async def unscanned_count(db: DB) -> dict:
    """Count of photos not yet run through InsightFace detection."""
    n = (await db.execute(
        select(func.count()).select_from(Photo)
        .where(Photo.status == 1)
        .where(Photo.media_type == "image")
        .where(Photo.faces_scanned_at.is_(None))
        .where(outside_prep())
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
    unnamed_faces: Optional[bool] = Query(None),
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
        has_faces=has_faces, unnamed_faces=unnamed_faces, no_date=no_date, search=search,
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


# Dropdown lists change only when a new camera/lens is imported, so a short
# TTL cache (single entry each, can't leak like a per-filter cache) avoids
# re-running the query on every filter-panel open.
_dropdown_cache: dict[str, tuple[list[dict], float]] = {}
_DROPDOWN_TTL = 60.0


@router.get("/cameras")
async def list_cameras(db: DB) -> list[dict]:
    """All cameras that have at least one photo, for filter dropdowns."""
    cached = _dropdown_cache.get("cameras")
    if cached and _time.monotonic() < cached[1]:
        return cached[0]
    from fernkam.db.models.photos import Camera
    # EXISTS (index-seekable per candidate camera) instead of
    # IN (SELECT DISTINCT camera_id FROM photos) — the latter forces a full
    # scan+dedup over the whole photos table just to populate a ~20-row list.
    rows = (await db.execute(
        select(Camera.id, Camera.make, Camera.model)
        .where(exists(select(1).where(Photo.camera_id == Camera.id)))
        .order_by(Camera.make.asc(), Camera.model.asc())
    )).fetchall()
    result = [{"id": r[0], "make": r[1], "model": r[2],
               "label": f"{r[1] or ''} {r[2] or ''}".strip()} for r in rows]
    _dropdown_cache["cameras"] = (result, _time.monotonic() + _DROPDOWN_TTL)
    return result


@router.get("/lenses")
async def list_lenses(db: DB) -> list[dict]:
    """All lenses that have at least one photo, for filter dropdowns."""
    cached = _dropdown_cache.get("lenses")
    if cached and _time.monotonic() < cached[1]:
        return cached[0]
    from fernkam.db.models.photos import Lens
    rows = (await db.execute(
        select(Lens.id, Lens.make, Lens.model)
        .where(exists(select(1).where(Photo.lens_id == Lens.id)))
        .order_by(Lens.make.asc(), Lens.model.asc())
    )).fetchall()
    result = [{"id": r[0], "make": r[1], "model": r[2],
               "label": f"{r[1] or ''} {r[2] or ''}".strip()} for r in rows]
    _dropdown_cache["lenses"] = (result, _time.monotonic() + _DROPDOWN_TTL)
    return result


@router.get("/batch", response_model=list[PhotoSummary])
async def get_photos_batch(
    db: DB,
    ids: str = Query(..., description="Comma-separated photo ids"),
) -> list[PhotoSummary]:
    """Batch fetch by id in one round-trip — e.g. the map view's per-pin photo
    panel used to fire one GET /{photo_id} per photo (up to 50 per click)."""
    try:
        id_list = [int(x) for x in ids.split(",") if x.strip()][:200]
    except ValueError:
        raise HTTPException(400, "ids must be a comma-separated list of integers")
    if not id_list:
        return []
    rows = (await db.execute(
        select(Photo).where(Photo.id.in_(id_list)).where(Photo.status == 1)
    )).scalars().all()
    by_id = {r.id: r for r in rows}
    # Preserve the requested order — callers (e.g. the map panel) rely on it.
    return [PhotoSummary.model_validate(by_id[i]) for i in id_list if i in by_id]


# NOTE: /{photo_id} must stay below every literal single-segment GET route
# above (timeline/cameras/lenses/batch) — FastAPI matches routes in
# registration order, so a literal route registered after this would be
# shadowed (any GET to e.g. /api/photos/timeline would 422 trying to parse "timeline" as
# photo_id). Multi-segment routes like /{photo_id}/tags are unaffected.
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
    detail.unverified_tag_ids = [pt.tag_id for pt in row.photo_tags
                                 if pt.tag and pt.verified_at is None and not pt.tag.is_person and not pt.tag.is_fact]
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


class BurstRequest(BaseModel):
    ids: list[int]


_sharpness_cache: dict[int, Optional[float]] = {}


@router.post("/bursts")
async def photo_bursts(req: BurstRequest, db: DB) -> dict:
    """Group the photos being culled into bursts and rank each burst by
    sharpness (see fernkam/bursts.py). Only photos in bursts of 2+ are listed:
    {id: {burst: first frame's id, size, rank (0 = sharpest), focus_stack}}."""
    from concurrent.futures import ThreadPoolExecutor
    from fernkam import bursts as B
    from fernkam.thumbnails import read_thumbnail_from_disk

    rows = (await db.execute(text("""
        SELECT id, album_path, filename, taken_at,
               exif->>'SubSecTimeOriginal' AS subsec, exif->>'FocusShiftShooting' AS fs
        FROM photos WHERE id = ANY(:ids)"""), {"ids": req.ids[:10000]})).all()
    # PureRAW's JPGs drop FocusShiftShooting, so read it from the RAW beside
    # the JPG's folder (AA_RAW/<shoot>/x.NEF for AA_RAW/<shoot>/jpg/x.jpg).
    shoot = lambda a: a[:-4] if a.endswith("/jpg") else a
    stem = lambda f: f.rsplit(".", 1)[0].lower()
    raw_fs = {(r.album_path, stem(r.filename)): r.fs for r in (await db.execute(text("""
        SELECT album_path, filename, exif->>'FocusShiftShooting' AS fs FROM photos
        WHERE album_path = ANY(:a) AND exif->>'FocusShiftShooting' IS NOT NULL"""),
        {"a": list({shoot(r.album_path) for r in rows})})).all()}
    focus = {r.id: str(r.fs or raw_fs.get((shoot(r.album_path), stem(r.filename))) or "0") not in ("0", "Off", "off")
             for r in rows}

    heads = B.group([(r.id, r.album_path, r.taken_at.timestamp() + B.subsec(r.subsec) if r.taken_at else None)
                     for r in rows])
    members: dict[int, list] = {}
    for r in sorted(rows, key=lambda r: (r.taken_at is None, r.taken_at, B.subsec(r.subsec))):
        members.setdefault(heads[r.id], []).append(r.id)
    bursts = {h: ids for h, ids in members.items() if len(ids) > 1}
    stacks = {h for h, ids in bursts.items() if any(focus[i] for i in ids)}

    need = [i for h, ids in bursts.items() if h not in stacks for i in ids if i not in _sharpness_cache]

    def score(pid: int) -> tuple[int, Optional[float]]:
        data = read_thumbnail_from_disk(pid, "lg") or read_thumbnail_from_disk(pid, "md")
        return pid, B.sharpness(data) if data else None

    if need:
        loop = asyncio.get_running_loop()
        with ThreadPoolExecutor(8) as ex:
            _sharpness_cache.update(await asyncio.gather(*[loop.run_in_executor(ex, score, i) for i in need]))

    out = {}
    for h, ids in bursts.items():
        order = ids if h in stacks else sorted(ids, key=lambda i: -(_sharpness_cache.get(i) or 0.0))
        for rank, i in enumerate(order):
            out[i] = {"burst": h, "size": len(ids), "rank": rank, "focus_stack": h in stacks}
    return {"bursts": out}


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


@router.post("/{photo_id}/tags/{tag_id}", status_code=204)
async def add_photo_tag(photo_id: int, tag_id: int, db: DB) -> None:
    """Adding a tag yourself is a decision, so it counts as approved (Tag
    Review), including when the photo already had it unverified."""
    await db.execute(text("""
        INSERT INTO photo_tags (photo_id, tag_id, verified_at) VALUES (:p, :t, now())
        ON CONFLICT (photo_id, tag_id)
            DO UPDATE SET verified_at = COALESCE(photo_tags.verified_at, now())
    """), {"p": photo_id, "t": tag_id})
    await db.execute(text("DELETE FROM tag_suggestions WHERE photo_id = :p AND tag_id = :t"),
                     {"p": photo_id, "t": tag_id})
    await db.execute(text("DELETE FROM tag_rejections WHERE photo_id = :p AND tag_id = :t"),
                     {"p": photo_id, "t": tag_id})
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
        q = q.where(Photo.album_path.like(f"{album_path.lstrip('/')}%"))
    if tag_id is not None:
        q = q.where(Photo.id.in_(select(PhotoTag.photo_id).where(PhotoTag.tag_id == tag_id)))
    q = q.limit(limit)
    rows = (await db.execute(q)).fetchall()
    return [
        {"id": r.id, "lat": float(r.latitude), "lon": float(r.longitude),
         "filename": r.filename, "taken_at": r.taken_at.isoformat() if r.taken_at else None}
        for r in rows
    ]


def _encode_face_crop(region) -> bytes | None:
    """Blocking: resize an already-cropped face region to 200x200 WebP. Call via run_in_executor."""
    import cv2
    crop = cv2.resize(region, (200, 200), interpolation=cv2.INTER_AREA)
    ok, buf = cv2.imencode(".webp", crop, [cv2.IMWRITE_WEBP_QUALITY, 85])
    return bytes(buf) if ok else None


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
    from datetime import datetime, timezone

    settings = get_settings()
    from fernkam.api.routers.faces._helpers import _resolve_sensitivity, _sensitivity_to_thresholds
    AUTO_CONFIRM_THRESH, _margin, _floor = _sensitivity_to_thresholds(await _resolve_sensitivity(db))
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

    # Hand the connection back before the slow part. Everything so far was a
    # read, but the session's open transaction would otherwise pin a pooled
    # connection "idle in transaction" through the decode and the wait for the
    # single FACE_EXECUTOR worker — and a scan runs FERNKAM_FACE_CONCURRENCY
    # (default max(4, CPU count)) of these at once, draining the 50-connection
    # pool the UI shares for no throughput gain.
    await db.commit()

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
                        matched.confirmed_by = "auto" if matched.status == "confirmed" else None
            await db.flush()
        else:
            new_dets.append(det)

    if not new_dets:
        # matched.* changes on existing faces were already flushed in the loop
        # above — one commit here covers those plus this update.
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

        h_img, w_img = img_bgr.shape[:2]
        pad = int(max(det["w"], det["h"]) * 0.2)
        x1 = max(0, det["x"] - pad)
        y1 = max(0, det["y"] - pad)
        x2 = min(w_img, det["x"] + det["w"] + pad)
        y2 = min(h_img, det["y"] + det["h"] + pad)
        crop_bytes: bytes | None = None
        if x2 > x1 and y2 > y1:
            # Resize+encode is CPU-bound; the decode/inference above are
            # already offloaded — this was the one step still on the loop.
            crop_bytes = await loop.run_in_executor(
                None, _encode_face_crop, img_bgr[y1:y2, x1:x2]
            )

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
            confirmed_by="auto" if status == "confirmed" else None,
        )
        db.add(face)
        new_faces.append((face, score))

    # flush (not commit) — makes the new rows visible to this session's own
    # queries below without ending the transaction, so the faces_scanned_at
    # update can land in the same commit instead of a second round-trip.
    await db.flush()

    # Single batched reload (with person_tag eager-loaded) instead of two
    # queries per face — flush() above already populated ids/columns on the
    # `new_faces` objects, so db.refresh() per-face was redundant too.
    face_ids = [face.id for face, _ in new_faces]
    reloaded_by_id = {
        f.id: f for f in (await db.execute(
            select(Face).options(selectinload(Face.person_tag)).where(Face.id.in_(face_ids))
        )).scalars().all()
    }

    results: list[FaceOut] = []
    for face, score in new_faces:
        reloaded = reloaded_by_id[face.id]
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


@router.post("/{photo_id}/reveal", response_model=dict)
async def reveal_in_file_manager(photo_id: int, db: DB) -> dict:
    """Open the OS file manager with this photo selected.

    Local desktop app — the backend and the person clicking are on the same
    machine, which is the only reason this is reasonable at all.
    """
    import subprocess
    import sys
    from fernkam.thumbnails import photo_disk_path

    row = (await db.execute(
        select(Photo.album_path, Photo.filename).where(Photo.id == photo_id)
    )).first()
    if not row:
        raise HTTPException(404, "Photo not found")
    path = photo_disk_path(row[0], row[1])
    if not path.exists():
        raise HTTPException(404, f"File not on disk: {path}")

    try:
        if sys.platform == "win32":
            # explorer always returns exit code 1 even on success, so its
            # return code is deliberately not checked.
            subprocess.Popen(["explorer", "/select,", str(path)])
        elif sys.platform == "darwin":
            subprocess.Popen(["open", "-R", str(path)])
        else:
            subprocess.Popen(["xdg-open", str(path.parent)])
    except OSError as exc:
        raise HTTPException(500, f"Could not open file manager: {exc}")
    return {"ok": True, "path": str(path)}


# ── Rotate / flip ────────────────────────────────────────────────────────────

# Pillow's reading of each EXIF Orientation (ImageOps.exif_transpose): what it
# does to the stored pixels to show them. Thumbnails are drawn the same way.
_EXIF_SHOWN = {1: (), 2: ("FLIP_LEFT_RIGHT",), 3: ("ROTATE_180",), 4: ("FLIP_TOP_BOTTOM",),
               5: ("TRANSPOSE",), 6: ("ROTATE_270",), 7: ("TRANSVERSE",), 8: ("ROTATE_90",)}
ORIENT_OPS = {"left": "ROTATE_90", "right": "ROTATE_270", "180": "ROTATE_180",
              "flip_h": "FLIP_LEFT_RIGHT", "flip_v": "FLIP_TOP_BOTTOM"}
VIDEO_DEGREES = {"left": 270, "right": 90, "180": 180}   # QuickTime Rotation turns clockwise


def orientation_after(current: Optional[int], op: str) -> int:
    """The EXIF Orientation that shows the picture turned or flipped by op
    from how it shows now. Found on a 3x2 probe rather than a hand-made table,
    so it cannot disagree with how thumbnails are drawn."""
    from PIL import Image
    T = Image.Transpose
    probe = Image.frombytes("L", (3, 2), bytes(range(6)))

    def shown(o: int):
        im = probe
        for t in _EXIF_SHOWN[o]:
            im = im.transpose(T[t])
        return im
    want = shown(current if current in _EXIF_SHOWN else 1).transpose(T[ORIENT_OPS[op]])
    return next(n for n in _EXIF_SHOWN if shown(n).size == want.size and shown(n).tobytes() == want.tobytes())


class OrientRequest(BaseModel):
    photo_ids: list[int]
    op: str   # left | right | 180 | flip_h | flip_v


@router.post("/orient", response_model=dict)
async def orient(req: OrientRequest, db: DB) -> dict:
    """Rotate or flip without touching pixels: the EXIF Orientation tag for
    images (lossless, even for RAW and TIFF), the QuickTime Rotation tag for
    videos (which cannot be flipped). The thumbnail is redrawn at once; faces
    and image-model vectors, taken from the old view, are redone in the
    background."""
    from fernkam.importers.filesystem import _forget_pixel_derived_data
    from fernkam.metadata_sync import _get_et_session
    from fernkam.task_manager import task_manager
    from fernkam.thumbnails import photo_disk_path, refresh_thumbnail

    if req.op not in ORIENT_OPS:
        raise HTTPException(400, f"op must be one of: {', '.join(ORIENT_OPS)}")
    task_manager.ensure_no_file_task()   # a scan reading these files mid-write would see a half-written file
    et = _get_et_session()
    if et is None:
        raise HTTPException(500, "exiftool is not available")
    rows = (await db.execute(select(Photo.id, Photo.album_path, Photo.filename, Photo.media_type, Photo.orientation)
                             .where(Photo.id.in_(req.photo_ids), Photo.status == 1))).all()
    loop = asyncio.get_running_loop()
    done, redo, errors = [], [], []
    for r in rows:
        path = photo_disk_path(r.album_path, r.filename)
        new_orient = r.orientation
        if r.media_type == "video":
            if req.op not in VIDEO_DEGREES:
                errors.append(f"{r.filename}: a video can be rotated but not flipped")
                continue
            cur = (await loop.run_in_executor(None, et.execute, ["-n", "-s3", "-Rotation", str(path)]) or "").strip()
            args = [f"-Rotation={(int(float(cur or 0)) + VIDEO_DEGREES[req.op]) % 360}"]
        else:
            new_orient = orientation_after(r.orientation, req.op)
            args = ["-n", f"-Orientation={new_orient}"]
        out = await loop.run_in_executor(None, et.execute, [*args, "-overwrite_original", str(path)])
        if not out or "1 image files updated" not in out:
            errors.append(f"{r.filename}: {(out or 'exiftool failed').strip()[:200]}")
            continue
        await _record_file_write(db, r.id, path, orientation=new_orient)
        if await loop.run_in_executor(None, refresh_thumbnail, r.id, path) == "changed":
            redo.append(r.id)
        done.append(r.id)
    if redo:
        await _forget_pixel_derived_data(db, redo, outside=False)
    await db.commit()
    if redo:
        asyncio.create_task(_redo_after_turn(redo), name="fernkam-reorient")
    return {"turned": len(done), "ids": done, "errors": errors}


async def _redo_after_turn(ids: list[int]) -> None:
    """Search vectors and faces for turned photos, from the new view."""
    import logging
    from fernkam import clip_embed, embed_index
    from fernkam.api.routers.semantic import embed_rows
    from fernkam.config import get_settings
    from fernkam.db.session import async_session_factory
    try:
        async with async_session_factory() as bdb:
            rows = (await bdb.execute(text(
                "SELECT id, album_path, filename, media_type FROM photos WHERE id = ANY(:ids)"), {"ids": ids})).all()
            if clip_embed.models_downloaded():
                await embed_rows(bdb, [(r.id, r.album_path, r.filename) for r in rows])
            await embed_index.refresh_photos(bdb, ids)
            for r in rows:
                if r.media_type == "image" and not get_settings().is_prep(r.album_path):
                    await _detect_and_suggest(r.id, bdb)
    except Exception:  # noqa: BLE001 — the scan and Discover indexer catch up later
        logging.getLogger(__name__).warning("redoing turned photos failed", exc_info=True)


async def _record_file_write(db, photo_id: int, path, **values) -> None:
    """fernKam just rewrote this file's metadata: keep its hash (move
    matching, duplicates), size and sync time current, so the next scan
    doesn't take it for an edit made in another program. Does not commit."""
    import os
    from fernkam.importers.filesystem import _sha256_path
    loop = asyncio.get_running_loop()
    st = await loop.run_in_executor(None, os.stat, path)
    sha = await loop.run_in_executor(None, _sha256_path, path)
    await db.execute(update(Photo).where(Photo.id == photo_id).values(
        file_size=st.st_size, sha256=sha,
        file_modified_at_sync=datetime.fromtimestamp(st.st_mtime, tz=timezone.utc), **values))


# ── File metadata: rights, credit, usage notes, and everything exiftool sees ──
# Written straight into the file (Lightroom, digiKam and Photoshop read these),
# never clearing a field: only fields given a new non-empty value are written.
# Usage notes are lines in XMP Rights "Usage Terms", only ever appended to, and
# a fact tag Usage › <who> makes them findable. Title, caption, rating and tags
# are fernKam's own (edited in the app, written at write-back), so not here.

FILE_FIELDS = {   # field -> tags written (the first is the one read back)
    "creator": ["XMP-dc:Creator", "EXIF:Artist"],
    "copyright": ["XMP-dc:Rights", "EXIF:Copyright"],
    "credit": ["XMP-photoshop:Credit"],
    "source": ["XMP-photoshop:Source"],
    "web_statement": ["XMP-xmpRights:WebStatement"],
    "instructions": ["XMP-photoshop:Instructions"],
}


def _first(v):
    return ", ".join(map(str, v)) if isinstance(v, list) else (str(v) if v is not None else "")


def read_file_fields(et, path) -> dict:
    """The FILE_FIELDS' current values and the usage notes (as lines).
    JSON, because exiftool's plain output turns line breaks into "."."""
    import json
    tags = [t for ts in FILE_FIELDS.values() for t in ts] + ["XMP-xmpRights:UsageTerms"]
    out = et.execute(["-j", "-G1", *[f"-{t}" for t in tags], str(path)])
    d = json.loads(out)[0] if out and out.strip().startswith("[") else {}
    vals = {f: next((_first(d[t]) for t in ts if d.get(t) not in (None, "")), "") for f, ts in FILE_FIELDS.items()}
    vals["usage"] = [x for x in _first(d.get("XMP-xmpRights:UsageTerms")).splitlines() if x.strip()]
    return vals


def write_file_fields(et, path, fields: dict, usage_line: Optional[str] = None) -> Optional[str]:
    """Write the given non-empty fields and append one usage line, in one
    exiftool call; the error, if any. -E carries line breaks as &#xa;
    (exiftool's session reads one argument per line), so every value is
    entity-escaped."""
    def esc(x: str) -> str:
        return x.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    args = ["-E"]
    for f, v in fields.items():
        if f in FILE_FIELDS and (v := " ".join(str(v).split())):
            args += [f"-{t}={esc(v)}" for t in FILE_FIELDS[f]]
    if usage_line:
        lines = [*read_file_fields(et, path)["usage"], usage_line]
        args.append("-XMP-xmpRights:UsageTerms=" + "&#xa;".join(esc(x) for x in lines))
    if len(args) == 1:
        return None
    out = et.execute([*args, "-overwrite_original", str(path)])
    return None if out and "1 image files updated" in out else (out or "exiftool failed").strip()[:200]


async def _photo_path(db, photo_id: int):
    from fernkam.thumbnails import photo_disk_path
    row = (await db.execute(select(Photo.album_path, Photo.filename).where(Photo.id == photo_id))).first()
    if not row:
        raise HTTPException(404, "Photo not found")
    return photo_disk_path(row.album_path, row.filename)


@router.get("/{photo_id}/file-fields", response_model=dict)
async def file_fields(photo_id: int, db: DB) -> dict:
    from fernkam.metadata_sync import _get_et_session
    path, et = await _photo_path(db, photo_id), _get_et_session()
    return await asyncio.get_running_loop().run_in_executor(None, read_file_fields, et, path) if et else {}


@router.get("/{photo_id}/all-metadata", response_model=dict)
async def all_metadata(photo_id: int, db: DB) -> dict:
    """Everything exiftool reads from the file, by group ("EXIF:IFD0", "XMP-dc"…)."""
    import json
    from fernkam.metadata_sync import _get_et_session
    path, et = await _photo_path(db, photo_id), _get_et_session()
    out = await asyncio.get_running_loop().run_in_executor(None, et.execute, ["-j", "-G1", "-a", "-s", str(path)]) if et else None
    d = json.loads(out)[0] if out and out.strip().startswith("[") else {}
    groups: dict = {}
    for key, v in d.items():
        g, _, tag = key.rpartition(":")
        groups.setdefault(g or "File", {})[tag] = _first(v)
    return {"groups": groups}


class UsageNote(BaseModel):
    when: date
    who: str
    usage: str


class FileFieldsEdit(BaseModel):
    photo_ids: list[int]
    fields: dict[str, str] = {}
    usage: Optional[UsageNote] = None


@router.post("/file-fields", response_model=dict)
async def edit_file_fields(req: FileFieldsEdit, db: DB) -> dict:
    """Write rights/credit fields and/or append a usage note to each photo's file."""
    from fernkam.metadata_sync import _get_et_session
    from fernkam.sync_merge import ensure_tag_path
    from fernkam.task_manager import task_manager
    from fernkam.thumbnails import photo_disk_path

    unknown = set(req.fields) - set(FILE_FIELDS)
    if unknown:
        raise HTTPException(400, f"Unknown field(s): {', '.join(sorted(unknown))}")
    line = who = None
    if req.usage:
        who, what = (" ".join(x.split()) for x in (req.usage.who, req.usage.usage))
        if not who or not what:
            raise HTTPException(400, "A usage note needs who it was for and what use was approved")
        line = f"{req.usage.when.isoformat()} — {who}: {what}"
    task_manager.ensure_no_file_task()
    et = _get_et_session()
    if et is None:
        raise HTTPException(500, "exiftool is not available")
    rows = (await db.execute(select(Photo.id, Photo.album_path, Photo.filename)
                             .where(Photo.id.in_(req.photo_ids), Photo.status == 1))).all()
    loop = asyncio.get_running_loop()
    done, errors = [], []
    for r in rows:
        path = photo_disk_path(r.album_path, r.filename)
        if err := await loop.run_in_executor(None, write_file_fields, et, path, req.fields, line):
            errors.append(f"{r.filename}: {err}")
            continue
        await _record_file_write(db, r.id, path)
        done.append(r.id)
    if done and who:
        tag = await ensure_tag_path(db, ["Usage", who], {})
        await db.execute(text("UPDATE tags SET is_fact = true WHERE path @> CAST(:p AS ltree)"), {"p": str(tag.path)})
        await db.execute(text("""
            INSERT INTO photo_tags (photo_id, tag_id, verified_at) SELECT unnest(CAST(:ids AS bigint[])), :t, now()
            ON CONFLICT (photo_id, tag_id) DO UPDATE SET verified_at = COALESCE(photo_tags.verified_at, now())
        """), {"ids": done, "t": tag.id})
        await db.execute(text("UPDATE photos SET file_sync_dirty = true WHERE id = ANY(:ids)"), {"ids": done})
    await db.commit()
    return {"written": len(done), "usage_line": line, "errors": errors}
