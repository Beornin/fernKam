"""Face clustering endpoints: rebuild, list, ignore-tiny, auto-assign."""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Query
from sqlalchemy import select, update

from fernkam.api.deps import DB
from fernkam.db.models.photos import Face, Tag

from ._helpers import _rebuild_face_clusters

router = APIRouter()


@router.post("/clusters/rebuild", response_model=dict)
async def rebuild_clusters(
    min_size: int = Query(0, ge=0),
    cluster_thresh: Optional[float] = Query(None),
    k: int = Query(30, ge=2, le=100),
) -> dict:
    """Kick off cluster rebuild as a background task. Returns a task_id."""
    import asyncio
    from fernkam.task_manager import task_manager
    from fernkam.config import get_settings
    from fernkam.db.session import async_session_factory as _session_factory

    thresh = cluster_thresh if cluster_thresh is not None else max(
        get_settings().suggest_thresh, 0.72
    )
    task_id = await task_manager.create_task("cluster_rebuild", "Building face clusters…")

    async def _run() -> None:
        async with _session_factory() as bg_db:
            try:
                n = await _rebuild_face_clusters(
                    bg_db, cluster_thresh=thresh, k=k, min_size=min_size
                )
                await task_manager.update_task(
                    task_id, status="completed",
                    message=f"Built {n} clusters",
                    progress={"clusters": n},
                )
            except Exception as exc:  # noqa: BLE001
                await task_manager.update_task(task_id, status="failed", message=str(exc))

    asyncio.create_task(_run())
    return {"task_id": task_id, "status": "started", "threshold": thresh}


@router.get("/clusters")
async def list_clusters(
    db: DB,
    offset: int = Query(0, ge=0),
    limit: int = Query(20, ge=1, le=100),
) -> dict:
    """Paginated clusters (largest first) of currently-unconfirmed faces, each
    with member face crops and a top suggested person (centroid k-NN)."""
    from collections import defaultdict
    from sqlalchemy import text as _sql
    from fernkam.config import get_settings

    ref_min = get_settings().min_ref_score

    # 1. Page of cluster ids ordered by current (still-unconfirmed) size.
    page_q = _sql("""
        SELECT fc.cluster_id AS cid, COUNT(*) AS size
        FROM face_clusters fc
        JOIN faces f ON f.id = fc.face_id AND f.status = 'unconfirmed'
        GROUP BY fc.cluster_id
        HAVING COUNT(*) >= 2
        ORDER BY size DESC, fc.cluster_id ASC
        OFFSET :offset LIMIT :limit
    """)
    page_rows = (await db.execute(page_q, {"offset": offset, "limit": limit})).fetchall()
    if not page_rows:
        # total remaining cluster count for UI progress
        total = (await db.execute(_sql("""
            SELECT COUNT(*) FROM (
                SELECT fc.cluster_id
                FROM face_clusters fc
                JOIN faces f ON f.id = fc.face_id AND f.status = 'unconfirmed'
                GROUP BY fc.cluster_id HAVING COUNT(*) >= 2
            ) t
        """))).scalar_one()
        return {"clusters": [], "total": total}

    cids = [r[0] for r in page_rows]
    size_by_cid = {r[0]: int(r[1]) for r in page_rows}

    # 2. Member faces for these clusters.
    mem_q = _sql("""
        SELECT fc.cluster_id, f.id, f.photo_id
        FROM face_clusters fc
        JOIN faces f ON f.id = fc.face_id AND f.status = 'unconfirmed'
        WHERE fc.cluster_id = ANY(CAST(:cids AS int[]))
        ORDER BY fc.cluster_id
    """)
    mem_rows = (await db.execute(mem_q, {"cids": cids})).fetchall()

    members: dict = defaultdict(list)
    for cid, fid, phid in mem_rows:
        members[cid].append({"id": str(fid), "photo_id": phid})

    # 3. Suggestion per cluster: sample up to 5 faces, k-NN vs confirmed pool.
    sample_ids: list = []
    sample_to_cid: dict = {}
    for cid in cids:
        for m in members[cid][:5]:
            sample_ids.append(m["id"])
            sample_to_cid[m["id"]] = cid

    suggestion: dict = {}  # cid -> {person_id, person_name, score}
    if sample_ids:
        knn_q = _sql("""
            WITH samp AS (
                SELECT id, embedding_v FROM faces
                WHERE id = ANY(CAST(:ids AS uuid[]))
            )
            SELECT s.id AS fid, c.person_tag_id AS pid,
                   1 - (s.embedding_v <=> c.embedding_v) AS score
            FROM samp s
            CROSS JOIN LATERAL (
                SELECT person_tag_id, embedding_v FROM faces
                WHERE status = 'confirmed' AND person_tag_id IS NOT NULL
                  AND embedding_v IS NOT NULL
                  AND (best_match_score IS NULL OR best_match_score >= :ref_min)
                ORDER BY embedding_v <=> s.embedding_v LIMIT 5
            ) c
        """)
        knn_rows = (await db.execute(
            knn_q, {"ids": sample_ids, "ref_min": ref_min}
        )).fetchall()

        # cid -> {person_id -> best_score}
        cluster_person: dict = defaultdict(lambda: defaultdict(float))
        for fid, pid, score in knn_rows:
            if pid is None:
                continue
            cid = sample_to_cid.get(str(fid))
            if cid is None:
                continue
            s = float(score)
            if s > cluster_person[cid][pid]:
                cluster_person[cid][pid] = s

        all_pids = {pid for d in cluster_person.values() for pid in d}
        names: dict = {}
        if all_pids:
            name_rows = (await db.execute(
                select(Tag.id, Tag.name).where(Tag.id.in_(all_pids))
            )).fetchall()
            names = {r[0]: r[1] for r in name_rows}

        for cid, persons in cluster_person.items():
            best_pid, best_score = max(persons.items(), key=lambda kv: kv[1])
            suggestion[cid] = {
                "person_id": best_pid,
                "person_name": names.get(best_pid),
                "score": round(best_score, 2),
            }

    total = (await db.execute(_sql("""
        SELECT COUNT(*) FROM (
            SELECT fc.cluster_id
            FROM face_clusters fc
            JOIN faces f ON f.id = fc.face_id AND f.status = 'unconfirmed'
            GROUP BY fc.cluster_id HAVING COUNT(*) >= 2
        ) t
    """))).scalar_one()

    clusters = [
        {
            "cluster_id": cid,
            "size": size_by_cid[cid],
            "faces": members[cid],
            "suggested_person": suggestion.get(cid),
        }
        for cid in cids
    ]
    return {"clusters": clusters, "total": total}


