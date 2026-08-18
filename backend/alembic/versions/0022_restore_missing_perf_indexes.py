"""restore_missing_perf_indexes — re-create indexes 0011 declared but that are
absent from the live DB, add a tag-first index for tag filtering, and drop a
byte-identical duplicate of the photo_tags primary key.

Audited against the live 124k-photo database with EXPLAIN ANALYZE; every change
here fixes a measured full sequential scan:

  * ix_photos_sha256 — MISSING despite being declared in 0011. Duplicate
    detection groups/filters on sha256; `GROUP BY sha256` was a 977 ms seq
    scan (22.5k buffers read) and the per-page `sha256 = ANY(...)` fetch was a
    64 ms parallel seq scan.
  * ix_photos_rating — MISSING despite being declared in 0011. `rating >= 4`
    was an 86 ms seq scan discarding 124,688 of 124,761 rows to return 73.
  * ix_photo_tags_tag_id — never existed. photo_tags' PK is (photo_id, tag_id),
    which cannot serve a tag_id-first lookup, so filtering photos by tag was a
    seq scan discarding 94,940 rows. Indexing (tag_id, photo_id) also makes it
    covering, so the tag filter becomes an index-only scan.
  * ix_photo_tags_photo_tag — DROPPED. It is (photo_id, tag_id) non-unique,
    exactly the same columns in the same order as pk_photo_tags, so it can
    never win a plan the PK wouldn't already serve. Pure write + disk overhead.

Deliberately NOT re-creating ix_photos_taken_at: ix_photos_taken_at_id
(taken_at DESC NULLS LAST, id DESC) already exists and has taken_at as its
leading column, so it serves taken_at-only queries too. A second index would
add write cost for no planning benefit.

Revision ID: 0022
Revises: 0021
Create Date: 2026-08-16
"""
from alembic import op

revision = "0022"
down_revision = "0021"
branch_labels = None
depends_on = None


def upgrade():
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_photos_sha256 ON photos (sha256) "
        "WHERE sha256 IS NOT NULL"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_photos_rating ON photos (rating) "
        "WHERE rating > 0"
    )
    # (tag_id, photo_id) — tag_id leading for the filter, photo_id included so
    # the common "photo ids carrying this tag" query is index-only.
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_photo_tags_tag_id ON photo_tags (tag_id, photo_id)"
    )
    # Redundant with pk_photo_tags (identical columns, identical order).
    op.execute("DROP INDEX IF EXISTS ix_photo_tags_photo_tag")


def downgrade():
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_photo_tags_photo_tag ON photo_tags (photo_id, tag_id)"
    )
    op.execute("DROP INDEX IF EXISTS ix_photo_tags_tag_id")
    op.execute("DROP INDEX IF EXISTS ix_photos_rating")
    op.execute("DROP INDEX IF EXISTS ix_photos_sha256")
