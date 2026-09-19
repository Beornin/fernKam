"""Drop dead tables and unused indexes (Roadmap phase 0.3 / 0.4).

Removed here, all verified empty or unread against the live catalogue:

  * `people` (0 rows) and `faces.person_id` (0 non-null). The people feature is
    implemented entirely on `tags.is_person` + `faces.person_tag_id`; the
    `people` table was never populated by any code path.
  * `audit_log` (0 rows). Groundwork for an undo feature that was never built
    and is explicitly out of scope. Re-add it if undo is ever wanted.
  * Five indexes reporting 0 scans in `pg_stat_user_indexes`, costing write
    time and disk for nothing. `ix_photos_exif_gin` alone is 25 MB.

`ix_photos_search_tsv`, the trgm indexes on photos/tags, and every HNSW face
index are all in active use and deliberately untouched.

Revision ID: 0023
Revises: 0022
"""
from alembic import op

revision = "0023"
down_revision = "0022"
branch_labels = None
depends_on = None


# 0-scan indexes. Their owning migrations are left intact; these drops sit at
# the head so a fresh database still creates then removes them, rather than
# rewriting history.
_UNUSED_INDEXES = [
    "ix_photos_exif_gin",        # 25 MB GIN over the exif JSONB
    "ix_photos_imported_at",     # 8.4 MB
    "ix_photos_city",            # 848 kB
    "ix_photos_country_code",    # 768 kB
    "ix_photos_duration_secs",   # 16 kB
]


def upgrade() -> None:
    for name in _UNUSED_INDEXES:
        op.execute(f"DROP INDEX IF EXISTS {name}")

    # faces.person_id is the only FK into `people`; drop it before the table.
    op.execute("ALTER TABLE faces DROP CONSTRAINT IF EXISTS faces_person_id_fkey")
    op.execute("ALTER TABLE faces DROP COLUMN IF EXISTS person_id")

    op.execute("DROP TABLE IF EXISTS audit_log")
    op.execute("DROP TABLE IF EXISTS people")


def downgrade() -> None:
    op.execute("""
        CREATE TABLE IF NOT EXISTS people (
            id      SERIAL PRIMARY KEY,
            tag_id  INTEGER UNIQUE REFERENCES tags(id),
            name    VARCHAR(255) NOT NULL,
            notes   TEXT
        )
    """)
    op.execute("""
        CREATE TABLE IF NOT EXISTS audit_log (
            id          BIGSERIAL PRIMARY KEY,
            table_name  VARCHAR(64) NOT NULL,
            row_id      VARCHAR(64),
            action      VARCHAR(16) NOT NULL,
            changed_by  VARCHAR(128),
            changed_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
            payload     JSONB
        )
    """)
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_audit_log_table_changed_at "
        "ON audit_log (table_name, changed_at)"
    )
    op.execute("ALTER TABLE faces ADD COLUMN IF NOT EXISTS person_id INTEGER REFERENCES people(id)")

    op.execute("CREATE INDEX IF NOT EXISTS ix_photos_exif_gin ON photos USING gin (exif) WHERE exif IS NOT NULL")
    op.execute("CREATE INDEX IF NOT EXISTS ix_photos_imported_at ON photos (imported_at)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_photos_city ON photos (city)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_photos_country_code ON photos (country_code)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_photos_duration_secs ON photos (duration_secs)")
