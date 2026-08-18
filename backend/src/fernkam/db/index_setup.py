"""Idempotent startup index maintenance and per-database planner tuning.

Adds missing B-tree / partial indexes and drops zero-use indexes that only add
write overhead. HNSW index creation is wrapped in try/except so a missing
pgvector install doesn't break startup.
"""
import logging
import re

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

logger = logging.getLogger(__name__)

# Postgres does not accept bind parameters in `ALTER DATABASE ... SET`, so the
# value has to be interpolated. These settings come from .env, so constrain
# them to a plain number with an optional size/time unit before interpolating.
_PG_SETTING_VALUE = re.compile(r"^\d+(\.\d+)?\s*(kB|MB|GB|TB|ms|s|min)?$", re.IGNORECASE)


async def ensure_database_tuning(engine: AsyncEngine) -> None:
    """Apply fernKam's planner/memory settings to its OWN database.

    Uses ALTER DATABASE, so this is scoped to fernKam's database and never
    touches other databases on the cluster or the server-wide config. Takes
    effect for new connections; no restart required. Reversible at any time
    with `ALTER DATABASE <db> RESET <setting>`.

    Best-effort: needs privileges to ALTER DATABASE, so it's wrapped and only
    warns on failure rather than blocking startup.
    """
    from fernkam.config import get_settings
    settings = get_settings()

    wanted = {
        "random_page_cost": settings.pg_random_page_cost,
        "work_mem": settings.pg_work_mem,
        "effective_cache_size": settings.pg_effective_cache_size,
    }
    wanted = {k: v for k, v in wanted.items() if v and str(v).strip()}
    if not wanted:
        return

    try:
        async with engine.connect() as conn:
            ac = await conn.execution_options(isolation_level="AUTOCOMMIT")
            dbname = (await ac.execute(text("SELECT current_database()"))).scalar_one()
            # Identifier, not a bindable parameter — quote it defensively even
            # though it comes from the server itself.
            ident = '"' + str(dbname).replace('"', '""') + '"'
            for key, value in wanted.items():
                value = str(value).strip()
                if not _PG_SETTING_VALUE.match(value):
                    logger.warning(
                        "[db_tuning] ignoring %s=%r — not a plain number with an "
                        "optional unit (e.g. '1.1', '32MB')", key, value
                    )
                    continue
                current = (await ac.execute(text(f"SHOW {key}"))).scalar_one()
                if str(current).strip().lower() == value.lower():
                    continue  # already correct — skip the catalog write
                await ac.execute(text(f"ALTER DATABASE {ident} SET {key} = '{value}'"))
                logger.info("[db_tuning] %s: %s -> %s", key, current, value)
    except Exception as exc:
        logger.warning(
            "[db_tuning] could not apply per-database settings (needs ALTER DATABASE "
            "privileges); continuing with server defaults: %s", exc
        )


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

        # ── Self-heal indexes that migration 0011 declares but that were found
        # missing from a live database (0011 is recorded as applied, yet these
        # three did not exist — the other 0011 indexes did). Without them,
        # duplicate detection and rating filters degrade to full sequential
        # scans over ~125k rows. Migration 0022 also creates them; these are
        # kept here as an idempotent safety net since they demonstrably went
        # missing once already.
        await conn.execute(text("""
            CREATE INDEX IF NOT EXISTS ix_photos_sha256
            ON photos (sha256) WHERE sha256 IS NOT NULL
        """))
        await conn.execute(text("""
            CREATE INDEX IF NOT EXISTS ix_photos_rating
            ON photos (rating) WHERE rating > 0
        """))
        # photo_tags' PK is (photo_id, tag_id) and cannot serve a tag_id-first
        # lookup, so filtering photos by tag seq-scans without this. Ordering
        # (tag_id, photo_id) also makes that query index-only.
        await conn.execute(text("""
            CREATE INDEX IF NOT EXISTS ix_photo_tags_tag_id
            ON photo_tags (tag_id, photo_id)
        """))

        # ── Drop genuinely redundant indexes ──
        # ix_photo_tags_photo_tag is (photo_id, tag_id) — the same columns in
        # the same order as pk_photo_tags, so it can never win a plan the PK
        # wouldn't already serve.
        # (An earlier version of this file dropped ix_photo_tags_tag_photo
        # instead, which was the tag-first index actually worth keeping; that
        # role is now filled by ix_photo_tags_tag_id above.)
        await conn.execute(text("DROP INDEX IF EXISTS ix_photo_tags_photo_tag"))

    # ── Partial HNSW for unconfirmed faces (clustering inner loop) ──
    # Must be outside a transaction block; wrap separately and swallow errors.
    try:
        async with engine.connect() as conn:
            ac = await conn.execution_options(isolation_level="AUTOCOMMIT")
            await ac.execute(text("""
                CREATE INDEX IF NOT EXISTS ix_faces_emb_unconfirmed_hnsw
                ON faces USING hnsw (embedding_v vector_cosine_ops)
                WHERE status = 'unconfirmed' AND embedding_v IS NOT NULL
            """))
    except Exception as exc:
        print(f"[index_setup] partial HNSW index skipped: {exc}", flush=True)
