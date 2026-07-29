"""Shared internal helpers for the faces router package.

None of these are route handlers — they are pure business-logic helpers used
by the sub-modules (suggestions, clusters, crud).
"""
from __future__ import annotations

import logging
from collections import defaultdict
from datetime import datetime
from typing import Optional
from uuid import UUID

from sqlalchemy import select, update

from fernkam.api.schemas import FaceOut
from fernkam.db.models.photos import Face, Photo, Tag

logger = logging.getLogger(__name__)


# ─────────────────────────── tiny helpers ─────────────────────────────────────

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


# Single 0-1 "sensitivity" knob the UI exposes, replacing what used to be two
# disconnected, differently-scaled confidence sliders. Persisted in app_settings
# so it survives restarts without an env-var edit; the sweep, similarity
# propagation, and cluster auto-assign all read the same value. 1.0 = maximally
# aggressive auto-confirm; the margin/vote checks stay the real guardrail
# against false positives, so leaning aggressive here is meant to be safe to
# undo (see the "recently auto-confirmed" audit queue) rather than risky.
FACE_SENSITIVITY_KEY = "face_sensitivity"
DEFAULT_SENSITIVITY = 0.65


async def _resolve_sensitivity(db) -> float:
    from fernkam.db.app_settings import get_setting
    raw = await get_setting(db, FACE_SENSITIVITY_KEY)
    if raw is None:
        return DEFAULT_SENSITIVITY
    try:
        return max(0.0, min(1.0, float(raw)))
    except (TypeError, ValueError):
        return DEFAULT_SENSITIVITY


def _sensitivity_to_thresholds(sensitivity: float) -> tuple[float, float, float]:
    """Map 0-1 sensitivity to (auto_confirm_thresh, knn_margin, adaptive_floor)."""
    sensitivity = max(0.0, min(1.0, sensitivity))
    thresh = 0.90 - 0.20 * sensitivity   # 0.90 (cautious) .. 0.70 (aggressive)
    margin = 0.08 - 0.05 * sensitivity   # 0.08 .. 0.03
    floor = 0.85 - 0.20 * sensitivity    # 0.85 .. 0.65
    return round(thresh, 4), round(margin, 4), round(floor, 4)


async def _thresholds(db):
    from fernkam.config import get_settings
    s = get_settings()
    sensitivity = await _resolve_sensitivity(db)
    auto_thresh, margin, floor = _sensitivity_to_thresholds(sensitivity)
    return (
        auto_thresh, s.suggest_thresh,
        s.knn_k, s.knn_min_votes, margin, s.min_ref_score,
        s.min_det_score, s.min_face_px, s.min_blur_score,
        floor,
    )


def _adaptive_auto_confirm_thresh(base: float, face_count: int, floor: float = 0.80) -> float:
    """Loosen auto-confirm score for people with many confirmed examples.

    A person with only a few confirmed faces keeps the strict base threshold,
    while someone with hundreds of examples can auto-confirm at a lower score
    because their centroid is well-anchored.  The margin/vote checks are the
    real guardrails against false positives.
    """
    if face_count < 20:
        return base
    # linearly relax down to `floor` as the pool grows from 20 to ~300 faces
    ratio = min(1.0, (face_count - 20) / 280.0)
    return max(floor, base - (base - floor) * ratio)


async def _resolve_person_min_dates(db) -> "dict[int, object]":
    """Return {person_tag_id: datetime.date} from FERNKAM_PERSON_MIN_DATES config.

    Looks up person names in the Tag table so callers work with integer IDs.
    Returns an empty dict if the config is unset or unparseable.
    """
    import json
    from datetime import date as _date
    from fernkam.config import get_settings
    try:
        raw: dict = json.loads(get_settings().person_min_dates or "{}")
        if not raw:
            return {}
        name_rows = (await db.execute(
            select(Tag.id, Tag.name).where(Tag.name.in_(list(raw.keys())))
        )).fetchall()
        return {
            int(tid): _date.fromisoformat(raw[name])
            for tid, name in name_rows
            if name in raw
        }
    except Exception as exc:
        logger.warning("[BIRTH-DATE] config parse error: %s", exc)
        return {}


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


