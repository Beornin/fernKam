"""Idempotent startup setup: face_clusters table.

Stores the result of the latest cluster-rebuild run: a mapping of
face_id -> cluster_id produced by the pgvector neighbor-graph + union-find
clustering in the faces router. Rebuilt on demand; safe to truncate.
"""
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine


async def ensure_face_clusters(engine: AsyncEngine) -> None:
    """Create face_clusters table and index if they don't exist."""
    async with engine.begin() as conn:
        await conn.execute(text("""
            CREATE TABLE IF NOT EXISTS face_clusters (
                face_id    UUID PRIMARY KEY REFERENCES faces(id) ON DELETE CASCADE,
                cluster_id INTEGER NOT NULL,
                built_at   TIMESTAMPTZ NOT NULL DEFAULT now()
            )
        """))
        await conn.execute(text("""
            CREATE INDEX IF NOT EXISTS ix_face_clusters_cluster_id
            ON face_clusters (cluster_id)
        """))
