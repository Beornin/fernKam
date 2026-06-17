from __future__ import annotations

from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Body, HTTPException, Query
from sqlalchemy import case, select, update
from sqlalchemy.orm import selectinload

from fernkam.api.deps import DB
from fernkam.api.schemas import FaceOut, FaceUpdate
from fernkam.db.models.photos import Face, Photo, PhotoTag, Tag

router = APIRouter()


async def _already_confirmed_in_photo(
    db, photo_id: int, person_tag_id: int, exclude_face_id=None
) -> bool:
    """Return True if *another* confirmed face for person_tag_id exists in photo_id.
    Always returns False for TWINS persons (multiple tagging allowed)."""
    tag = (await db.execute(select(Tag.name).where(Tag.id == person_tag_id))).scalar_one_or_none()
    if tag and "- TWINS" in tag:
        return False
    q = select(Face.id).where(
        Face.photo_id == photo_id,
        Face.person_tag_id == person_tag_id,
        Face.status == "confirmed",
    )
    if exclude_face_id is not None:
        q = q.where(Face.id != exclude_face_id)
    return (await db.execute(q)).first() is not None


@router.get("/unassigned/count")
async def unassigned_count(db: DB) -> dict:
    """Count of faces needing review (excludes confirmed and ignored)."""
    from sqlalchemy import func
    n = (await db.execute(
        select(func.count()).select_from(Face)
        .where(Face.status.not_in(["confirmed", "ignored"]))
    )).scalar_one()
    return {"count": n}


