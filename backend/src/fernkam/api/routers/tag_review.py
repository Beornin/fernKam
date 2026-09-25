"""Tag Review: approve or reject tags, like Face Review does for faces.

Every tag starts unverified. The user works through one tag at a time:
  unverified   tags already on photos (from files, digiKam, workflows)
  suggested    photos the tag's models think should have it (or, before the
               tag is learning, photos found by its name)
  approved     checked and right; these train the models
  rejected     checked and wrong; removed from the photo, and a negative example

Approving or rejecting is the only way labels reach tag_learning. The image
models (embed_models / embed_index) and the local vision model (vision_check)
are managed from here too.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Literal

from fastapi import APIRouter, Body, HTTPException, Query
from sqlalchemy import text

from fernkam import embed_index, embed_models, photo_context, species_range, tag_learning, vision_check
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
        # Per image model: its weight for this tag and its own numbers.
        "experts": row.experts or [],
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
          (SELECT count(*) FROM photos p WHERE p.status = 1 AND {tag_learning._HAS_VECTOR}) AS embedded,
          (SELECT count(*) FROM photos WHERE status = 1) AS photos
    """.replace("{tag_learning._HAS_VECTOR}", tag_learning._HAS_VECTOR)))).one()
    return {**row._asdict(), "min_positives": tag_learning.MIN_POSITIVES}


@router.get("/tags")
async def list_tags(db: DB) -> list[dict]:
    """Every tag, busiest first. Tags nobody has used yet come last: they can
    still be found by name."""
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
        ORDER BY COALESCE(c.unverified, 0) + COALESCE(s.n, 0) DESC,
                 (c.tag_id IS NULL AND r.tag_id IS NULL), t.path
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
    by_name = (await db.execute(text(
        "SELECT count(*) FROM tag_suggestions WHERE tag_id = :t AND source = 'name'"), {"t": tag_id})).scalar_one()
    return {
        "id": tag.id, "name": tag.name, "path": tag.path,
        "counts": counts._asdict(),
        # Approved photos counting descendants: what the model learns from.
        "learning_positives": positives, "learning_negatives": negatives,
        "min_positives": tag_learning.MIN_POSITIVES,
        "model": _model_out(model),
        "found_by_name": by_name,
        "name_models": await tag_learning.name_models(db),
        "vision": await vision_check.agreement(db, tag_id),
        "species": await _species(db, tag_id),
    }


async def _species(db, tag_id: int) -> dict | None:
    row = (await db.execute(text("SELECT * FROM tag_species WHERE tag_id = :t"), {"t": tag_id})).first()
    if not row:
        return None
    cells = await species_range.library_cells(db)
    have = {r[0] for r in (await db.execute(text(
        "SELECT cell FROM gbif_cell_counts WHERE taxon_key = :k AND month = 0"), {"k": row.taxon_key})).all()}
    return {"taxon_key": row.taxon_key, "scientific_name": row.scientific_name,
            "common_name": row.common_name, "rank": row.rank, "class_name": row.class_name,
            "range_fetched_at": row.range_fetched_at.isoformat() if row.range_fetched_at else None,
            "places": len(cells), "places_with_data": len(have & set(cells))}


