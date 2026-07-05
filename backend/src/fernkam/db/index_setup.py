"""Idempotent startup index maintenance.

Adds missing B-tree / partial indexes and drops zero-use indexes that only add
write overhead. HNSW index creation is wrapped in try/except so a missing
pgvector install doesn't break startup.
"""
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine


async def ensure_indexes(engine: AsyncEngine) -> None:
    """Create missing indexes and drop dead ones. Safe to call on every startup."""

    async with engine.begin() as conn:
        # ── faces: B-tree on status (most-queried filter column, no index existed) ──
        await conn.execute(text("""
            CREATE INDEX IF NOT EXISTS ix_faces_status
            ON faces (status)
        """))

        # ── photos: partial index for the face-scan job queue ──
        await conn.execute(text("""
            CREATE INDEX IF NOT EXISTS ix_photos_unscanned
            ON photos (id)
            WHERE faces_scanned_at IS NULL
              AND status = 1
              AND media_type = 'image'
        """))

        # ── photos: partial index for sync-dirty flag ──
        await conn.execute(text("""
            CREATE INDEX IF NOT EXISTS ix_photos_sync_dirty
            ON photos (id)
            WHERE file_sync_dirty = TRUE
        """))

        # ── photos: text_pattern_ops for LIKE 'path%' album browsing ──
        await conn.execute(text("""
            CREATE INDEX IF NOT EXISTS ix_photos_album_path
            ON photos (album_path text_pattern_ops)
        """))

        # ── Drop only truly obsolete indexes (replaced by migration 0011) ──
        # NOTE: do NOT drop ix_photos_exif_gin, ix_photos_sha256, ix_photos_rating,
        # ix_photos_taken_at — those are created by migration 0011 and are actively used.
        await conn.execute(text("DROP INDEX IF EXISTS ix_photo_tags_tag_photo"))

    # ── Partial HNSW for unconfirmed faces (clustering inner loop) ──
    # Must be outside a transaction block; wrap separately and swallow errors.
    try:
        async with engine.connect() as conn:
            await conn.execution_options(isolation_level="AUTOCOMMIT").execute(text("""
                CREATE INDEX IF NOT EXISTS ix_faces_emb_unconfirmed_hnsw
                ON faces USING hnsw (embedding_v vector_cosine_ops)
                WHERE status = 'unconfirmed' AND embedding_v IS NOT NULL
            """))
    except Exception as exc:
        print(f"[index_setup] partial HNSW index skipped: {exc}", flush=True)