# ──────────────────────────── auto-confirm sweep ──────────────────────────────

async def _auto_confirm_sweep(db, since_dt: Optional[datetime] = None) -> dict:
    """k-NN voting + margin test sweep against confirmed/ignored pools.

    For each unconfirmed/suggested face:
    1. Find the nearest K confirmed faces via HNSW LATERAL.
    2. Aggregate per-person: vote count + best cosine score.
    3. Auto-confirm if: best_score >= adaptive_thresh AND votes >= min_votes AND
       (best_score - 2nd_place_score) >= margin.
    4. Promote to 'suggested' if best_score >= suggest_thresh (review queue).

    since_dt: if provided, only process faces created after this timestamp
              (incremental sweep — skip already-scored faces from prior runs).
    Returns count of auto-confirmed faces in this pass.
    """
    from sqlalchemy import text as _sql

    auto_thresh, suggest_thresh, knn_k, min_votes, margin, min_ref_score, min_det, min_px, min_blur, adaptive_floor = await _thresholds(db)

    since_filter = "AND created_at > :since_dt" if since_dt else ""
    since_params: dict = {"since_dt": since_dt} if since_dt else {}
    quality_filter = ""
    if min_det and min_det > 0:
        quality_filter += " AND (det_score IS NULL OR det_score >= :min_det)"
    if min_px and min_px > 0:
        quality_filter += " AND w >= :min_px AND h >= :min_px"
    if min_blur and min_blur > 0:
        quality_filter += " AND (blur_score IS NULL OR blur_score >= :min_blur)"
    quality_params: dict = {"min_det": min_det or 0.0, "min_px": min_px or 0, "min_blur": min_blur or 0.0}

    # ── 1. Ignored pool: nearest ignored face >= auto_thresh → auto-ignore ──
    ignored_q = _sql(f"""
        WITH unc AS (
            SELECT id, embedding_v
            FROM faces
            WHERE status IN ('unconfirmed', 'suggested')
              AND embedding_v IS NOT NULL
              {since_filter}
              {quality_filter}
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
        rows = (await db.execute(ignored_q, {"thresh": auto_thresh - 0.005, **since_params, **quality_params})).fetchall()
        auto_ignore = [r[0] for r in rows]
    except Exception as e:
        logger.warning("[AUTO-CONFIRM] ignored pool query skipped: %s", e)

    # ── 2. Confirmed pool: prefer person_centroids (multi-centroid, faster) ──
    centroid_count = (await db.execute(_sql(
        "SELECT count(*) FROM person_centroids WHERE embedding_v IS NOT NULL"
    ))).scalar_one()

    if centroid_count > 0:
        confirmed_q = _sql(f"""
            WITH unc AS (
                SELECT id, person_tag_id, embedding_v
                FROM faces
                WHERE status IN ('unconfirmed', 'suggested')
                  AND embedding_v IS NOT NULL
                  AND ( :exclude_count = 0 OR id <> ALL(CAST(:exclude_ids AS uuid[])) )
                  {since_filter}
                  {quality_filter}
            )
            SELECT u.id          AS face_id,
                   u.person_tag_id AS unc_ptid,
                   c.person_tag_id AS nbr_ptid,
                   1 - (u.embedding_v <=> c.embedding_v) AS score
            FROM unc u
            CROSS JOIN LATERAL (
                SELECT person_tag_id, embedding_v
                FROM person_centroids
                WHERE embedding_v IS NOT NULL
                ORDER BY embedding_v <=> u.embedding_v
                LIMIT :k
            ) c
        """)
    else:
        confirmed_q = _sql(f"""
            WITH unc AS (
                SELECT id, person_tag_id, embedding_v
                FROM faces
                WHERE status IN ('unconfirmed', 'suggested')
                  AND embedding_v IS NOT NULL
                  AND ( :exclude_count = 0 OR id <> ALL(CAST(:exclude_ids AS uuid[])) )
                  {since_filter}
                  {quality_filter}
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
                  AND (best_match_score IS NULL OR best_match_score >= :ref_min)
                ORDER BY embedding_v <=> u.embedding_v
                LIMIT :k
            ) c
        """)
    try:
        params = {
            "exclude_count": len(auto_ignore),
            "exclude_ids": [str(x) for x in auto_ignore],
            "k": knn_k,
            "ref_min": min_ref_score,
            **since_params,
            **quality_params,
        }
        rows = (await db.execute(confirmed_q, params)).fetchall()
    except Exception as e:
        logger.warning("[AUTO-CONFIRM] confirmed pool query skipped: %s", e)
        rows = []

    # ── 3. Aggregate k-NN votes per person per face ──
    face_votes: dict = defaultdict(lambda: defaultdict(lambda: [0, 0.0]))
    face_unc_ptid: dict = {}

    # Confidence scaling: how many confirmed examples each person has
    fc_rows = (await db.execute(_sql(
        "SELECT person_tag_id, count(*) FROM faces "
        "WHERE status = 'confirmed' AND person_tag_id IS NOT NULL "
        "GROUP BY person_tag_id"
    ))).fetchall()
    person_face_counts = {int(ptid): int(cnt) for ptid, cnt in fc_rows}

    # ── 3.5 Resolve person birth dates for assignment filtering ──
    person_min_dates: dict = await _resolve_person_min_dates(db)
    face_photo_dates: dict = {}  # face_id -> datetime.date | None  (populated after vote loop)

    for face_id, unc_ptid, nbr_ptid, score in rows:
        if nbr_ptid is None:
            continue
        s = float(score)
        entry = face_votes[face_id][nbr_ptid]
        entry[0] += 1
        if s > entry[1]:
            entry[1] = s
        face_unc_ptid[face_id] = unc_ptid

    if person_min_dates and face_votes:
        ph_date_rows = (await db.execute(_sql(
            "SELECT f.id, p.taken_at FROM faces f "
            "JOIN photos p ON p.id = f.photo_id "
            "WHERE f.id = ANY(CAST(:fids AS uuid[]))"
        ), {"fids": [str(f) for f in face_votes.keys()]})).fetchall()
        for fid, taken in ph_date_rows:
            face_photo_dates[fid] = taken.date() if taken else None

    # ── 4. Apply decision rules ──
    pending: dict = {}
    suggest_updates: list = []
    score_updates: list = []

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

        # Birth-date guard: nullify target if photo predates person's birth
        if target is not None and person_min_dates:
            min_date = person_min_dates.get(int(target))
            if min_date:
                photo_date = face_photo_dates.get(face_id)
                if photo_date is not None and photo_date < min_date:
                    target = None

        margin_ok = (p1_score - p2_score) >= margin
        votes_ok = p1_votes >= min_votes
        effective_thresh = _adaptive_auto_confirm_thresh(
            auto_thresh, person_face_counts.get(int(target), 0) if target is not None else 0,
            floor=adaptive_floor,
        )
        score_ok = round(p1_score, 2) >= effective_thresh

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
    # Chunked, parameterized executemany instead of a single UPDATE ... FROM
    # (VALUES ...) built by string concatenation — over a full sweep (tens of
    # thousands of faces) that string could reach multiple MB of SQL text.
    if score_updates:
        stmt = _sql("UPDATE faces SET best_match_score = :s WHERE id = :id")
        CHUNK = 5000
        for i in range(0, len(score_updates), CHUNK):
            await db.execute(stmt, score_updates[i:i + CHUNK])

    # ── 7. Persist: auto-confirm (with duplicate guard, TWINS-aware) ──
    clean: dict = {}
    if pending:
        pending_ids = list(pending.keys())
        ph_rows = (await db.execute(
            select(Face.id, Face.photo_id).where(Face.id.in_(pending_ids))
        )).fetchall()
        photo_map = {fid: phid for fid, phid in ph_rows}

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
                .values(person_tag_id=ptid, status="confirmed", confirmed_by="auto")
            )

    # ── 8. Persist: promote uncertain faces to suggested ──
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
        "confirmed_person_ids": sorted(set(clean.values())),
    }


async def _rebuild_person_centroids(
    db, n_clusters: int = 3, person_ids: "Optional[set[int]]" = None
) -> dict:
    """Recompute per-person centroid embeddings using k-means sub-clustering.

    Mirrors the /build-centroids endpoint but usable as a helper from the sweep
    loop so newly-confirmed faces can immediately influence the next pass.

    person_ids: if provided, only rebuild centroids for these person_tag_ids
                (e.g. the people confirmed in the previous sweep pass) instead
                of recomputing k-means for every person — keeps interleaved
                rebuilds cheap during a multi-pass sweep.
    """
    from sqlalchemy import text as _sql

    if person_ids:
        rows = (await db.execute(_sql("""
            SELECT person_tag_id, embedding_v::text AS emb_text
            FROM   faces
            WHERE  status = 'confirmed'
              AND  person_tag_id = ANY(:pids)
              AND  embedding_v   IS NOT NULL
        """), {"pids": list(person_ids)})).fetchall()
    else:
        rows = (await db.execute(_sql("""
            SELECT person_tag_id, embedding_v::text AS emb_text
            FROM   faces
            WHERE  status = 'confirmed'
              AND  person_tag_id IS NOT NULL
              AND  embedding_v   IS NOT NULL
        """))).fetchall()

    if not rows:
        return {"updated": 0, "n_clusters": n_clusters}

    # Parsing every embedding (pure-Python float() per dimension, ~20M calls
    # over the full confirmed pool) and the k-means passes are CPU-bound with
    # no DB access — run off the event loop so this doesn't stall the server
    # for its whole duration. It's awaited directly from two request handlers
    # (POST /build-centroids and POST /people/{id}/merge).
    import asyncio
    loop = asyncio.get_event_loop()
    person_centroids = await loop.run_in_executor(None, _compute_person_centroids, rows, n_clusters)

    total_updated = 0
    for ptid, label_data in person_centroids.items():
        for label, lit, face_cnt in label_data:
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
            WHERE person_tag_id = {ptid} AND label >= {len(label_data)}
        """))

        total_updated += 1

    await db.commit()
    return {"updated": total_updated, "n_clusters": n_clusters}