@router.get("/tags/{tag_id}/photos")
async def tag_photos(
    tag_id: int,
    db: DB,
    state: State = Query("unverified"),
    sort: Literal["date", "doubtful", "score", "recent", "vision_yes", "vision_no"] = Query("date"),
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
    if sort == "vision_yes":     # what the vision model confirms, first
        order = f"c.verdict DESC NULLS LAST, c.p_yes DESC NULLS LAST, {score} DESC NULLS LAST, p.id"
    elif sort == "vision_no":    # what it disputes, first
        order = f"c.verdict ASC NULLS LAST, c.p_yes ASC NULLS LAST, {score} ASC NULLS LAST, p.id"
    source = "x.source" if state == "suggested" else "NULL"
    rows = (await db.execute(text(f"""
        SELECT p.id, p.filename, p.album_path, p.taken_at, p.media_type, {score} AS score,
               {source} AS source, c.verdict AS vision, c.p_yes AS vision_p,
               EXISTS (SELECT 1 FROM tag_rejections r
                       WHERE r.photo_id = p.id AND r.tag_id = :t) AS rejected_before,
               count(*) OVER () AS total
        FROM {table} JOIN photos p ON p.id = x.photo_id
        LEFT JOIN tag_checks c ON c.photo_id = p.id AND c.tag_id = :t
        WHERE {where} AND p.status = 1
        ORDER BY {order}
        LIMIT :limit OFFSET :offset
    """), {"t": tag_id, "limit": limit, "offset": offset})).all()
    # Where and when GBIF says the linked species is not (or hardly) recorded.
    ranges: dict[int, dict] = {}
    table = await species_range.RangeTable.load(db, tag_id)
    if table is not None and rows:
        meta = await photo_context.photo_meta(db, [r.id for r in rows])
        for pid, (lat, lon, _d, _h, month) in meta.items():
            st = table.status(lat, lon, month)
            if st:
                ranges[pid] = st
    return {
        "total": rows[0].total if rows else 0,
        "photos": [
            {"photo_id": r.id, "filename": r.filename, "album_path": r.album_path,
             "taken_at": r.taken_at.isoformat() if r.taken_at else None,
             "media_type": r.media_type,
             "score": round(float(r.score), 4) if r.score is not None else None,
             "source": r.source,
             "vision": r.vision,
             "vision_p": round(float(r.vision_p), 3) if r.vision_p is not None else None,
             "range": ranges.get(r.id),
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

    # Only the models' own suggestions count toward their hit rate, not
    # photos found by the tag's name.
    if approve:
        from_suggestions["accepted"] = sum(r.source == "model" for r in (await db.execute(text(
            "DELETE FROM tag_suggestions WHERE tag_id = :t AND photo_id = ANY(CAST(:a AS bigint[])) "
            "RETURNING photo_id, source"), p)).all())
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
        from_suggestions["rejected"] = sum(r.source == "model" for r in (await db.execute(text(
            "DELETE FROM tag_suggestions WHERE tag_id = :t AND photo_id = ANY(CAST(:r AS bigint[])) "
            "RETURNING photo_id, source"), p)).all())
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
            f"Needs {tag_learning.MIN_POSITIVES} approved photos that an image model has indexed "
            f"to learn from; {positives} approved so far"))
    return result


@router.post("/tags/{tag_id}/find-by-name")
async def find_by_name(tag_id: int, db: DB) -> dict:
    """Search for a tag by its name, before it has enough approved photos to
    learn from."""
    await _tag(db, tag_id)
    r = await tag_learning.find_by_name(db, tag_id)
    if r.get("learning"):
        raise HTTPException(400, "This tag is already learning from your decisions")
    if not r["models"]:
        raise HTTPException(400, "No image model with a text tower is indexed. "
                                 "Index CLIP on Discover, or add a model's text tower under Models.")
    return r


@router.post("/tags/{tag_id}/check")
async def check_tag(
    tag_id: int,
    db: DB,
    state: Literal["suggested", "unverified"] = Body("suggested", embed=True),
    limit: int = Body(300, embed=True, ge=1, le=2000),
) -> dict:
    """Ask the local vision model about this tab's photos, as a background task."""
    from fernkam.task_manager import task_manager

    tag = await _tag(db, tag_id)
    st = await vision_check.status(db)
    if not st["reachable"]:
        raise HTTPException(400, f"No vision model server at {st['url']}. Is Ollama running?")
    if not st["model"]:
        raise HTTPException(400, "The server has no vision model. With Ollama: ollama pull qwen3-vl:8b")
    ids = await vision_check.photos_to_check(db, tag_id, state, limit, st["model"])
    if not ids:
        return {"task_id": None, "queued": 0, "message": "Everything here is already checked"}
    task_id = await task_manager.create_task(
        "vision_check", f"Asking {st['model']} about {len(ids)} photos for {tag.name}…")

    async def run() -> None:
        from fernkam.db.session import async_session_factory

        async def is_cancelled() -> bool:
            t = await task_manager.get_task(task_id)
            return bool(t and t.status == "cancelled")

        async def on_progress(done, total, yes, no):
            await task_manager.update_task(
                task_id, message=f"{st['model']} on {tag.name}: {done}/{total} ({yes} yes, {no} no)",
                progress={"done": done, "total": total})
        try:
            async with async_session_factory() as bdb:
                r = await vision_check.check_photos(bdb, tag_id, ids, on_progress, is_cancelled)
            await task_manager.update_task(
                task_id, status="completed",
                message=f"{r['model']} checked {r['checked']} photos for {tag.name}: "
                        f"{r['yes']} yes, {r['no']} no" + (f", {r['failed']} failed" if r["failed"] else ""))
        except Exception as exc:  # noqa: BLE001
            logger.exception("vision check failed")
            await task_manager.update_task(task_id, status="failed", message=str(exc)[:500])

    asyncio.create_task(run(), name=f"fernkam-vision-{task_id}")
    return {"task_id": task_id, "queued": len(ids), "message": "running"}


# ── species (GBIF range priors) ──────────────────────────────────────────────

@router.get("/species-search")
async def species_search(q: str = Query(..., min_length=2)) -> list[dict]:
    """GBIF taxa for a common or scientific name."""
    import httpx
    try:
        return await species_range.search(q)
    except httpx.HTTPError as exc:
        raise HTTPException(502, f"Could not reach GBIF ({exc.__class__.__name__}). Is the internet reachable?")


async def _start_range_fetch(tag_id: int, name: str) -> str:
    from fernkam.task_manager import task_manager
    task_id = await task_manager.create_task("species_range", f"Fetching GBIF range data for {name}…")

    async def run() -> None:
        from fernkam.db.session import async_session_factory

        async def progress(done: int, total: int) -> None:
            await task_manager.update_task(task_id, message=f"GBIF range for {name}: {done}/{total} places",
                                           progress={"done": done, "total": total})
        try:
            async with async_session_factory() as bdb:
                r = await species_range.fetch_range(bdb, tag_id, progress)
                msg = f"GBIF range for {name}: {r['cells']} places ready"
                if (await bdb.execute(text("SELECT 1 FROM tag_models WHERE tag_id = :t"), {"t": tag_id})).first():
                    await task_manager.update_task(task_id, message=f"{msg}; relearning the tag…")
                    await tag_learning.train_tag(bdb, tag_id)
                    msg += "; tag relearned with it"
                await task_manager.update_task(task_id, status="completed", message=msg)
        except Exception as exc:  # noqa: BLE001
            logger.exception("GBIF range fetch failed")
            await task_manager.update_task(task_id, status="failed", message=f"GBIF: {str(exc)[:400]}")

    asyncio.create_task(run(), name=f"fernkam-range-{tag_id}")
    return task_id


@router.post("/tags/{tag_id}/species")
async def link_species(
    tag_id: int,
    db: DB,
    taxon_key: int = Body(...),
    scientific_name: str = Body(...),
    class_key: int = Body(...),
    common_name: str | None = Body(None),
    rank: str | None = Body(None),
    class_name: str | None = Body(None),
) -> dict:
    """Link a tag to a GBIF taxon and fetch its range around the library's
    places (then relearn the tag if it is learning)."""
    await _tag(db, tag_id)
    if taxon_key == class_key:
        raise HTTPException(400, "Link a species, genus or family, not a whole class")
    await db.execute(text("""
        INSERT INTO tag_species (tag_id, taxon_key, scientific_name, common_name, rank, class_key, class_name)
        VALUES (:t, :k, :sn, :cn, :r, :ck, :cl)
        ON CONFLICT (tag_id) DO UPDATE SET taxon_key = EXCLUDED.taxon_key,
            scientific_name = EXCLUDED.scientific_name, common_name = EXCLUDED.common_name,
            rank = EXCLUDED.rank, class_key = EXCLUDED.class_key, class_name = EXCLUDED.class_name,
            linked_at = now(), range_fetched_at = NULL
    """), {"t": tag_id, "k": taxon_key, "sn": scientific_name, "cn": common_name, "r": rank,
           "ck": class_key, "cl": class_name})
    await db.commit()
    return {"task_id": await _start_range_fetch(tag_id, common_name or scientific_name)}


@router.post("/tags/{tag_id}/species/refresh")
async def refresh_species(tag_id: int, db: DB) -> dict:
    """Fetch range data for places added since the last fetch."""
    row = (await db.execute(text(
        "SELECT scientific_name, common_name FROM tag_species WHERE tag_id = :t"), {"t": tag_id})).first()
    if not row:
        raise HTTPException(400, "This tag is not linked to a species")
    return {"task_id": await _start_range_fetch(tag_id, row.common_name or row.scientific_name)}


@router.delete("/tags/{tag_id}/species")
async def unlink_species(tag_id: int, db: DB) -> dict:
    await db.execute(text("DELETE FROM tag_species WHERE tag_id = :t"), {"t": tag_id})
    await db.commit()
    return {"unlinked": tag_id}


# ── models ───────────────────────────────────────────────────────────────────

@router.get("/models")
async def models(db: DB) -> dict:
    """The image models (CLIP and the extra ones) and the vision model."""
    import shutil
    from fernkam import clip_embed
    try:
        import onnxruntime as ort
        gpu = "CUDAExecutionProvider" in ort.get_available_providers()
    except Exception:  # noqa: BLE001
        gpu = False
    counts = await embed_index.indexed_counts(db)
    total = (await db.execute(text(
        "SELECT count(*) FROM photos WHERE status = 1 AND media_type IN ('image', 'video')"))).scalar_one()
    out = [{"key": "clip", "label": "CLIP", "purpose": "The search index behind Discover. Fast, general.",
            "installed": clip_embed.models_downloaded(), "text_installed": clip_embed.models_downloaded(),
            "indexed": counts["clip"], "source": "builtin"}]
    for m in embed_models.MODELS.values():
        out.append({
            "key": m.key, "label": m.label, "purpose": m.purpose, "dim": m.dim, "source": m.source,
            "size_mb": m.size_mb, "text_size_mb": m.text_size_mb, "gpu_recommended": m.gpu_recommended,
            "recommended_24gb": m.recommended_24gb,
            "installed": embed_models.installed(m.key), "text_installed": embed_models.text_installed(m.key),
            "indexed": counts[m.key],
            "export_command": embed_models.export_command(m.key) if m.source == "export" else None,
        })
    return {"models": out, "photos": total, "gpu": gpu, "uv_available": shutil.which("uv") is not None,
            "vision": {**await vision_check.status(db), "agreement": await vision_check.agreement(db)}}


@router.post("/models/{key}/install")
async def install_model(key: str) -> dict:
    """Download (or build) a model if needed and index the library with it."""
    import shutil
    if key not in embed_models.MODELS:
        raise HTTPException(404, "Unknown model")
    m = embed_models.MODELS[key]
    if m.source == "export" and not embed_models.installed(key) and not shutil.which("uv"):
        raise HTTPException(400, f"Build it once from a terminal in backend/: {embed_models.export_command(key)}")
    return {"task_id": await embed_index.start_install(key)}


@router.post("/models/{key}/text")
async def install_text_tower(key: str) -> dict:
    """Download a model's text tower, for finding photos by a tag's name."""
    from fernkam.task_manager import task_manager
    if key not in embed_models.MODELS:
        raise HTTPException(404, "Unknown model")
    m = embed_models.MODELS[key]
    if m.source != "download":
        raise HTTPException(400, f"{m.label}'s text tower comes with its build")
    task_id = await task_manager.create_task("model_install", f"Downloading {m.label} text tower…")

    async def run() -> None:
        loop = asyncio.get_running_loop()
        try:
            await loop.run_in_executor(None, lambda: embed_models.download(
                key, text=True, progress=embed_index._threadsafe_progress(task_id, loop)))
            await task_manager.update_task(task_id, status="completed",
                                           message=f"{m.label} can now find photos by name")
        except Exception as exc:  # noqa: BLE001
            logger.exception("text tower download failed")
            await task_manager.update_task(task_id, status="failed", message=str(exc)[:500])

    asyncio.create_task(run(), name=f"fernkam-text-{key}")
    return {"task_id": task_id}


@router.delete("/models/{key}")
async def remove_model(key: str, db: DB) -> dict:
    if key not in embed_models.MODELS:
        raise HTTPException(404, "Unknown model")
    await embed_index.remove(db, key)
    return {"removed": key}


@router.post("/vision")
async def set_vision(
    db: DB,
    model: str | None = Body(None, embed=True),
    url: str | None = Body(None, embed=True),
) -> dict:
    """Pick the vision model (and optionally the server)."""
    from fernkam.db.app_settings import set_setting
    if url is not None:
        await set_setting(db, "vision_url", url.strip())
    if model is not None:
        await set_setting(db, "vision_model", model.strip())
    return await vision_check.status(db)


@router.post("/train-all")
async def train_all(db: DB, check: bool = Body(False, embed=True)) -> dict:
    """Relearn every tag with enough approved photos, as a background task.
    Also picks up newly imported photos for suggestions. With check=true, the
    vision model then double-checks each tag's best new suggestions."""
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
                        linked = (await bdb.execute(text(
                            "SELECT 1 FROM tag_species WHERE tag_id = :t"), {"t": tid})).first()
                        if linked:   # places added since the last fetch
                            try:
                                await species_range.fetch_range(bdb, tid)
                            except Exception:  # noqa: BLE001 — learn without fresh range data
                                logger.warning("GBIF range update for tag %s failed", tid, exc_info=True)
                                await bdb.rollback()
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
            if check:
                msg += await _check_after_learning(task_id, tag_ids)
            await task_manager.update_task(task_id, status="completed", message=msg,
                                           progress={"done": done, "total": len(tag_ids)})
        except Exception as exc:  # noqa: BLE001
            logger.exception("tag learning task failed")
            await task_manager.update_task(task_id, status="failed", message=str(exc))

    asyncio.create_task(_run(), name=f"fernkam-tag-learning-{task_id}")
    return {"task_id": task_id, "tags": len(tag_ids), "message": "running"}


CHECK_PER_TAG = 30


async def _check_after_learning(task_id: str, tag_ids: list[int]) -> str:
    """Learn all → vision check: each tag's best unchecked suggestions."""
    from fernkam.db.session import async_session_factory
    from fernkam.task_manager import task_manager

    async with async_session_factory() as bdb:
        st = await vision_check.status(bdb)
        if not st["reachable"] or not st["model"]:
            return "; vision check skipped (no vision model reachable)"
        checked = 0
        for i, tid in enumerate(tag_ids):
            t = await task_manager.get_task(task_id)
            if t and t.status == "cancelled":
                break
            ids = await vision_check.photos_to_check(bdb, tid, "suggested", CHECK_PER_TAG, st["model"])
            if not ids:
                continue
            await task_manager.update_task(
                task_id, message=f"Double-checking with {st['model']}… tag {i + 1}/{len(tag_ids)}")
            try:
                checked += (await vision_check.check_photos(bdb, tid, ids))["checked"]
            except Exception:  # noqa: BLE001
                logger.exception("vision check after learning failed")
                return f"; vision check stopped after {checked} photos (see Logs)"
    return f"; {st['model']} double-checked {checked} suggestions"
