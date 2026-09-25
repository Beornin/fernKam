"""Tag Review: approve or reject tags, like Face Review does for faces.

Every tag starts unverified. The user works through one tag at a time:
  unverified   tags already on photos (from files, digiKam, workflows)
  suggested    photos the tag's model thinks should have it
  approved     checked and right; these train the model
  rejected     checked and wrong; removed from the photo, and a negative example

Approving or rejecting is the only way labels reach tag_learning.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Literal

from fastapi import APIRouter, Body, HTTPException, Query
from sqlalchemy import text

from fernkam import tag_learning
from fernkam.api.deps import DB

logger = logging.getLogger(__name__)
router = APIRouter()

State = Literal["unverified", "suggested", "approved", "rejected"]


async def _tag(db, tag_id: int):
    tag = (await db.execute(text(
        "SELECT id, name, path::text AS path, is_person FROM tags WHERE id = :t"
    ), {"t": tag_id})).first()
    if not tag:
        raise HTTPException(404, "Tag not found")
    if tag.is_person:
        raise HTTPException(400, "People are reviewed on the Face Review page")
    return tag


def _model_out(row) -> dict | None:
    if row is None or row.trained_at is None:
        return None
    reviewed = row.reviewed_accepted + row.reviewed_rejected
    return {
        "trained_at": row.trained_at.isoformat(),
        "positives": row.positives,
        "negatives": row.negatives,
        "cv_agreement": row.cv_agreement,
        "cv_recall": row.cv_recall,
        "reviewed_accepted": row.reviewed_accepted,
        "reviewed_rejected": row.reviewed_rejected,
        # Of the suggestions the user has judged, the share that were right:
        # the model's real accuracy, measured by the review itself.
        "hit_rate": row.reviewed_accepted / reviewed if reviewed else None,
    }


@router.get("/summary")
async def summary(db: DB) -> dict:
    row = (await db.execute(text("""
        SELECT
          (SELECT count(*) FROM photo_tags pt JOIN tags t ON t.id = pt.tag_id
            WHERE NOT t.is_person AND pt.verified_at IS NULL) AS unverified,
          (SELECT count(*) FROM photo_tags pt JOIN tags t ON t.id = pt.tag_id
            WHERE NOT t.is_person AND pt.verified_at IS NOT NULL) AS approved,
          (SELECT count(*) FROM tag_suggestions) AS suggested,
          (SELECT count(*) FROM tag_rejections) AS rejected,
          (SELECT count(*) FROM tag_models) AS models,
          (SELECT count(*) FILTER (WHERE embedding_v IS NOT NULL) FROM photos WHERE status = 1) AS embedded,
          (SELECT count(*) FROM photos WHERE status = 1) AS photos
    """))).one()
    return {**row._asdict(), "min_positives": tag_learning.MIN_POSITIVES}


@router.get("/tags")
async def list_tags(db: DB) -> list[dict]:
    """Every tag with something to review or already reviewed, busiest first."""
    rows = (await db.execute(text("""
        WITH c AS (
            SELECT tag_id,
                   count(*) FILTER (WHERE verified_at IS NULL) AS unverified,
                   count(*) FILTER (WHERE verified_at IS NOT NULL) AS approved
            FROM photo_tags GROUP BY tag_id
        ),
        s AS (SELECT tag_id, count(*) AS n FROM tag_suggestions GROUP BY tag_id),
        r AS (SELECT tag_id, count(*) AS n FROM tag_rejections GROUP BY tag_id)
        SELECT t.id, t.name, t.path::text AS path,
               COALESCE(c.unverified, 0) AS unverified, COALESCE(c.approved, 0) AS approved,
               COALESCE(s.n, 0) AS suggested, COALESCE(r.n, 0) AS rejected,
               m.trained_at
        FROM tags t
        LEFT JOIN c ON c.tag_id = t.id
        LEFT JOIN s ON s.tag_id = t.id
        LEFT JOIN r ON r.tag_id = t.id
        LEFT JOIN tag_models m ON m.tag_id = t.id
        WHERE NOT t.is_person
          AND (c.tag_id IS NOT NULL OR s.tag_id IS NOT NULL OR r.tag_id IS NOT NULL)
        ORDER BY COALESCE(c.unverified, 0) + COALESCE(s.n, 0) DESC, t.path
    """))).all()
    return [
        {"id": r.id, "name": r.name, "path": r.path, "unverified": r.unverified,
         "approved": r.approved, "suggested": r.suggested, "rejected": r.rejected,
         "learning": r.trained_at is not None}
        for r in rows
    ]


@router.get("/tags/{tag_id}")
async def tag_detail(tag_id: int, db: DB) -> dict:
    tag = await _tag(db, tag_id)
    counts = (await db.execute(text("""
        SELECT
          (SELECT count(*) FROM photo_tags WHERE tag_id = :t AND verified_at IS NULL) AS unverified,
          (SELECT count(*) FROM photo_tags WHERE tag_id = :t AND verified_at IS NOT NULL) AS approved,
          (SELECT count(*) FROM tag_suggestions WHERE tag_id = :t) AS suggested,
          (SELECT count(*) FROM tag_rejections WHERE tag_id = :t) AS rejected
    """), {"t": tag_id})).one()
    model = (await db.execute(text(
        "SELECT * FROM tag_models WHERE tag_id = :t"), {"t": tag_id})).first()
    positives, negatives = await tag_learning.label_counts(db, tag.path)
    return {
        "id": tag.id, "name": tag.name, "path": tag.path,
        "counts": counts._asdict(),
        # Approved photos counting descendants: what the model learns from.
        "learning_positives": positives, "learning_negatives": negatives,
        "min_positives": tag_learning.MIN_POSITIVES,
        "model": _model_out(model),
    }


@router.get("/tags/{tag_id}/photos")
async def tag_photos(
    tag_id: int,
    db: DB,
    state: State = Query("unverified"),
    sort: Literal["date", "doubtful", "score", "recent"] = Query("date"),
    limit: int = Query(60, ge=1, le=500),
    offset: int = Query(0, ge=0),
) -> dict:
    await _tag(db, tag_id)
    if state == "unverified":
        src = ("photo_tags x", "x.tag_id = :t AND x.verified_at IS NULL", "x.model_score")
        order = {"doubtful": "x.model_score ASC NULLS LAST, p.id",
                 "score": "x.model_score DESC NULLS LAST, p.id"}.get(
            sort, "p.taken_at NULLS LAST, p.id")
    elif state == "approved":
        src = ("photo_tags x", "x.tag_id = :t AND x.verified_at IS NOT NULL", "x.model_score")
        order = "x.verified_at DESC, p.id DESC" if sort == "recent" else "p.taken_at NULLS LAST, p.id"
    elif state == "suggested":
        src = ("tag_suggestions x", "x.tag_id = :t", "x.score")
        order = "p.taken_at NULLS LAST, p.id" if sort == "date" else "x.score DESC, p.id"
    else:
        src = ("tag_rejections x", "x.tag_id = :t", "NULL::real")
        order = "x.rejected_at DESC, p.id DESC"
    table, where, score = src
    rows = (await db.execute(text(f"""
        SELECT p.id, p.filename, p.album_path, p.taken_at, p.media_type, {score} AS score,
               EXISTS (SELECT 1 FROM tag_rejections r
                       WHERE r.photo_id = p.id AND r.tag_id = :t) AS rejected_before,
               count(*) OVER () AS total
        FROM {table} JOIN photos p ON p.id = x.photo_id
        WHERE {where} AND p.status = 1
        ORDER BY {order}
        LIMIT :limit OFFSET :offset
    """), {"t": tag_id, "limit": limit, "offset": offset})).all()
    return {
        "total": rows[0].total if rows else 0,
        "photos": [
            {"photo_id": r.id, "filename": r.filename, "album_path": r.album_path,
             "taken_at": r.taken_at.isoformat() if r.taken_at else None,
             "media_type": r.media_type,
             "score": round(float(r.score), 4) if r.score is not None else None,
             "rejected_before": r.rejected_before and state != "rejected"}
            for r in rows
        ],
    }


@router.post("/tags/{tag_id}/decide")
async def decide(
    tag_id: int,
    db: DB,
    approve: list[int] = Body(default_factory=list),
    reject: list[int] = Body(default_factory=list),
) -> dict:
    """Record the user's decisions for one tag.

    approve: the photo has this tag. Unverified links are approved; suggestions
             and earlier rejections become approved links.
    reject:  the photo does not. The tag is removed from the photo (and from the
             file at the next write-back), and the photo becomes a negative
             example that is never suggested again.
    """
    await _tag(db, tag_id)
    approve, reject = sorted(set(approve)), sorted(set(reject))
    if set(approve) & set(reject):
        raise HTTPException(400, "A photo cannot be both approved and rejected")
    p = {"t": tag_id, "a": approve, "r": reject}
    to_write: set[int] = set()
    from_suggestions = {"accepted": 0, "rejected": 0}

    if approve:
        from_suggestions["accepted"] = len((await db.execute(text(
            "DELETE FROM tag_suggestions WHERE tag_id = :t AND photo_id = ANY(CAST(:a AS bigint[])) "
            "RETURNING photo_id"), p)).all())
        linked = (await db.execute(text("""
            INSERT INTO photo_tags (photo_id, tag_id, verified_at)
            SELECT id, :t, now() FROM photos WHERE id = ANY(CAST(:a AS bigint[]))
            ON CONFLICT (photo_id, tag_id)
                DO UPDATE SET verified_at = COALESCE(photo_tags.verified_at, EXCLUDED.verified_at)
            RETURNING photo_id, (xmax = 0) AS inserted
        """), p)).all()
        to_write |= {r.photo_id for r in linked if r.inserted}
        await db.execute(text(
            "DELETE FROM tag_rejections WHERE tag_id = :t AND photo_id = ANY(CAST(:a AS bigint[]))"), p)

    if reject:
        from_suggestions["rejected"] = len((await db.execute(text(
            "DELETE FROM tag_suggestions WHERE tag_id = :t AND photo_id = ANY(CAST(:r AS bigint[])) "
            "RETURNING photo_id"), p)).all())
        removed = {r.photo_id for r in (await db.execute(text(
            "DELETE FROM photo_tags WHERE tag_id = :t AND photo_id = ANY(CAST(:r AS bigint[])) "
            "RETURNING photo_id"), p)).all()}
        to_write |= removed
        await db.execute(text("""
            INSERT INTO tag_rejections (photo_id, tag_id, was_tagged)
            SELECT p.id, :t, p.id = ANY(CAST(:removed AS bigint[]))
            FROM photos p WHERE p.id = ANY(CAST(:r AS bigint[]))
            ON CONFLICT (photo_id, tag_id) DO UPDATE
                SET rejected_at = now(),
                    was_tagged = tag_rejections.was_tagged OR EXCLUDED.was_tagged
        """), {**p, "removed": sorted(removed)})

    if to_write:
        await db.execute(text(
            "UPDATE photos SET file_sync_dirty = true WHERE id = ANY(CAST(:ids AS bigint[]))"
        ), {"ids": sorted(to_write)})
    if from_suggestions["accepted"] or from_suggestions["rejected"]:
        await db.execute(text("""
            UPDATE tag_models SET reviewed_accepted = reviewed_accepted + :acc,
                                  reviewed_rejected = reviewed_rejected + :rej
            WHERE tag_id = :t
        """), {"t": tag_id, "acc": from_suggestions["accepted"], "rej": from_suggestions["rejected"]})
    await db.commit()

    # The feedback loop: enough new labels and the tag relearns right away.
    model = None
    try:
        model = await tag_learning.retrain_if_due(db, tag_id)
    except Exception:  # noqa: BLE001 — the decisions are saved either way
        logger.exception("retraining tag %s failed", tag_id)
        await db.rollback()
    return {
        "approved": len(approve), "rejected": len(reject),
        "files_to_update": len(to_write),
        "from_suggestions": from_suggestions,
        "retrained": model,
    }


@router.post("/tags/{tag_id}/train")
async def train(tag_id: int, db: DB) -> dict:
    tag = await _tag(db, tag_id)
    result = await tag_learning.train_tag(db, tag_id)
    if result is None:
        positives, _ = await tag_learning.label_counts(db, tag.path)
        raise HTTPException(400, (
            f"Needs {tag_learning.MIN_POSITIVES} approved photos with a search index entry "
            f"to learn from; {positives} approved so far"))
    return result


@router.post("/train-all")
async def train_all(db: DB) -> dict:
    """Relearn every tag with enough approved photos, as a background task.
    Also picks up newly imported photos for suggestions."""
    from fernkam.task_manager import task_manager

    tag_ids = await tag_learning.trainable_tags(db)
    if not tag_ids:
        return {"task_id": None, "tags": 0,
                "message": f"No tag has {tag_learning.MIN_POSITIVES} approved photos yet"}
    task_id = await task_manager.create_task(
        "tag_learning", f"Learning {len(tag_ids)} tags from your reviews…")

    async def _run() -> None:
        from fernkam.db.session import async_session_factory

        done = suggestions = failed = 0
        try:
            async with async_session_factory() as bdb:
                for tid in tag_ids:
                    task = await task_manager.get_task(task_id)
                    if task and task.status == "cancelled":
                        return
                    try:
                        r = await tag_learning.train_tag(bdb, tid)
                        suggestions += (r or {}).get("suggestions", 0)
                    except Exception:  # noqa: BLE001
                        logger.exception("learning tag %s failed", tid)
                        await bdb.rollback()
                        failed += 1
                    done += 1
                    await task_manager.update_task(
                        task_id, message=f"Learning tags… {done}/{len(tag_ids)}",
                        progress={"done": done, "total": len(tag_ids)})
            msg = f"Learned {done - failed} tags; {suggestions:,} suggestions to review"
            if failed:
                msg += f" ({failed} failed, see Logs)"
            await task_manager.update_task(task_id, status="completed", message=msg,
                                           progress={"done": done, "total": len(tag_ids)})
        except Exception as exc:  # noqa: BLE001
            logger.exception("tag learning task failed")
            await task_manager.update_task(task_id, status="failed", message=str(exc))

    asyncio.create_task(_run(), name=f"fernkam-tag-learning-{task_id}")
    return {"task_id": task_id, "tags": len(tag_ids), "message": "running"}
