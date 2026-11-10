"""Indexes the 2026-11 audit measured as missing, and one it found unused.

Measured on the 122k-photo library (rolled-back trial, then these):
- tags had no index on path or parent_id: every tag lookup by path (each file read, every tag
  filter) and every children lookup was a full scan; 1.1 million scans so far. 3 ms -> 0.03 ms.
  A unique path also makes a second tag with the same path impossible.
- Sorting the whole library by import date, filename or rating scanned and sorted every row
  (83-141 ms); a partial index on live photos answers the first page in 0.04 ms.
- The album tree and the live-photo count read every photo (114 ms); an index on live albums
  answers them from the index alone (20 ms).
- The Maintenance status count of never-synced photos (70 ms -> 6 ms).
- ix_faces_emb_unconfirmed_hnsw (48 MB) has never been used: the sweeps ask for
  status IN ('unconfirmed', 'suggested'), which a partial index on 'unconfirmed' alone can't serve.

Revision ID: 0039
Revises: 0038
"""
from alembic import op

revision = "0039"
down_revision = "0038"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        DO $$ BEGIN
            IF NOT EXISTS (SELECT path FROM tags GROUP BY path HAVING count(*) > 1) THEN
                CREATE UNIQUE INDEX IF NOT EXISTS uq_tags_path ON tags (path);
            ELSE
                RAISE NOTICE 'two tags share a path: merge them, then restart to add uq_tags_path';
            END IF;
        END $$
    """)
    op.execute("CREATE INDEX IF NOT EXISTS ix_tags_path_gist ON tags USING gist (path)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_tags_parent_id ON tags (parent_id)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_photos_live_imported ON photos (imported_at DESC NULLS LAST, id DESC) WHERE status = 1")
    op.execute("CREATE INDEX IF NOT EXISTS ix_photos_live_filename ON photos (filename, id) WHERE status = 1")
    op.execute("CREATE INDEX IF NOT EXISTS ix_photos_live_rating ON photos (rating DESC, id DESC) WHERE status = 1")
    op.execute("CREATE INDEX IF NOT EXISTS ix_photos_live_album ON photos (album_path) WHERE status = 1")
    op.execute("CREATE INDEX IF NOT EXISTS ix_photos_never_synced ON photos (id) WHERE meta_synced_at IS NULL AND status = 1")
    op.execute("DROP INDEX IF EXISTS ix_faces_emb_unconfirmed_hnsw")
    op.execute("ANALYZE tags")
    op.execute("ANALYZE photos")


def downgrade() -> None:
    for ix in ("uq_tags_path", "ix_tags_path_gist", "ix_tags_parent_id", "ix_photos_live_imported",
               "ix_photos_live_filename", "ix_photos_live_rating", "ix_photos_live_album", "ix_photos_never_synced"):
        op.execute(f"DROP INDEX IF EXISTS {ix}")
    # ix_faces_emb_unconfirmed_hnsw is not recreated: it was never used.
