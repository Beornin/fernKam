"""Schema review: drop what is never written, drop unused indexes, add one missing.

Measured on the live catalogue (120,070 photos, index counters never reset):

Columns never written by any code path, empty on every row:
  photos.color_depth, photos.color_model   digiKam import only
  photos.country                           the geocoder fills country_code (36,129 rows)
  faces.file_synced_at                     declared, never read or written
  tags.icon, tags.color                    no UI sets them
  app_logs.context                         fed by log_context(), which nothing used
Always true by construction:
  photo_stacks.has_raw                     every stack is a RAW+JPG pair
Table: face_clusters_backup_pre_phase1     safety copy from the Phase 1 rebuild, never read

Indexes:
  ix_faces_best_match_score   3.7 MB, 2 scans     the status index serves those queries
  ix_faces_created_at         2.9 MB, 6 scans
  ix_person_centroids_ptid    duplicate of uq_person_centroids_ptid_label's first column
  ix_app_logs_msg_trgm        0 scans, table of ~140 rows
  ix_saved_searches_name      0 scans, 12 rows
Added:
  ix_photo_stacks_cover_photo  photo_stacks.cover_photo_id is a foreign key to photos
      (ON DELETE SET NULL) with no index, so every deleted photo scanned all stacks:
      28,051 full scans reading 54M rows.

Revision ID: 0033
Revises: 0032
"""
from alembic import op

revision = "0033"
down_revision = "0032"
branch_labels = None
depends_on = None

_COLUMNS = (
    ("photos", "color_depth", "SMALLINT"), ("photos", "color_model", "SMALLINT"), ("photos", "country", "TEXT"),
    ("faces", "file_synced_at", "TIMESTAMPTZ"), ("tags", "icon", "VARCHAR(255)"), ("tags", "color", "VARCHAR(16)"),
    ("app_logs", "context", "JSONB"), ("photo_stacks", "has_raw", "BOOLEAN DEFAULT false"),
)
_INDEXES = ("ix_faces_best_match_score", "ix_faces_created_at", "ix_person_centroids_ptid",
            "ix_app_logs_msg_trgm", "ix_saved_searches_name")


def upgrade() -> None:
    for table, column, _ in _COLUMNS:
        op.execute(f"ALTER TABLE {table} DROP COLUMN IF EXISTS {column}")
    op.execute("DROP TABLE IF EXISTS face_clusters_backup_pre_phase1")
    for name in _INDEXES:
        op.execute(f"DROP INDEX IF EXISTS {name}")
    op.execute("CREATE INDEX IF NOT EXISTS ix_photo_stacks_cover_photo ON photo_stacks (cover_photo_id)")


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_photo_stacks_cover_photo")
    op.execute("CREATE INDEX IF NOT EXISTS ix_faces_best_match_score ON faces (best_match_score)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_faces_created_at ON faces (created_at)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_person_centroids_ptid ON person_centroids (person_tag_id)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_saved_searches_name ON saved_searches (name)")
    for table, column, sql_type in _COLUMNS:
        op.execute(f"ALTER TABLE {table} ADD COLUMN IF NOT EXISTS {column} {sql_type}")
