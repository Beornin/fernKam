"""Idempotent startup setup: person_centroids table + det_score column on faces."""
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine


async def ensure_person_centroids(engine: AsyncEngine) -> None:
    """Create person_centroids table and HNSW index if they don't exist."""
    async with engine.begin() as conn:
        await conn.execute(text("""
            CREATE TABLE IF NOT EXISTS person_centroids (
                id            SERIAL PRIMARY KEY,
                person_tag_id INTEGER NOT NULL REFERENCES tags(id) ON DELETE CASCADE,
                label         SMALLINT NOT NULL DEFAULT 0,
                embedding_v   vector(512),
                face_count    INTEGER NOT NULL DEFAULT 0,
                built_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
                CONSTRAINT uq_person_centroids_ptid_label UNIQUE (person_tag_id, label)
            )
        """))
        await conn.execute(text("""
            CREATE INDEX IF NOT EXISTS ix_person_centroids_ptid
            ON person_centroids (person_tag_id)
        """))
        try:
            await conn.execute(text("""
                CREATE INDEX IF NOT EXISTS ix_person_centroids_emb_hnsw
                ON person_centroids USING hnsw (embedding_v vector_cosine_ops)
                WITH (m = 16, ef_construction = 64)
            """))
        except Exception:
            pass


async def ensure_det_score_column(engine: AsyncEngine) -> None:
    """Add det_score NUMERIC(5,4) to faces table if the column is absent."""
    async with engine.begin() as conn:
        await conn.execute(text("""
            ALTER TABLE faces ADD COLUMN IF NOT EXISTS det_score NUMERIC(5, 4)
        """))


async def drop_single_confirmed_per_photo_constraint(engine: AsyncEngine) -> None:
    """Drop the partial unique index that prevents TWINS persons appearing
    multiple times as confirmed in the same photo.
    Application code now enforces uniqueness for non-TWINS persons."""
    async with engine.begin() as conn:
        await conn.execute(text("""
            DROP INDEX IF EXISTS uq_faces_confirmed_per_photo_person
        """))
