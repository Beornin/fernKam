"""Face suggestion, auto-confirm, and review-queue endpoints."""
from __future__ import annotations

from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Query
from sqlalchemy import case, select, update
from sqlalchemy.orm import selectinload

from fernkam.api.deps import DB
from fernkam.api.schemas import FaceOut
from fernkam.db.models.photos import Face, Photo, Tag

from ._helpers import _auto_confirm_sweep, _make_face_out

router = APIRouter()


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
async def suggestions_people_list(db: DB) -> list[dict]:
    """People who have at least one face currently in 'suggested' status."""
    from sqlalchemy import func

    rows = (
        await db.execute(
            select(Tag.id, Tag.name, func.count(Face.id).label("cnt"))
            .join(Face, Face.person_tag_id == Tag.id)
            .where(Face.status == "suggested")
            .group_by(Tag.id, Tag.name)
            .order_by(Tag.name)
        )
    ).fetchall()
    return [{"person_id": r.id, "person_name": r.name, "count": r.cnt} for r in rows]


@router.get("/suggestions")
async def face_suggestions(
    db: DB,
    limit: int = Query(200, le=2000),
    offset: int = Query(0, ge=0),
    sort: str = Query("score_desc"),
    status_filter: str = Query("all"),
    person_tag_id: Optional[int] = Query(None),
) -> list[dict]:
    """Unassigned faces with top-3 person suggestions from embedding similarity (pgvector)."""
    from sqlalchemy import text as _sql

    # When filtering by a specific person, only 'suggested' faces make sense
    if person_tag_id is not None:
        status_clause = Face.status == "suggested"
    elif status_filter == "suggested":
        status_clause = Face.status == "suggested"
    elif status_filter == "unconfirmed":
        status_clause = Face.status == "unconfirmed"
    else:
        status_clause = Face.status.in_(["unconfirmed", "suggested"])

    if sort == "score_asc":
        order_clauses = [Face.best_match_score.asc().nullsfirst(), Face.created_at.desc()]
    elif sort == "newest":
        order_clauses = [Face.created_at.desc()]
    elif sort == "status":
        order_clauses = [
            case((Face.status == "suggested", 0), else_=1),
            Face.best_match_score.desc().nullslast(),
        ]
    else:
        order_clauses = [Face.best_match_score.desc().nullslast(), Face.created_at.desc()]

    q = (
        select(Face)
        .options(selectinload(Face.person_tag))
        .where(status_clause)
        .where(Face.embedding_v.is_not(None))
    )
    if person_tag_id is not None:
        q = q.where(Face.person_tag_id == person_tag_id)
    unassigned = (await db.execute(
        q.order_by(*order_clauses).offset(offset).limit(limit)
    )).scalars().all()

    if not unassigned:
        return []

    # Single SQL: for each unassigned face, find top-10 confirmed neighbors via HNSW.
    unc_ids = [str(f.id) for f in unassigned]
    top_q = _sql(
        """
        WITH unc AS (
            SELECT id, embedding_v
            FROM faces
            WHERE id = ANY(CAST(:ids AS uuid[]))
        )
        SELECT u.id AS unc_id,
               c.person_tag_id AS person_id,
               1 - (u.embedding_v <=> c.embedding_v) AS score
        FROM unc u
        CROSS JOIN LATERAL (
            SELECT person_tag_id, embedding_v
            FROM faces
            WHERE status = 'confirmed'
              AND person_tag_id IS NOT NULL
              AND embedding_v IS NOT NULL
            ORDER BY embedding_v <=> u.embedding_v
            LIMIT 10
        ) c
        """
    )
    rows = (await db.execute(top_q, {"ids": unc_ids})).fetchall()

    # Group by unc_id; keep top match per person_id, then keep top-3 persons.
    per_face: dict = {}  # unc_id -> {person_id -> best_score}
    for unc_id, person_id, score in rows:
        if person_id is None:
            continue
        d = per_face.setdefault(unc_id, {})
        s = float(score)
        if person_id not in d or s > d[person_id]:
            d[person_id] = s

    # Resolve person names in one query.
    all_pids = {pid for d in per_face.values() for pid in d.keys()}
    tag_names: dict = {}
    if all_pids:
        tag_rows = (await db.execute(select(Tag).where(Tag.id.in_(all_pids)))).scalars().all()
        tag_names = {t.id: t.name for t in tag_rows}

    # Pre-fetch confirmed person_tag_ids per photo so we can flag conflicts.
    photo_ids_uniq = list({f.photo_id for f in unassigned})
    confirmed_by_photo: dict = {}  # photo_id -> set of person_tag_ids
    if photo_ids_uniq:
        from sqlalchemy import text as _sql
        conf_rows = (await db.execute(
            _sql(
                "SELECT photo_id, person_tag_id FROM faces "
                "WHERE photo_id = ANY(CAST(:pids AS int[])) AND status = 'confirmed' "
                "AND person_tag_id IS NOT NULL"
            ),
            {"pids": photo_ids_uniq},
        )).fetchall()
        for ph_id, pt_id in conf_rows:
            confirmed_by_photo.setdefault(ph_id, set()).add(pt_id)

    results: list[dict] = []
    for face in unassigned:
        per = per_face.get(face.id, {})
        ranked = sorted(per.items(), key=lambda kv: kv[1], reverse=True)[:3]
        confirmed_here = confirmed_by_photo.get(face.photo_id, set())
        suggestions = [
            {
                "person_id": pid,
                "person_name": tag_names.get(pid),
                "score": round(s, 2),
                "conflict": pid in confirmed_here and "- TWINS" not in (tag_names.get(pid) or ""),
            }
            for pid, s in ranked
        ]
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
    """Top-K most similar faces by cosine similarity of InsightFace embeddings (pgvector HNSW)."""
    from fastapi import HTTPException
    from fernkam.face_processor import bytes_to_embedding, find_similar_pg

    face = (await db.execute(select(Face).where(Face.id == face_id))).scalar_one_or_none()
    if not face:
        raise HTTPException(404, "Face not found")
    if not face.embedding:
        raise HTTPException(422, "Face has no embedding — run detect-faces first")

    try:
        query_emb = bytes_to_embedding(face.embedding)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc

    matches = await find_similar_pg(
        db, query_emb,
        confirmed_only=confirmed_only,
        exclude_face_id=face_id,
        k=k,
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
            "score": round(m["score"], 2),
        }
        for m in matches
    ]