def _compute_person_centroids(rows, n_clusters: int) -> dict:
    """Blocking: parse embeddings and run k-means per person. Call via run_in_executor.

    Returns {person_tag_id: [(label, pgvector_literal, face_count), ...]}.
    """
    import numpy as np
    from fernkam.face_processor import _pgvector_literal

    person_embs: dict = defaultdict(list)
    for ptid, emb_text in rows:
        arr = np.array([float(x) for x in emb_text.strip("[]").split(",")], dtype=np.float32)
        person_embs[int(ptid)].append(arr)

    result: dict = {}
    for ptid, embs in person_embs.items():
        mat = np.array(embs, dtype=np.float32)
        k = max(1, min(n_clusters, len(embs) // 2))
        centers, assignments = _kmeans_np(mat, k)

        labels_out = []
        for label in range(len(centers)):
            c = centers[label]
            norm = float(np.linalg.norm(c))
            if norm > 0:
                c = c / norm
            lit = _pgvector_literal(c)
            face_cnt = int((assignments == label).sum())
            labels_out.append((label, lit, face_cnt))
        result[ptid] = labels_out

    return result


def _kmeans_np(X, k: int, n_iter: int = 25):
    """Minimal Lloyd's k-means++ on a numpy float32 matrix.

    Returns (centroids_array shape [k, d], assignments array shape [n]).
    Falls back to single centroid when k >= len(X).
    """
    import numpy as np

    n = len(X)
    if k >= n:
        return X.mean(axis=0, keepdims=True), np.zeros(n, dtype=int)

    rng = np.random.default_rng(42)
    idxs = [int(rng.integers(n))]
    for _ in range(k - 1):
        dists = np.min(np.sum((X[:, None, :] - X[idxs, :][None, :, :]) ** 2, axis=2), axis=1)
        probs = dists / dists.sum()
        idxs.append(int(rng.choice(n, p=probs)))
    centers = X[idxs].copy()

    assignments = np.zeros(n, dtype=int)
    for _ in range(n_iter):
        dists = np.sum((X[:, None, :] - centers[None, :, :]) ** 2, axis=2)
        new_asgn = np.argmin(dists, axis=1)
        if np.array_equal(new_asgn, assignments):
            break
        assignments = new_asgn
        for j in range(k):
            mask = assignments == j
            if mask.any():
                centers[j] = X[mask].mean(axis=0)
    return centers, assignments


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
              AND (best_match_score IS NULL OR best_match_score >= :ref_min)
            ORDER BY embedding_v <=> u.embedding_v
            LIMIT 1
        ) s
        WHERE 1 - (u.embedding_v <=> s.embedding_v) >= :thresh
        """
    )
    thresholds = await _thresholds(db)
    auto_thresh, min_ref_score = thresholds[0], thresholds[5]
    rows = (await db.execute(q, {"seeds": seed_str, "thresh": auto_thresh - 0.005, "ref_min": min_ref_score})).fetchall()
    auto_ids = [r[0] for r in rows]
    if not auto_ids:
        return

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
            .values(person_tag_id=person_tag_id, status="confirmed", confirmed_by="auto")
        )
        await db.commit()


# ─────────────────────────── cluster helper ───────────────────────────────────

async def _rebuild_face_clusters(
    db, *, cluster_thresh: float, k: int, min_size: int
) -> int:
    """Build same-person clusters of unconfirmed faces and persist to face_clusters.

    Uses the HNSW pgvector index to find each face's k nearest unconfirmed
    neighbours, keeps edges with cosine similarity >= cluster_thresh, then
    union-finds the edge list into connected components. Singletons (no edge
    above threshold) are dropped. Returns the number of clusters written.
    """
    from collections import defaultdict
    from sqlalchemy import text as _sql

    # Boost ef_search so the HNSW index scans enough candidates when
    # filtering to status='unconfirmed' (a subset of all indexed faces).
    await db.execute(_sql("SET LOCAL hnsw.ef_search = 300"))

    edges_q = _sql("""
        WITH unc AS (
            SELECT id, embedding_v
            FROM faces
            WHERE status = 'unconfirmed' AND embedding_v IS NOT NULL
              AND (:min_size = 0 OR (w >= :min_size AND h >= :min_size))
        )
        SELECT u.id AS a, nb.id AS b
        FROM unc u
        CROSS JOIN LATERAL (
            SELECT o.id, 1 - (o.embedding_v <=> u.embedding_v) AS score
            FROM faces o
            WHERE o.status = 'unconfirmed' AND o.embedding_v IS NOT NULL
              AND o.id <> u.id
              AND (:min_size = 0 OR (o.w >= :min_size AND o.h >= :min_size))
            ORDER BY o.embedding_v <=> u.embedding_v
            LIMIT :k
        ) nb
        WHERE nb.score >= :thresh
    """)
    rows = (await db.execute(
        edges_q, {"min_size": min_size, "k": k, "thresh": cluster_thresh}
    )).fetchall()

    # ── Union-find over the edge list ──
    parent: dict = {}

    def find(x):
        root = x
        while parent.get(root, root) != root:
            root = parent[root]
        while parent.get(x, x) != root:
            parent[x], x = root, parent.get(x, x)
        return root

    def union(a, b):
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[rb] = ra

    for a, b in rows:
        if a not in parent:
            parent[a] = a
        if b not in parent:
            parent[b] = b
        union(a, b)

    # ── Group faces by root; assign sequential cluster ids ──
    groups: dict = defaultdict(list)
    for node in parent:
        groups[find(node)].append(node)

    cluster_rows: list = []
    cid = 0
    for members in groups.values():
        if len(members) < 2:
            continue
        for fid in members:
            cluster_rows.append({"fid": str(fid), "cid": cid})
        cid += 1

    # ── Persist: truncate + bulk insert ──
    # Chunked, parameterized executemany instead of one INSERT built by string
    # concatenation over every clustered face (can be tens of thousands of
    # rows for a full unconfirmed-pool rebuild).
    await db.execute(_sql("TRUNCATE TABLE face_clusters"))
    if cluster_rows:
        ins = _sql("INSERT INTO face_clusters (face_id, cluster_id) VALUES (:fid, :cid)")
        CHUNK = 5000
        for i in range(0, len(cluster_rows), CHUNK):
            await db.execute(ins, cluster_rows[i:i + CHUNK])
    await db.commit()
    return cid
