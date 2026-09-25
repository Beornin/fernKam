"""Where each image model's vectors live, and indexing photos with a model.

A "space" is one image model's vectors. CLIP's live in photos.embedding_v (it
predates the rest and powers Discover). Every other model's live in
photo_embeddings, one row per photo and model, with a partial HNSW index per
model on v::vector(dim), because the column itself has no fixed length.

Model keys come from embed_models.MODELS, never from a request, so they are
written into SQL as literals: a partial index is only used when the query's
WHERE matches its predicate literally.
"""
from __future__ import annotations

import asyncio
import logging
import shutil
import time
from dataclasses import dataclass
from typing import Optional

import numpy as np
from sqlalchemy import text

from fernkam import embed_models as em

logger = logging.getLogger(__name__)

_BATCH = 32


@dataclass(frozen=True)
class Space:
    key: str
    dim: int
    label: str

    @property
    def is_clip(self) -> bool:
        return self.key == "clip"

    def vector_sql(self) -> str:
        """(photo_id, vector text) for :ids."""
        if self.is_clip:
            return "SELECT id, embedding_v::text FROM photos WHERE id = ANY(:ids) AND embedding_v IS NOT NULL"
        return (f"SELECT photo_id, v::text FROM photo_embeddings "
                f"WHERE model = '{self.key}' AND photo_id = ANY(:ids)")

    def nearest_sql(self, where: str) -> str:
        """(photo_id, inner product with :w), best first; `where` filters photos p."""
        if self.is_clip:
            return f"""
                SELECT p.id, -(p.embedding_v <#> CAST(:w AS vector)) AS ip
                FROM photos p
                WHERE p.status = 1 AND p.embedding_v IS NOT NULL AND {where}
                ORDER BY p.embedding_v <=> CAST(:w AS vector)
                LIMIT :k"""
        v, d = f"e.v::vector({self.dim})", f"vector({self.dim})"
        return f"""
            SELECT e.photo_id, -({v} <#> CAST(:w AS {d})) AS ip
            FROM photo_embeddings e JOIN photos p ON p.id = e.photo_id
            WHERE e.model = '{self.key}' AND p.status = 1 AND {where}
            ORDER BY {v} <=> CAST(:w AS {d})
            LIMIT :k"""


CLIP = Space("clip", 512, "CLIP")


def space(key: str) -> Space:
    if key == "clip":
        return CLIP
    m = em.MODELS[key]
    return Space(key, m.dim, m.label)


async def indexed_counts(db) -> dict[str, int]:
    """Photos (in the library) with a vector, per space."""
    counts = {"clip": int((await db.execute(text(
        "SELECT count(*) FROM photos WHERE status = 1 AND embedding_v IS NOT NULL"))).scalar_one())}
    for key in em.MODELS:
        counts[key] = int((await db.execute(text(
            f"SELECT count(*) FROM photo_embeddings e JOIN photos p ON p.id = e.photo_id "
            f"WHERE e.model = '{key}' AND p.status = 1"))).scalar_one())
    return counts


async def active_spaces(db) -> list[Space]:
    """Spaces that have any vectors: what tag learning can use."""
    out = []
    if (await db.execute(text("SELECT EXISTS (SELECT 1 FROM photos WHERE embedding_v IS NOT NULL)"))).scalar():
        out.append(CLIP)
    for key in em.MODELS:
        if (await db.execute(text(
                f"SELECT EXISTS (SELECT 1 FROM photo_embeddings WHERE model = '{key}')"))).scalar():
            out.append(space(key))
    return out


def parse_vec(s: str) -> np.ndarray:
    return np.array(s[1:-1].split(","), dtype=np.float32)


def pgvec(v: np.ndarray) -> str:
    return "[" + ",".join(f"{x:.7g}" for x in np.asarray(v, dtype=np.float32)) + "]"


async def fetch_vectors(db, sp: Space, ids: list[int]) -> dict[int, np.ndarray]:
    out: dict[int, np.ndarray] = {}
    for start in range(0, len(ids), 5000):
        rows = (await db.execute(text(sp.vector_sql()), {"ids": ids[start:start + 5000]})).all()
        out.update((r[0], parse_vec(r[1])) for r in rows)
    return out


