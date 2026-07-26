"""Face suggestion, auto-confirm, and review-queue endpoints."""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Body, Query
from sqlalchemy import select, update
from sqlalchemy.orm import selectinload

from fernkam.api.deps import DB
from fernkam.db.models.photos import Face, Photo, Tag

from ._helpers import (
    FACE_SENSITIVITY_KEY,
    _auto_confirm_sweep,
    _make_face_out,
    _rebuild_person_centroids,
    _resolve_person_min_dates,
    _resolve_sensitivity,
    _sensitivity_to_thresholds,
)

router = APIRouter()


@router.get("/sensitivity")
async def get_sensitivity(db: DB) -> dict:
    """The single auto-confirm aggressiveness knob (0=cautious, 1=aggressive),
    shared by the background sweep and every bulk-confirm UI so there's one
    consistent notion of 'confidence' instead of several disconnected sliders."""
    sensitivity = await _resolve_sensitivity(db)
    thresh, margin, floor = _sensitivity_to_thresholds(sensitivity)
    return {"sensitivity": sensitivity, "auto_confirm_thresh": thresh, "knn_margin": margin, "adaptive_floor": floor}


@router.put("/sensitivity")
async def set_sensitivity(db: DB, sensitivity: float = Body(..., embed=True, ge=0.0, le=1.0)) -> dict:
    from fernkam.db.app_settings import set_setting
    await set_setting(db, FACE_SENSITIVITY_KEY, str(sensitivity))
    thresh, margin, floor = _sensitivity_to_thresholds(sensitivity)
    return {"sensitivity": sensitivity, "auto_confirm_thresh": thresh, "knn_margin": margin, "adaptive_floor": floor}


@router.get("/recent-auto")
async def recent_auto_confirmed(
    db: DB,
    limit: int = Query(100, le=500),
    offset: int = Query(0, ge=0),
) -> list[dict]:
    """Faces the automation confirmed without a human looking at them, least-
    confident-first — the audit queue for an aggressive auto-confirm sweep.
    'Send back to review' just PATCHes status back to unconfirmed (crud.py),
    which also clears confirmed_by."""
    q = (
        select(Face)
        .options(selectinload(Face.person_tag))
        .where(Face.status == "confirmed")
        .where(Face.confirmed_by == "auto")
        .order_by(Face.best_match_score.asc().nulls_first())
        .offset(offset)
        .limit(limit)
    )
    faces = (await db.execute(q)).scalars().all()
    return [_make_face_out(f).model_dump() for f in faces]


@router.get("/recent-auto/count")
async def recent_auto_confirmed_count(db: DB) -> dict:
    from sqlalchemy import func
    n = (await db.execute(
        select(func.count()).select_from(Face)
        .where(Face.status == "confirmed")
        .where(Face.confirmed_by == "auto")
    )).scalar_one()
    return {"count": n}


@router.get("/unassigned/count")
async def unassigned_count(db: DB) -> dict:
    """Count of faces needing review (excludes confirmed and ignored)."""
    from sqlalchemy import func
    n = (await db.execute(
        select(func.count()).select_from(Face)
        .where(Face.status.not_in(["confirmed", "ignored"]))
    )).scalar_one()
    return {"count": n}


@router.get("/suggestions/people")
async def suggestions_people_list(db: DB, min_score: float = Query(0.45, ge=0, le=1)) -> list[dict]:
    """People with >=1 plausible candidate face among unconfirmed+suggested faces.

    Uses a single bulk k-NN pass (same LATERAL pattern as the auto-confirm sweep)
    against person_centroids, so a person shows up in the sidebar even if the
    nightly sweep hasn't promoted any of their faces to 'suggested' yet. Falls
    back to literal status='suggested' counts if no centroids have been built.
    """
    from sqlalchemy import func, text as _sql

    centroid_count = (await db.execute(_sql(
        "SELECT count(*) FROM person_centroids WHERE embedding_v IS NOT NULL"
    ))).scalar_one()

    counts: dict = {}
    if centroid_count > 0:
        rows = (await db.execute(_sql("""
            WITH cand AS (
                SELECT id, embedding_v FROM faces
                WHERE status IN ('unconfirmed', 'suggested') AND embedding_v IS NOT NULL
            )
            SELECT nn.person_tag_id AS pid, count(*) AS cnt
            FROM cand c
            CROSS JOIN LATERAL (
                SELECT person_tag_id, 1 - (embedding_v <=> c.embedding_v) AS score
                FROM person_centroids
                WHERE embedding_v IS NOT NULL
                ORDER BY embedding_v <=> c.embedding_v
                LIMIT 1
            ) nn
            WHERE nn.score >= :min_score
            GROUP BY nn.person_tag_id
        """), {"min_score": min_score})).fetchall()
        counts = {int(r[0]): int(r[1]) for r in rows}
    else:
        rows = (
            await db.execute(
                select(Face.person_tag_id, func.count(Face.id))
                .where(Face.status == "suggested")
                .where(Face.person_tag_id.is_not(None))
                .group_by(Face.person_tag_id)
            )
        ).fetchall()
        counts = {int(r[0]): int(r[1]) for r in rows}

    if not counts:
        return []

    tag_rows = (await db.execute(select(Tag.id, Tag.name).where(Tag.id.in_(counts.keys())))).fetchall()
    names = {r.id: r.name for r in tag_rows}
    return sorted(
        [{"person_id": pid, "person_name": names.get(pid, "?"), "count": cnt} for pid, cnt in counts.items()],
        key=lambda x: -x["count"],
    )