def _kmeans_np(X, k: int, n_iter: int = 25):
    """Minimal Lloyd's k-means++ on a numpy float32 matrix.

    Returns (centroids_array shape [k, d], assignments array shape [n]).
    Falls back to single centroid when k >= len(X).
    """
    import numpy as np

    n = len(X)
    if k >= n:
        return X.mean(axis=0, keepdims=True), np.zeros(n, dtype=int)

    # k-means++ initialisation
    rng = np.random.default_rng(42)
    idxs = [int(rng.integers(n))]
    for _ in range(k - 1):
        dists = np.min(np.sum((X[:, None, :] - X[idxs, :][None, :, :]) ** 2, axis=2), axis=1)
        probs = dists / dists.sum()
        idxs.append(int(rng.choice(n, p=probs)))
    centers = X[idxs].copy()

    assignments = np.zeros(n, dtype=int)
    for _ in range(n_iter):
        # assign
        dists = np.sum((X[:, None, :] - centers[None, :, :]) ** 2, axis=2)
        new_asgn = np.argmin(dists, axis=1)
        if np.array_equal(new_asgn, assignments):
            break
        assignments = new_asgn
        # update
        for j in range(k):
            mask = assignments == j
            if mask.any():
                centers[j] = X[mask].mean(axis=0)
    return centers, assignments


