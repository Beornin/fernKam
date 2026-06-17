"""Idempotent startup migration that wires up pgvector for face embeddings.

- Enables the `vector` extension.
- Adds `faces.embedding_v vector(512)` column.
- Backfills `embedding_v` from the legacy `embedding` bytea column in chunks.
- Creates an HNSW index for cosine similarity.

Designed to be safe to run on every backend start: each step uses IF NOT EXISTS
or checks current state, so it's a no-op once the schema is in place.
"""
from __future__ import annotations

import asyncio
import logging
import struct
from typing import Optional

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

logger = logging.getLogger(__name__)

_BACKFILL_BATCH = 1000


def _bytes_to_floats(data: bytes) -> Optional[list[float]]:
    if not data:
        return None
    if len(data) != 512 * 4:
        return None
    return list(struct.unpack(f"<{512}f", data))


def _floats_to_pgvector_literal(floats: list[float]) -> str:
    """Render a Python list[float] as a pgvector text literal: '[0.1,0.2,...]'."""
    return "[" + ",".join(f"{x:.6f}" for x in floats) + "]"


async def ensure_pgvector(engine: AsyncEngine) -> None:
    """Run the idempotent migration. Logs progress; never raises through unless
    the extension itself can't be created."""
    async with engine.begin() as conn:
        # 1) Extension
        try:
            await conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
        except Exception as e:
            logger.warning("[pgvector] CREATE EXTENSION failed: %s. Skipping setup.", e)
            return

        # 2) Column
        await conn.execute(text(
            "ALTER TABLE faces ADD COLUMN IF NOT EXISTS embedding_v vector(512)"
        ))

    # 3) Backfill in chunks (separate session per batch so we don't lock long).
    pending = await _count_pending_backfill(engine)
    if pending:
        logger.info("[pgvector] Backfilling %d face embeddings…", pending)
        await _backfill(engine, pending)
    else:
        logger.info("[pgvector] No backfill needed")

    # 4) HNSW indexes for cosine distance (safe to issue every startup).
    async with engine.begin() as conn:
        # Full index used for general nearest-neighbor lookups.
        await conn.execute(text(
            "CREATE INDEX IF NOT EXISTS ix_faces_embedding_v_hnsw "
            "ON faces USING hnsw (embedding_v vector_cosine_ops) "
            "WHERE embedding_v IS NOT NULL"
        ))
        # Partial index for confirmed pool — makes auto-confirm sweep ~10-100x faster
        # by eliminating the post-filter on status = 'confirmed'.
        await conn.execute(text(
            "CREATE INDEX IF NOT EXISTS ix_faces_embedding_v_confirmed_hnsw "
            "ON faces USING hnsw (embedding_v vector_cosine_ops) "
            "WHERE status = 'confirmed' AND person_tag_id IS NOT NULL AND embedding_v IS NOT NULL"
        ))
        # Partial index for ignored pool.
        await conn.execute(text(
            "CREATE INDEX IF NOT EXISTS ix_faces_embedding_v_ignored_hnsw "
            "ON faces USING hnsw (embedding_v vector_cosine_ops) "
            "WHERE status = 'ignored' AND embedding_v IS NOT NULL"
        ))
    logger.info("[pgvector] Setup complete")


async def _count_pending_backfill(engine: AsyncEngine) -> int:
    async with engine.connect() as conn:
        n = await conn.scalar(text(
            "SELECT count(*) FROM faces WHERE embedding IS NOT NULL AND embedding_v IS NULL"
        ))
    return int(n or 0)


async def _backfill(engine: AsyncEngine, total: int) -> None:
    done = 0
    while True:
        async with engine.connect() as conn:
            rows = (await conn.execute(text(
                "SELECT id, embedding FROM faces "
                "WHERE embedding IS NOT NULL AND embedding_v IS NULL "
                "LIMIT :lim"
            ), {"lim": _BACKFILL_BATCH})).fetchall()
        if not rows:
            break

        # Build (id, vector_literal) tuples client-side.
        updates = []
        for fid, emb_bytes in rows:
            floats = _bytes_to_floats(bytes(emb_bytes))
            if floats is None:
                continue
            updates.append({"id": fid, "v": _floats_to_pgvector_literal(floats)})
        if not updates:
            # If all rows had bad bytes, mark them processed by setting an empty vector?
            # Better: skip and bail to prevent loop.
            break

        async with engine.begin() as conn:
            await conn.execute(text(
                "UPDATE faces SET embedding_v = (:v)::vector WHERE id = :id"
            ), updates)
        done += len(updates)
        if total:
            logger.info("[pgvector] Backfill %d/%d", done, total)
        # Yield to event loop occasionally
        await asyncio.sleep(0)