@router.get("/candidates")
async def person_candidates(
    db: DB,
    person_tag_id: int = Query(...),
    limit: int = Query(300, le=2000),
    min_score: float = Query(0.4, ge=0, le=1),
) -> list[dict]:
    """Live k-NN candidate search for one person across the full unconfirmed+suggested
    pool — not limited to faces the nightly sweep already promoted to 'suggested'.
    Powers the person-centric Face Review grid (pick a person, see every plausible
    match ranked by score, in one shot).
    """
    from sqlalchemy import text as _sql

    centroid_count = (await db.execute(_sql(
        "SELECT count(*) FROM person_centroids WHERE person_tag_id = :ptid AND embedding_v IS NOT NULL"
    ), {"ptid": person_tag_id})).scalar_one()

    params = {"ptid": person_tag_id, "min_score": min_score, "limit": limit}
    if centroid_count > 0:
        q = _sql("""
            WITH cand AS (
                SELECT id, photo_id, status, embedding_v FROM faces
                WHERE status IN ('unconfirmed', 'suggested') AND embedding_v IS NOT NULL
            )
            SELECT c.id, c.photo_id, c.status,
                   MAX(1 - (c.embedding_v <=> pc.embedding_v)) AS score
            FROM cand c
            CROSS JOIN (
                SELECT embedding_v FROM person_centroids
                WHERE person_tag_id = :ptid AND embedding_v IS NOT NULL
            ) pc
            GROUP BY c.id, c.photo_id, c.status
            HAVING MAX(1 - (c.embedding_v <=> pc.embedding_v)) >= :min_score
            ORDER BY score DESC
            LIMIT :limit
        """)
    else:
        q = _sql("""
            WITH cand AS (
                SELECT id, photo_id, status, embedding_v FROM faces
                WHERE status IN ('unconfirmed', 'suggested') AND embedding_v IS NOT NULL
            ),
            ref AS (
                SELECT embedding_v FROM faces
                WHERE status = 'confirmed' AND person_tag_id = :ptid AND embedding_v IS NOT NULL
                LIMIT 50
            )
            SELECT c.id, c.photo_id, c.status,
                   MAX(1 - (c.embedding_v <=> ref.embedding_v)) AS score
            FROM cand c
            CROSS JOIN ref
            GROUP BY c.id, c.photo_id, c.status
            HAVING MAX(1 - (c.embedding_v <=> ref.embedding_v)) >= :min_score
            ORDER BY score DESC
            LIMIT :limit
        """)
    rows = (await db.execute(q, params)).fetchall()
    if not rows:
        return []

    photo_ids = list({r[1] for r in rows})

    # Birth-date filtering — drop suggestions on photos taken before a configured person's birth date.
    person_min_dates = await _resolve_person_min_dates(db)
    photo_dates: dict = {}
    if person_min_dates and person_tag_id in person_min_dates:
        pd_rows = (await db.execute(
            select(Photo.id, Photo.taken_at).where(Photo.id.in_(photo_ids))
        )).fetchall()
        photo_dates = {r[0]: (r[1].date() if r[1] else None) for r in pd_rows}

    # Conflict: this person already confirmed elsewhere in the same photo (skip for TWINS).
    tag_name = (await db.execute(select(Tag.name).where(Tag.id == person_tag_id))).scalar_one_or_none()
    is_twins = tag_name is not None and "- TWINS" in tag_name
    confirmed_photos: set = set()
    if photo_ids and not is_twins:
        conf_rows = (await db.execute(_sql(
            "SELECT photo_id FROM faces WHERE photo_id = ANY(CAST(:pids AS int[])) "
            "AND status = 'confirmed' AND person_tag_id = :ptid"
        ), {"pids": photo_ids, "ptid": person_tag_id})).fetchall()
        confirmed_photos = {r[0] for r in conf_rows}

    results: list[dict] = []
    for fid, photo_id, status, score in rows:
        if person_min_dates and person_tag_id in person_min_dates:
            pdate = photo_dates.get(photo_id)
            if pdate is not None and pdate < person_min_dates[person_tag_id]:
                continue
        results.append({
            "face_id": str(fid),
            "photo_id": photo_id,
            "status": status,
            "score": round(float(score), 2),
            "conflict": photo_id in confirmed_photos,
        })
    return results