@router.get("/suggestions")
async def face_suggestions(
    db: DB,
    limit: int = Query(200, le=2000),
    offset: int = Query(0, ge=0),
    sort: str = Query("score_desc"),
    status_filter: str = Query("all"),
) -> list[dict]:
    """Unassigned faces with top-3 person suggestions from embedding similarity (pgvector)."""
    from sqlalchemy import text as _sql

    if status_filter == "suggested":
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

    unassigned = (await db.execute(
        select(Face)
        .options(selectinload(Face.person_tag))
        .where(status_clause)
        .where(Face.embedding_v.is_not(None))
        .order_by(*order_clauses)
        .offset(offset).limit(limit)
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


def _thresholds():
    from fernkam.config import get_settings
    s = get_settings()
    return s.auto_confirm_thresh, s.suggest_thresh, s.knn_k, s.knn_min_votes, s.knn_margin


async def _auto_confirm_sweep(db) -> int:
    """k-NN voting + margin test sweep against confirmed/ignored pools.

    For each unconfirmed/suggested face:
    1. Find the nearest K confirmed faces via HNSW LATERAL.
    2. Aggregate per-person: vote count + best cosine score.
    3. Auto-confirm if: best_score >= auto_thresh AND votes >= min_votes AND
       (best_score - 2nd_place_score) >= margin.
    4. Promote to 'suggested' if best_score >= suggest_thresh (review queue).
    Returns count of auto-confirmed faces in this pass.
    """
    import logging
    from collections import defaultdict
    from sqlalchemy import text as _sql

    logger = logging.getLogger(__name__)
    auto_thresh, suggest_thresh, knn_k, min_votes, margin = _thresholds()

    # ── 1. Ignored pool: nearest ignored face >= auto_thresh → auto-ignore ──
    ignored_q = _sql("""
        WITH unc AS (
            SELECT id, embedding_v
            FROM faces
            WHERE status IN ('unconfirmed', 'suggested')
              AND embedding_v IS NOT NULL
        )
        SELECT u.id,
               1 - (u.embedding_v <=> i.embedding_v) AS score
        FROM unc u
        CROSS JOIN LATERAL (
            SELECT embedding_v
            FROM faces
            WHERE status = 'ignored' AND embedding_v IS NOT NULL
            ORDER BY embedding_v <=> u.embedding_v
            LIMIT 1
        ) i
        WHERE 1 - (u.embedding_v <=> i.embedding_v) >= :thresh
    """)
    auto_ignore: list = []
    try:
        rows = (await db.execute(ignored_q, {"thresh": auto_thresh - 0.005})).fetchall()
        auto_ignore = [r[0] for r in rows]
    except Exception as e:
        logger.warning("[AUTO-CONFIRM] ignored pool query skipped: %s", e)

    # ── 2. Confirmed pool: top-K neighbours per face ──
    confirmed_q = _sql("""
        WITH unc AS (
            SELECT id, person_tag_id, embedding_v
            FROM faces
            WHERE status IN ('unconfirmed', 'suggested')
              AND embedding_v IS NOT NULL
              AND ( :exclude_count = 0 OR id <> ALL(CAST(:exclude_ids AS uuid[])) )
        )
        SELECT u.id          AS face_id,
               u.person_tag_id AS unc_ptid,
               c.person_tag_id AS nbr_ptid,
               1 - (u.embedding_v <=> c.embedding_v) AS score
        FROM unc u
        CROSS JOIN LATERAL (
            SELECT person_tag_id, embedding_v
            FROM faces
            WHERE status = 'confirmed'
              AND person_tag_id IS NOT NULL
              AND embedding_v IS NOT NULL
            ORDER BY embedding_v <=> u.embedding_v
            LIMIT :k
        ) c
    """)
    try:
        params = {
            "exclude_count": len(auto_ignore),
            "exclude_ids": [str(x) for x in auto_ignore],
            "k": knn_k,
        }
        rows = (await db.execute(confirmed_q, params)).fetchall()
    except Exception as e:
        logger.warning("[AUTO-CONFIRM] confirmed pool query skipped: %s", e)
        rows = []

    # ── 3. Aggregate k-NN votes per person per face ──
    # face_votes[face_id][person_tag_id] = [vote_count, best_score]
    face_votes: dict = defaultdict(lambda: defaultdict(lambda: [0, 0.0]))
    face_unc_ptid: dict = {}

    for face_id, unc_ptid, nbr_ptid, score in rows:
        if nbr_ptid is None:
            continue
        s = float(score)
        entry = face_votes[face_id][nbr_ptid]
        entry[0] += 1
        if s > entry[1]:
            entry[1] = s
        face_unc_ptid[face_id] = unc_ptid

    # ── 4. Apply decision rules ──
    pending: dict = {}          # face_id -> person_tag_id  (auto-confirm)
    suggest_updates: list = []  # [{id, ptid}]              (promote to suggested)
    score_updates: list = []    # [{id, s}]                 (bulk score update)

    for face_id, person_scores in face_votes.items():
        unc_ptid = face_unc_ptid.get(face_id)
        candidates = sorted(
            [(ptid, data[0], data[1]) for ptid, data in person_scores.items()],
            key=lambda x: -x[2],
        )
        if not candidates:
            continue

        p1_ptid, p1_votes, p1_score = candidates[0]
        p2_score = candidates[1][2] if len(candidates) > 1 else 0.0

        s = round(p1_score, 4)
        score_updates.append({"id": face_id, "s": s})

        target = unc_ptid if (unc_ptid is not None and unc_ptid == p1_ptid) else p1_ptid

        margin_ok = (p1_score - p2_score) >= margin
        votes_ok = p1_votes >= min_votes
        score_ok = round(p1_score, 2) >= auto_thresh

        if score_ok and votes_ok and margin_ok and target is not None:
            pending[face_id] = target
        elif p1_score >= suggest_thresh and target is not None:
            suggest_updates.append({"id": face_id, "ptid": target})

    logger.info(
        "[AUTO-CONFIRM] %d auto-ignore, %d auto-confirm, %d suggest, %d scored",
        len(auto_ignore), len(pending), len(suggest_updates), len(score_updates),
    )

    # ── 5. Persist: ignored ──
    if auto_ignore:
        await db.execute(
            update(Face).where(Face.id.in_(auto_ignore))
            .values(status="ignored", person_tag_id=None)
        )

    # ── 6. Persist: bulk score update ──
    if score_updates:
        await db.execute(
            _sql(
                "UPDATE faces SET best_match_score = v.s "
                "FROM (VALUES " + ",".join(
                    f"('{u['id']}'::uuid, {u['s']})" for u in score_updates
                ) + ") AS v(id, s) WHERE faces.id = v.id"
            )
        )

    # ── 7. Persist: auto-confirm (with duplicate guard, TWINS-aware) ──
    clean: dict = {}
    if pending:
        pending_ids = list(pending.keys())
        ph_rows = (await db.execute(
            select(Face.id, Face.photo_id).where(Face.id.in_(pending_ids))
        )).fetchall()
        photo_map = {fid: phid for fid, phid in ph_rows}

        # Identify TWINS persons — they are allowed multiple faces per photo.
        all_ptids = set(pending.values())
        twins_ptids: set = set()
        if all_ptids:
            twins_rows = (await db.execute(
                select(Tag.id).where(Tag.id.in_(all_ptids)).where(Tag.name.like("%- TWINS%"))
            )).fetchall()
            twins_ptids = {r[0] for r in twins_rows}

        affected_photos = list(set(photo_map.values()))
        existing_pairs: set = set()
        if affected_photos:
            ex_rows = (await db.execute(
                select(Face.photo_id, Face.person_tag_id)
                .where(Face.photo_id.in_(affected_photos))
                .where(Face.status == "confirmed")
                .where(Face.person_tag_id.is_not(None))
                .where(Face.person_tag_id.not_in(twins_ptids) if twins_ptids else True)
            )).fetchall()
            existing_pairs = {(r[0], r[1]) for r in ex_rows}

        reset_ids: list = []
        for fid, ptid in pending.items():
            pair = (photo_map.get(fid), ptid)
            if ptid not in twins_ptids and pair[0] is not None and pair in existing_pairs:
                reset_ids.append(fid)
            else:
                clean[fid] = ptid
                if pair[0] is not None and ptid not in twins_ptids:
                    existing_pairs.add(pair)

        if reset_ids:
            await db.execute(
                update(Face).where(Face.id.in_(reset_ids))
                .values(status="unconfirmed", person_tag_id=None)
            )

        for ptid in set(clean.values()):
            ids = [fid for fid, p in clean.items() if p == ptid]
            await db.execute(
                update(Face).where(Face.id.in_(ids))
                .values(person_tag_id=ptid, status="confirmed")
            )

    # ── 8. Persist: promote uncertain faces to suggested (review queue) ──
    to_suggest: list = []
    if suggest_updates:
        pending_ids_set = set(pending.keys())
        to_suggest = [u for u in suggest_updates if u["id"] not in pending_ids_set]
        for ptid in {u["ptid"] for u in to_suggest}:
            ids = [u["id"] for u in to_suggest if u["ptid"] == ptid]
            await db.execute(
                update(Face).where(Face.id.in_(ids))
                .values(person_tag_id=ptid, status="suggested")
            )

    # ── 9. Build per-pass stats dict ──
    await db.commit()
    return {
        "confirmed": len(clean),
        "ignored": len(auto_ignore),
        "suggested": len(to_suggest),
        "scored": len(score_updates),
    }


async def _auto_confirm_similar(db, person_tag_id: int, seed_face_ids: list) -> None:
    """Auto-confirm any unconfirmed/suggested faces whose nearest seed scores
    >= AUTO_CONFIRM_THRESH. Single indexed pgvector LATERAL query."""
    from sqlalchemy import text as _sql

    seed_str = [str(x) for x in seed_face_ids]
    if not seed_str:
        return

    q = _sql(
        """
        WITH unc AS (
            SELECT id, embedding_v
            FROM faces
            WHERE status IN ('unconfirmed', 'suggested')
              AND embedding_v IS NOT NULL
        )
        SELECT u.id
        FROM unc u
        CROSS JOIN LATERAL (
            SELECT embedding_v
            FROM faces
            WHERE id = ANY(CAST(:seeds AS uuid[]))
              AND embedding_v IS NOT NULL
            ORDER BY embedding_v <=> u.embedding_v
            LIMIT 1
        ) s
        WHERE 1 - (u.embedding_v <=> s.embedding_v) >= :thresh
        """
    )
    auto_thresh = _thresholds()[0]
    rows = (await db.execute(q, {"seeds": seed_str, "thresh": auto_thresh - 0.005})).fetchall()
    auto_ids = [r[0] for r in rows]
    if not auto_ids:
        return

    # Duplicate guard (TWINS-aware): skip faces where person already confirmed in that photo.
    tag_name = (await db.execute(select(Tag.name).where(Tag.id == person_tag_id))).scalar_one_or_none()
    is_twins = tag_name is not None and "- TWINS" in tag_name

    if not is_twins:
        ph_rows = (await db.execute(
            select(Face.id, Face.photo_id).where(Face.id.in_(auto_ids))
        )).fetchall()
        photo_map = {fid: phid for fid, phid in ph_rows}
        affected_photos = list(set(photo_map.values()))
        taken: set = set()
        if affected_photos:
            ex_rows = (await db.execute(
                select(Face.photo_id)
                .where(Face.photo_id.in_(affected_photos))
                .where(Face.status == "confirmed")
                .where(Face.person_tag_id == person_tag_id)
                .where(Face.id.not_in(auto_ids))
            )).fetchall()
            taken = {r[0] for r in ex_rows}
        seen: set = set()
        safe: list = []
        for fid in auto_ids:
            phid = photo_map.get(fid)
            if phid not in taken and phid not in seen:
                safe.append(fid)
                if phid is not None:
                    seen.add(phid)
        auto_ids = safe

    if auto_ids:
        await db.execute(
            update(Face).where(Face.id.in_(auto_ids))
            .values(person_tag_id=person_tag_id, status="confirmed")
        )
        await db.commit()


@router.post("/build-centroids", response_model=dict)
async def build_person_centroids(db: DB) -> dict:
    """Recompute per-person centroid embeddings from all confirmed faces.

    Computes element-wise mean via SQL avg(), normalises in Python, then
    upserts into person_centroids.  Typical runtime: < 5 s for 20 k faces.
    """
    import numpy as np
    from sqlalchemy import text as _sql
    from fernkam.face_processor import _pgvector_literal

    rows = (await db.execute(_sql("""
        SELECT person_tag_id,
               avg(embedding_v)::text  AS centroid_text,
               count(*)::int           AS face_cnt
        FROM   faces
        WHERE  status = 'confirmed'
          AND  person_tag_id IS NOT NULL
          AND  embedding_v   IS NOT NULL
        GROUP  BY person_tag_id
    """))).fetchall()

    if not rows:
        return {"updated": 0}

    for ptid, centroid_text, face_cnt in rows:
        arr = np.array(
            [float(x) for x in centroid_text.strip("[]").split(",")],
            dtype=np.float32,
        )
        norm = float(np.linalg.norm(arr))
        if norm > 0:
            arr /= norm
        lit = _pgvector_literal(arr)
        await db.execute(_sql(f"""
            INSERT INTO person_centroids (person_tag_id, label, embedding_v, face_count, built_at)
            VALUES ({int(ptid)}, 0, '{lit}'::vector(512), {int(face_cnt)}, now())
            ON CONFLICT (person_tag_id, label)
            DO UPDATE SET embedding_v = EXCLUDED.embedding_v,
                          face_count  = EXCLUDED.face_count,
                          built_at    = EXCLUDED.built_at
        """))

    await db.commit()
    return {"updated": len(rows)}


@router.post("/auto-confirm-all", response_model=dict)
async def auto_confirm_all_faces() -> dict:
    """Kick off auto-confirm sweep as a background task. Returns immediately with a task_id."""
    import asyncio
    from fernkam.task_manager import task_manager
    from fernkam.db.session import async_session_factory as _session_factory

    task_id = await task_manager.create_task("auto_confirm", "Auto-confirm sweep running…")

    async def _run() -> None:
        async with _session_factory() as bg_db:
            try:
                pass_num = 0
                totals = {"confirmed": 0, "ignored": 0, "suggested": 0, "scored": 0}
                while True:
                    pass_num += 1
                    r = await _auto_confirm_sweep(bg_db)
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
) -> dict:
    """Total count of unassigned faces with embeddings (matches /suggestions filter)."""
    from sqlalchemy import func
    if status_filter == "suggested":
        status_clause = Face.status == "suggested"
    elif status_filter == "unconfirmed":
        status_clause = Face.status == "unconfirmed"
    else:
        status_clause = Face.status.in_(["unconfirmed", "suggested"])
    n = (await db.execute(
        select(func.count()).select_from(Face)
        .where(status_clause)
        .where(Face.embedding_v.is_not(None))
    )).scalar_one()
    return {"count": n}


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
        score=float(f.best_match_score) if f.best_match_score is not None else None,
    )


