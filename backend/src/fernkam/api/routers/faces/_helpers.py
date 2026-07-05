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


def _thresholds():
    from fernkam.config import get_settings
    s = get_settings()
    return (
        s.auto_confirm_thresh, s.suggest_thresh,
        s.knn_k, s.knn_min_votes, s.knn_margin, s.min_ref_score,
        s.min_det_score, s.min_face_px, s.min_blur_score,
    )


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

async def _auto_confirm_sweep(db, since_dt: Optional[datetime] = None) -> int:
    """k-NN voting + margin test sweep against confirmed/ignored pools.

    For each unconfirmed/suggested face:
    1. Find the nearest K confirmed faces via HNSW LATERAL.
    2. Aggregate per-person: vote count + best cosine score.
    3. Auto-confirm if: best_score >= auto_thresh AND votes >= min_votes AND
       (best_score - 2nd_place_score) >= margin.
    4. Promote to 'suggested' if best_score >= suggest_thresh (review queue).

    since_dt: if provided, only process faces created after this timestamp
              (incremental sweep — skip already-scored faces from prior runs).
    Returns count of auto-confirmed faces in this pass.
    """
    from sqlalchemy import text as _sql

    auto_thresh, suggest_thresh, knn_k, min_votes, margin, min_ref_score, min_det, min_px, min_blur = _thresholds()

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
              AND (best_match_score IS NULL OR best_match_score >= :ref_min)
            ORDER BY embedding_v <=> u.embedding_v
            LIMIT 1
        ) s
        WHERE 1 - (u.embedding_v <=> s.embedding_v) >= :thresh
        """
    )
    thresholds = _thresholds()
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
            .values(person_tag_id=person_tag_id, status="confirmed")
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
    await db.execute(_sql("TRUNCATE TABLE face_clusters"))
    if cluster_rows:
        ins = _sql(
            "INSERT INTO face_clusters (face_id, cluster_id) VALUES "
            + ",".join(
                f"('{r['fid']}'::uuid, {r['cid']})" for r in cluster_rows
            )
        )
        await db.execute(ins)
    await db.commit()
    return cid