# /suggestions and /unassigned (top-3-ranked-suggestions-per-face endpoints) were
# removed here — confirmed dead by cross-referencing every frontend call site.
# The live UX uses /candidates (person-first) and /clusters (cluster-first) instead.


@router.post("/build-centroids", response_model=dict)
async def build_person_centroids(
    db: DB,
    n_clusters: int = Query(3, ge=1, le=10, description="Max sub-centroids per person (k-means). Falls back to 1 for small pools."),
) -> dict:
    """Recompute per-person centroid embeddings using k-means sub-clustering.
    """
    return await _rebuild_person_centroids(db, n_clusters=n_clusters)


@router.post("/auto-confirm-all", response_model=dict)
async def auto_confirm_all_faces(
    since: Optional[str] = Query(
        None,
        description="ISO-8601 datetime — only process faces created after this timestamp (incremental mode)",
    ),
) -> dict:
    """Kick off auto-confirm sweep as a background task. Returns immediately with a task_id.

    Pass `since=<ISO datetime>` to run an incremental sweep covering only faces
    added after that point (e.g. after a fresh scan batch).
    """
    import asyncio
    from datetime import datetime
    from fernkam.task_manager import task_manager
    from fernkam.db.session import async_session_factory as _session_factory

    since_dt = None
    if since:
        try:
            since_dt = datetime.fromisoformat(since)
        except ValueError:
            pass

    mode = f"since {since_dt.isoformat()}" if since_dt else "full"
    task_id = await task_manager.create_task("auto_confirm", f"Auto-confirm sweep ({mode}) running…")

    async def _run() -> None:
        async with _session_factory() as bg_db:
            try:
                pass_num = 0
                totals = {"confirmed": 0, "ignored": 0, "suggested": 0, "scored": 0}
                rebuild_person_ids: set = set()
                while True:
                    pass_num += 1
                    if rebuild_person_ids:
                        await _rebuild_person_centroids(bg_db, person_ids=rebuild_person_ids)
                    r = await _auto_confirm_sweep(bg_db, since_dt=since_dt)
                    rebuild_person_ids = set(r.get("confirmed_person_ids") or [])
                    for k in totals:
                        totals[k] += r.get(k, 0)
                    await task_manager.update_task(
                        task_id,
                        message=(
                            f"Pass {pass_num}: +{r['confirmed']} confirmed, "
                            f"+{r['ignored']} ignored, +{r['suggested']} queued"
                        ),
                        progress={
                            "pass": pass_num,
                            "confirmed": totals["confirmed"],
                            "ignored": totals["ignored"],
                            "suggested": totals["suggested"],
                            "scored": totals["scored"],
                            "last_confirmed": r["confirmed"],
                        },
                    )
                    if r["confirmed"] == 0:
                        break
                await task_manager.update_task(
                    task_id,
                    status="completed",
                    message=(
                        f"Done ({pass_num} pass{'es' if pass_num > 1 else ''}): "
                        f"{totals['confirmed']} confirmed, {totals['ignored']} ignored, "
                        f"{totals['suggested']} queued for review"
                    ),
                )
            except Exception as exc:  # noqa: BLE001
                await task_manager.update_task(task_id, status="failed", message=str(exc))

    asyncio.create_task(_run())
    return {"task_id": task_id, "status": "started"}