@router.post("/build-centroids", response_model=dict)
async def build_person_centroids(
    db: DB,
    n_clusters: int = Query(3, ge=1, le=10, description="Max sub-centroids per person (k-means). Falls back to 1 for small pools."),
) -> dict:
    """Recompute per-person centroid embeddings using k-means sub-clustering.

    For persons with >= 2*n_clusters confirmed faces, computes n_clusters
    sub-centroids capturing appearance variation (age, glasses, pose).
    Smaller pools fall back to a single mean centroid (label=0).
    Deletes stale labels on shrink (e.g. after faces are removed).
    """
    import numpy as np
    from collections import defaultdict as _dd
    from sqlalchemy import text as _sql
    from fernkam.face_processor import _pgvector_literal

    rows = (await db.execute(_sql("""
        SELECT person_tag_id, embedding_v::text AS emb_text
        FROM   faces
        WHERE  status = 'confirmed'
          AND  person_tag_id IS NOT NULL
          AND  embedding_v   IS NOT NULL
    """))).fetchall()

    if not rows:
        return {"updated": 0, "n_clusters": n_clusters}

    person_embs: dict = _dd(list)
    for ptid, emb_text in rows:
        arr = np.array([float(x) for x in emb_text.strip("[]").split(",")], dtype=np.float32)
        person_embs[int(ptid)].append(arr)

    total_updated = 0
    for ptid, embs in person_embs.items():
        mat = np.array(embs, dtype=np.float32)
        k = max(1, min(n_clusters, len(embs) // 2))

        centers, assignments = _kmeans_np(mat, k)

        for label in range(len(centers)):
            c = centers[label]
            norm = float(np.linalg.norm(c))
            if norm > 0:
                c = c / norm
            lit = _pgvector_literal(c)
            face_cnt = int((assignments == label).sum())
            await db.execute(_sql(f"""
                INSERT INTO person_centroids (person_tag_id, label, embedding_v, face_count, built_at)
                VALUES ({ptid}, {label}, '{lit}'::vector(512), {face_cnt}, now())
                ON CONFLICT (person_tag_id, label)
                DO UPDATE SET embedding_v = EXCLUDED.embedding_v,
                              face_count  = EXCLUDED.face_count,
                              built_at    = EXCLUDED.built_at
            """))

        await db.execute(_sql(f"""
            DELETE FROM person_centroids
            WHERE person_tag_id = {ptid} AND label >= {len(centers)}
        """))

        total_updated += 1

    await db.commit()
    return {"updated": total_updated, "n_clusters": n_clusters}


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
                while True:
                    pass_num += 1
                    r = await _auto_confirm_sweep(bg_db, since_dt=since_dt)
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


@router.get("/suggestions/count")
async def suggestions_count(
    db: DB,
    status_filter: str = Query("all"),
    person_tag_id: Optional[int] = Query(None),
) -> dict:
    """Total count of unassigned faces with embeddings (matches /suggestions filter)."""
    from sqlalchemy import func
    if person_tag_id is not None:
        status_clause = Face.status == "suggested"
    elif status_filter == "suggested":
        status_clause = Face.status == "suggested"
    elif status_filter == "unconfirmed":
        status_clause = Face.status == "unconfirmed"
    else:
        status_clause = Face.status.in_(["unconfirmed", "suggested"])
    q = (
        select(func.count()).select_from(Face)
        .where(status_clause)
        .where(Face.embedding_v.is_not(None))
    )
    if person_tag_id is not None:
        q = q.where(Face.person_tag_id == person_tag_id)
    n = (await db.execute(q)).scalar_one()
    return {"count": n}


@router.post("/purge-weak-suggestions")
async def purge_weak_suggestions(db: DB) -> dict:
    """Reset suggested faces below suggest_thresh back to unconfirmed.

    Cleans the review queue of low-confidence suggestions produced before
    the threshold was raised.
    """
    from fernkam.config import get_settings

    thresh = get_settings().suggest_thresh
    result = await db.execute(
        update(Face)
        .where(Face.status == "suggested")
        .where(Face.best_match_score < thresh)
        .values(status="unconfirmed", person_tag_id=None)
    )
    await db.commit()
    return {"cleared": result.rowcount, "threshold": thresh}


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
        "SELECT created_at FROM tasks WHERE task_type = 'auto_confirm' AND status = 'completed' "
        "ORDER BY created_at DESC LIMIT 1"
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


@router.post("/demote-low-confidence-confirmed")
async def demote_low_confidence_confirmed(db: DB) -> dict:
    """Demote confirmed faces that look suspicious back to 'suggested' for review.

    Targets faces where:
    - det_score IS NULL  (processed via buggy overlap path — quality gate was never applied)
    - best_match_score < 0.65  (well below auto-confirm threshold)

    These are moved to 'suggested' (keeping person_tag_id) so they appear in the
    face review queue for manual confirmation or rejection.
    """
    result = await db.execute(
        update(Face)
        .where(Face.status == "confirmed")
        .where(Face.det_score.is_(None))
        .where(Face.best_match_score < 0.65)
        .values(status="suggested")
    )
    await db.commit()
    return {"demoted": result.rowcount}