async def ensure_hnsw(db, key: str) -> None:
    """Build the model's partial HNSW index once it has vectors."""
    dim = em.MODELS[key].dim
    exists = (await db.execute(text(
        "SELECT 1 FROM pg_indexes WHERE indexname = :n"), {"n": f"ix_photo_embeddings_hnsw_{key}"})).first()
    if exists:
        return
    await db.execute(text("SET LOCAL maintenance_work_mem = '1GB'"))
    await db.execute(text(
        f"CREATE INDEX IF NOT EXISTS ix_photo_embeddings_hnsw_{key} ON photo_embeddings "
        f"USING hnsw ((v::vector({dim})) vector_cosine_ops) WHERE model = '{key}'"))
    await db.commit()
    # Fresh statistics, or the planner still thinks the table is empty and
    # sorts every vector instead of using the index.
    await db.execute(text("ANALYZE photo_embeddings"))
    await db.commit()


# ── indexing ─────────────────────────────────────────────────────────────────

async def embed_photo_rows(bdb, key: str, rows, on_progress=None, is_cancelled=None) -> tuple[int, int]:
    """Embed (id, album_path, filename) rows with a model into photo_embeddings.
    Returns (embedded, unreadable). Commits per batch."""
    from fernkam.api.routers.semantic import load_photo_image

    loop = asyncio.get_running_loop()
    size = em.MODELS[key].thumb
    ok = skipped = 0
    for start in range(0, len(rows), _BATCH):
        if is_cancelled and await is_cancelled():
            break
        chunk = rows[start:start + _BATCH]
        imgs = await loop.run_in_executor(None, lambda c=chunk: [load_photo_image(*r, size) for r in c])
        idx = [i for i, im in enumerate(imgs) if im is not None]
        skipped += len(chunk) - len(idx)
        if idx:
            vecs = await loop.run_in_executor(None, em.embed_images, key, [imgs[i] for i in idx])
            params = [{"pid": chunk[i][0], "v": pgvec(v)} for i, v in zip(idx, vecs) if v is not None]
            if params:
                await bdb.execute(text(f"""
                    INSERT INTO photo_embeddings (photo_id, model, v)
                    VALUES (:pid, '{key}', CAST(:v AS vector))
                    ON CONFLICT (photo_id, model) DO UPDATE SET v = EXCLUDED.v, created_at = now()
                """), params)
                await bdb.commit()
                ok += len(params)
        if on_progress:
            await on_progress(min(start + _BATCH, len(rows)), ok, skipped)
    return ok, skipped


async def missing_rows(db, key: str, ids: Optional[list[int]] = None) -> list[tuple]:
    only = "AND p.id = ANY(:ids)" if ids is not None else ""
    return [tuple(r) for r in (await db.execute(text(f"""
        SELECT p.id, p.album_path, p.filename FROM photos p
        WHERE p.status = 1 AND p.media_type IN ('image', 'video') {only}
          AND NOT EXISTS (SELECT 1 FROM photo_embeddings e
                          WHERE e.photo_id = p.id AND e.model = '{key}')
        ORDER BY p.id
    """), {"ids": ids} if ids is not None else {})).all()]


async def refresh_photos(bdb, ids: list[int]) -> int:
    """After a scan: embed new and re-edited photos with every model the user
    has indexed. Never downloads anything."""
    n = 0
    for key in em.MODELS:
        if not em.installed(key):
            continue
        has_any = (await bdb.execute(text(
            f"SELECT EXISTS (SELECT 1 FROM photo_embeddings WHERE model = '{key}')"))).scalar()
        if not has_any:
            continue
        rows = await missing_rows(bdb, key, ids)
        if rows:
            done, _ = await embed_photo_rows(bdb, key, rows)
            n += done
            em.release(key)
    return n