@router.post("/drop-pre-birth-suggestions")
async def drop_pre_birth_suggestions(db: DB) -> dict:
    """Reset suggested faces that violate person birth-date constraints.

    For each person in FERNKAM_PERSON_MIN_DATES, any face with status='suggested'
    pointing to that person on a photo whose taken_at is BEFORE the birth date is
    reset to status='unconfirmed' with person_tag_id cleared.
    """
    from sqlalchemy import text as _sql

    person_min_dates = await _resolve_person_min_dates(db)
    if not person_min_dates:
        return {"cleared": 0, "reason": "No person_min_dates configured"}

    total_cleared = 0
    for ptid, min_date in person_min_dates.items():
        result = await db.execute(_sql(
            "UPDATE faces SET status = 'unconfirmed', person_tag_id = NULL, best_match_score = NULL "
            "FROM photos "
            "WHERE faces.photo_id = photos.id "
            "  AND faces.status = 'suggested' "
            "  AND faces.person_tag_id = :ptid "
            "  AND photos.taken_at IS NOT NULL "
            "  AND photos.taken_at::date < :min_date"
        ), {"ptid": ptid, "min_date": min_date})
        total_cleared += result.rowcount

    await db.commit()
    return {"cleared": total_cleared}


@router.post("/archive-low-quality")
async def archive_low_quality_faces(db: DB) -> dict:
    """Mark unconfirmed/suggested faces that fail quality gates as 'ignored'.

    Quality gates (from config):
    - det_score < min_det_score
    - bbox width or height < min_face_px

    These faces are silently moved to 'ignored' — they never appear in the review
    queue and are excluded from future sweep passes.
    """
    from fernkam.config import get_settings

    s = get_settings()
    clauses = []
    if s.min_det_score and s.min_det_score > 0:
        clauses.append(
            (Face.det_score.is_not(None)) & (Face.det_score < s.min_det_score)
        )
    if s.min_face_px and s.min_face_px > 0:
        from sqlalchemy import or_
        clauses.append(or_(Face.w < s.min_face_px, Face.h < s.min_face_px))
    if s.min_blur_score and s.min_blur_score > 0:
        clauses.append(
            (Face.blur_score.is_not(None)) & (Face.blur_score < s.min_blur_score)
        )

    if not clauses:
        return {"archived": 0, "reason": "No quality gates configured"}

    from sqlalchemy import or_ as _or
    combined = clauses[0]
    for c in clauses[1:]:
        combined = _or(combined, c)

    result = await db.execute(
        update(Face)
        .where(Face.status.in_(["unconfirmed", "suggested"]))
        .where(combined)
        .values(status="ignored", person_tag_id=None)
    )
    await db.commit()
    return {
        "archived": result.rowcount,
        "min_det_score": s.min_det_score,
        "min_face_px": s.min_face_px,
    }


@router.post("/auto-confirm-incremental")
async def auto_confirm_incremental(db: DB) -> dict:
    """Kick off an incremental auto-confirm sweep using the last completed sweep's start time.

    Looks up the most recent completed 'auto_confirm' task to find the cutoff,
    then only processes faces created after that timestamp. Falls back to a full
    sweep if no prior run exists.
    """
    import asyncio
    from datetime import datetime
    from sqlalchemy import text as _sql
    from fernkam.task_manager import task_manager
    from fernkam.db.session import async_session_factory as _session_factory

    last_row = (await db.execute(_sql(
        "SELECT completed_at FROM tasks WHERE task_type = 'auto_confirm' AND status = 'completed' "
        "ORDER BY completed_at DESC LIMIT 1"
    ))).fetchone()
    since_dt: Optional[datetime] = last_row[0] if last_row else None

    mode = f"since {since_dt.isoformat()}" if since_dt else "full (no prior run)"
    task_id = await task_manager.create_task("auto_confirm", f"Incremental sweep ({mode})…")

    async def _run() -> None:
        async with _session_factory() as bg_db:
            try:
                pass_num = 0
                totals = {"confirmed": 0, "ignored": 0, "suggested": 0, "scored": 0}
                while True:
                    pass_num += 1
                    r = await _auto_confirm_sweep(bg_db, since_dt=since_dt)
                    for k in totals:
                        totals[k] += r.get(k, 0)
                    await task_manager.update_task(
                        task_id,
                        message=f"Pass {pass_num}: +{r['confirmed']} confirmed, +{r['ignored']} ignored",
                        progress={"pass": pass_num, **totals},
                    )
                    if r["confirmed"] == 0:
                        break
                await task_manager.update_task(
                    task_id, status="completed",
                    message=(
                        f"Done ({pass_num} pass{'es' if pass_num > 1 else ''}): "
                        f"{totals['confirmed']} confirmed, {totals['suggested']} queued"
                    ),
                )
            except Exception as exc:
                await task_manager.update_task(task_id, status="failed", message=str(exc))

    asyncio.create_task(_run())
    return {"task_id": task_id, "status": "started", "since": since_dt.isoformat() if since_dt else None}