@router.post("/ignore-tiny")
async def ignore_tiny_faces(
    db: DB,
    max_size: int = Query(50, ge=1),
) -> dict:
    """Mark all unconfirmed faces smaller than max_size (in either dimension)
    as ignored. Quick triage for unidentifiable junk crops."""
    result = await db.execute(
        update(Face)
        .where(Face.status == "unconfirmed")
        .where((Face.w < max_size) | (Face.h < max_size))
        .values(status="ignored", person_tag_id=None)
    )
    await db.commit()
    return {"ignored": result.rowcount, "max_size": max_size}


@router.post("/clusters/auto-assign", response_model=dict)
async def auto_assign_clusters(
    min_score: float = Query(0.82, ge=0.50, le=1.0),
    min_agreement: float = Query(0.60, ge=0.0, le=1.0),
    sample_k: int = Query(5, ge=1, le=10),
    dry_run: bool = Query(False),
) -> dict:
    """Background task: auto-confirm clusters where k-NN consensus score >= min_score.

    Iterates all unconfirmed clusters, samples up to sample_k faces per cluster,
    runs a k-NN vote against the confirmed pool, and assigns any cluster whose
    top-person avg similarity >= min_score AND agreement fraction >= min_agreement.
    Returns a task_id to poll for progress.
    """
    import asyncio as _asyncio
    from collections import defaultdict as _dd
    from sqlalchemy import text as _sql
    from fernkam.task_manager import task_manager
    from fernkam.db.session import async_session_factory as _factory
    from fernkam.config import get_settings

    task_id = await task_manager.create_task(
        "cluster_auto_assign",
        f"Auto-assigning clusters (min_score={min_score:.2f})…",
    )

    async def _run() -> None:
        assigned_clusters = 0
        assigned_faces = 0
        checked = 0
        try:
            async with _factory() as bg_db:
                ref_min = get_settings().min_ref_score

                cid_rows = (await bg_db.execute(_sql("""
                    SELECT fc.cluster_id
                    FROM face_clusters fc
                    JOIN faces f ON f.id = fc.face_id AND f.status = 'unconfirmed'
                    GROUP BY fc.cluster_id HAVING COUNT(*) >= 2
                    ORDER BY COUNT(*) DESC
                """))).fetchall()
                all_cids = [r[0] for r in cid_rows]
                total_clusters = len(all_cids)

                if not total_clusters:
                    await task_manager.update_task(
                        task_id, status="completed",
                        message="No clusters to process.",
                        progress={"assigned_clusters": 0, "assigned_faces": 0, "checked": 0},
                    )
                    return

                CHUNK = 200
                for chunk_start in range(0, total_clusters, CHUNK):
                    chunk = all_cids[chunk_start: chunk_start + CHUNK]

                    mem_rows = (await bg_db.execute(_sql("""
                        SELECT fc.cluster_id, f.id AS face_id
                        FROM face_clusters fc
                        JOIN faces f ON f.id = fc.face_id AND f.status = 'unconfirmed'
                          AND f.embedding_v IS NOT NULL
                        WHERE fc.cluster_id = ANY(CAST(:cids AS int[]))
                    """), {"cids": chunk})).fetchall()

                    cluster_all: dict = _dd(list)
                    cluster_sample: dict = _dd(list)
                    for cid, fid in mem_rows:
                        sfid = str(fid)
                        cluster_all[cid].append(sfid)
                        if len(cluster_sample[cid]) < sample_k:
                            cluster_sample[cid].append(sfid)

                    sample_ids = [fid for fids in cluster_sample.values() for fid in fids]
                    sample_to_cid = {fid: cid for cid, fids in cluster_sample.items() for fid in fids}

                    cluster_votes: dict = _dd(lambda: _dd(list))
                    if sample_ids:
                        knn_rows = (await bg_db.execute(_sql("""
                            WITH samp AS (
                                SELECT id, embedding_v FROM faces
                                WHERE id = ANY(CAST(:ids AS uuid[]))
                            )
                            SELECT s.id AS fid, c.person_tag_id AS pid,
                                   1 - (s.embedding_v <=> c.embedding_v) AS score
                            FROM samp s
                            CROSS JOIN LATERAL (
                                SELECT person_tag_id, embedding_v FROM faces
                                WHERE status = 'confirmed' AND person_tag_id IS NOT NULL
                                  AND embedding_v IS NOT NULL
                                  AND (best_match_score IS NULL OR best_match_score >= :ref_min)
                                ORDER BY embedding_v <=> s.embedding_v LIMIT 1
                            ) c
                        """), {"ids": sample_ids, "ref_min": ref_min})).fetchall()

                        for fid, pid, score in knn_rows:
                            if pid is None:
                                continue
                            cid = sample_to_cid.get(str(fid))
                            if cid is not None:
                                cluster_votes[cid][pid].append(float(score))

                    to_assign: list = []
                    for cid in chunk:
                        checked += 1
                        if cid not in cluster_votes:
                            continue
                        votes = cluster_votes[cid]
                        best_pid = max(votes, key=lambda p: (len(votes[p]), sum(votes[p])))
                        scores = votes[best_pid]
                        avg_score = sum(scores) / len(scores)
                        n_samples = len(cluster_sample.get(cid, []))
                        agreement = len(scores) / n_samples if n_samples else 0.0
                        if avg_score >= min_score and agreement >= min_agreement:
                            to_assign.append((cid, best_pid, cluster_all[cid]))

                    if not dry_run and to_assign:
                        for cid, pid, face_ids in to_assign:
                            await bg_db.execute(_sql("""
                                UPDATE faces SET status='confirmed', person_tag_id=:pid
                                WHERE id = ANY(CAST(:ids AS uuid[]))
                                  AND status = 'unconfirmed'
                            """), {"pid": pid, "ids": face_ids})
                            await bg_db.execute(_sql(
                                "DELETE FROM face_clusters WHERE cluster_id = :cid"
                            ), {"cid": cid})
                            assigned_faces += len(face_ids)
                        await bg_db.commit()

                    if dry_run:
                        for _, _, face_ids in to_assign:
                            assigned_faces += len(face_ids)

                    assigned_clusters += len(to_assign)

                    await task_manager.update_task(
                        task_id,
                        message=(
                            f"Checked {checked}/{total_clusters} · "
                            f"assigned {assigned_clusters} clusters ({assigned_faces} faces)…"
                        ),
                        progress={
                            "checked": checked,
                            "total": total_clusters,
                            "assigned_clusters": assigned_clusters,
                            "assigned_faces": assigned_faces,
                        },
                    )

                suffix = " (dry run)" if dry_run else ""
                await task_manager.update_task(
                    task_id, status="completed",
                    message=(
                        f"Done: {assigned_clusters} clusters / {assigned_faces} faces "
                        f"auto-assigned{suffix}"
                    ),
                    progress={
                        "assigned_clusters": assigned_clusters,
                        "assigned_faces": assigned_faces,
                        "checked": checked,
                    },
                )
        except Exception as exc:
            import logging as _log
            _log.getLogger(__name__).warning("cluster auto-assign error: %s", exc)
            await task_manager.update_task(task_id, status="failed", message=str(exc))

    _asyncio.create_task(_run(), name="fernkam-cluster-auto-assign")
    return {"task_id": task_id, "status": "started", "threshold": min_score, "dry_run": dry_run}