def _threadsafe_progress(task_id: str, loop, prefix: str = ""):
    """A progress callback usable from a worker thread, at most once a second."""
    from fernkam.task_manager import task_manager
    last = [0.0]

    def cb(msg: str) -> None:
        if time.time() - last[0] < 1.0:
            return
        last[0] = time.time()
        asyncio.run_coroutine_threadsafe(task_manager.update_task(task_id, message=prefix + msg), loop)
    return cb


async def _export(task_id: str, key: str, is_cancelled) -> None:
    """Build an export-only model by running the export in uv's throwaway
    environment (PyTorch is never installed into fernKam's own)."""
    from fernkam.config import BACKEND_DIR
    from fernkam.task_manager import task_manager

    uv = shutil.which("uv")
    if not uv:
        raise RuntimeError(f"uv is not on PATH. Build it from a terminal: {em.export_command(key)}")
    proc = await asyncio.create_subprocess_exec(
        uv, "run", "--with", "open-clip-torch", "fernkam", "export-model", key,
        cwd=str(BACKEND_DIR), stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT)
    tail: list[str] = []
    assert proc.stdout is not None
    while line := await proc.stdout.readline():
        msg = line.decode(errors="replace").strip()
        if msg:
            tail = (tail + [msg])[-5:]
            await task_manager.update_task(task_id, message=f"Building {em.MODELS[key].label}: {msg[:160]}")
        if await is_cancelled():
            proc.kill()
            raise asyncio.CancelledError
    if await proc.wait() != 0 or not em.installed(key):
        raise RuntimeError("export failed: " + " | ".join(tail))


async def start_install(key: str, index: bool = True) -> str:
    """Install (download or build) a model if needed, then index the library
    with it, as one background task."""
    from fernkam.task_manager import task_manager

    info = em.MODELS[key]
    task_id = await task_manager.create_task("model_install", f"Preparing {info.label}…")

    async def is_cancelled() -> bool:
        t = await task_manager.get_task(task_id)
        return bool(t and t.status == "cancelled")

    async def run() -> None:
        from fernkam.db.session import async_session_factory

        loop = asyncio.get_running_loop()
        try:
            if not em.installed(key):
                if info.source == "download":
                    await loop.run_in_executor(
                        None, lambda: em.download(key, progress=_threadsafe_progress(task_id, loop)))
                else:
                    await _export(task_id, key, is_cancelled)
            if not index:
                await task_manager.update_task(task_id, status="completed", message=f"{info.label} installed")
                return
            async with async_session_factory() as bdb:
                rows = await missing_rows(bdb, key)
                total, t0 = len(rows), time.time()

                async def on_progress(done: int, ok: int, skipped: int) -> None:
                    rate = done / max(time.time() - t0, 0.001)
                    await task_manager.update_task(
                        task_id,
                        message=f"Indexing with {info.label}… {done:,}/{total:,} ({rate:.0f}/s, "
                                f"ETA {int((total - done) / max(rate, 0.001))}s)",
                        progress={"done": done, "total": total, "ok": ok, "skipped": skipped})

                ok, skipped = await embed_photo_rows(bdb, key, rows, on_progress, is_cancelled)
                if await is_cancelled():
                    return
                await task_manager.update_task(task_id, message=f"Building {info.label} search index…")
                await ensure_hnsw(bdb, key)
            em.release(key)   # give the GPU back, e.g. to a local vision model
            await task_manager.update_task(
                task_id, status="completed",
                message=f"{info.label}: indexed {ok:,} photos ({skipped} unreadable). "
                        f"Relearn tags to use it.",
                progress={"done": total, "total": total, "ok": ok, "skipped": skipped})
        except asyncio.CancelledError:
            pass
        except Exception as exc:  # noqa: BLE001
            logger.exception("installing %s failed", key)
            await task_manager.update_task(task_id, status="failed", message=str(exc)[:500])

    asyncio.create_task(run(), name=f"fernkam-model-{key}-{task_id}")
    return task_id


async def remove(db, key: str) -> None:
    await db.execute(text(f"DROP INDEX IF EXISTS ix_photo_embeddings_hnsw_{key}"))
    await db.execute(text(f"DELETE FROM photo_embeddings WHERE model = '{key}'"))
    await db.commit()
    em.remove(key)
