"""search_and_perf_indexes — Phase 0B foundation

Revision ID: 0011
Revises: 0010
Create Date: 2026-06-27

Adds the indexing + full-text-search groundwork the roadmap depends on:
  - btree indexes for the common sort/filter columns (taken_at, rating,
    media_type, color_label, camera_id, lens_id, imported_at)
  - a composite (taken_at DESC NULLS LAST, id DESC) index for keyset pagination
  - a GIN index on the exif JSONB for camera/lens/exposure queries
  - a STORED generated tsvector column (filename + title + caption) + GIN index
    for full-text search (tag-name search is OR'd in at query time via the
    existing trigram index on tags.name)
  - a btree index on sha256 for duplicate detection

All statements use IF NOT EXISTS so the migration is safe to re-run.
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op

revision: str = "0011"
down_revision: Union[str, None] = "0010"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ── Sort / filter btree indexes ───────────────────────────────────────
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_photos_taken_at "
        "ON photos (taken_at DESC NULLS LAST)"
    )
    # Composite for keyset (seek) pagination on the default sort.
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_photos_taken_at_id "
        "ON photos (taken_at DESC NULLS LAST, id DESC)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_photos_imported_at "
        "ON photos (imported_at DESC)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_photos_rating ON photos (rating) "
        "WHERE rating > 0"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_photos_color_label ON photos (color_label) "
        "WHERE color_label > 0"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_photos_media_type ON photos (media_type)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_photos_camera_id ON photos (camera_id) "
        "WHERE camera_id IS NOT NULL"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_photos_lens_id ON photos (lens_id) "
        "WHERE lens_id IS NOT NULL"
    )
    # Partial index to find photos missing a date (date-inference workflow).
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_photos_no_date ON photos (id) "
        "WHERE taken_at IS NULL AND status = 1"
    )
    # GPS-present filter / map.
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_photos_has_gps ON photos (id) "
        "WHERE latitude IS NOT NULL AND longitude IS NOT NULL"
    )

    # ── EXIF JSONB GIN index ──────────────────────────────────────────────
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_photos_exif_gin "
        "ON photos USING gin (exif jsonb_path_ops)"
    )

    # ── Duplicate detection ───────────────────────────────────────────────
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_photos_sha256 ON photos (sha256) "
        "WHERE sha256 IS NOT NULL"
    )

    # ── Full-text search: STORED generated tsvector + GIN ─────────────────
    # to_tsvector('english', ...) with a constant regconfig is IMMUTABLE, so it
    # is valid in a generated column. Weighted: filename(A) title(B) caption(C).
    op.execute(
        """
        ALTER TABLE photos
        ADD COLUMN IF NOT EXISTS search_tsv tsvector
        GENERATED ALWAYS AS (
            setweight(to_tsvector('english', coalesce(filename, '')), 'A') ||
            setweight(to_tsvector('english', coalesce(title, '')), 'B') ||
            setweight(to_tsvector('english', coalesce(caption, '')), 'C')
        ) STORED
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_photos_search_tsv "
        "ON photos USING gin (search_tsv)"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_photos_search_tsv")
    op.execute("ALTER TABLE photos DROP COLUMN IF EXISTS search_tsv")
    op.execute("DROP INDEX IF EXISTS ix_photos_sha256")
    op.execute("DROP INDEX IF EXISTS ix_photos_exif_gin")
    op.execute("DROP INDEX IF EXISTS ix_photos_has_gps")
    op.execute("DROP INDEX IF EXISTS ix_photos_no_date")
    op.execute("DROP INDEX IF EXISTS ix_photos_lens_id")
    op.execute("DROP INDEX IF EXISTS ix_photos_camera_id")
    op.execute("DROP INDEX IF EXISTS ix_photos_media_type")
    op.execute("DROP INDEX IF EXISTS ix_photos_color_label")
    op.execute("DROP INDEX IF EXISTS ix_photos_rating")
    op.execute("DROP INDEX IF EXISTS ix_photos_imported_at")
    op.execute("DROP INDEX IF EXISTS ix_photos_taken_at_id")
    op.execute("DROP INDEX IF EXISTS ix_photos_taken_at")
