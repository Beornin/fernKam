"""Drop photo_thumbnails; thumbnails are a disk cache (Roadmap phase 0.2).

`photo_thumbnails` held 431,495 rows / 32 GB of a 34 GB database — 94% of the
catalogue was a regenerable cache, and its TOAST segment alone was 31 GB.

Read latency was never the problem (Postgres served these in ~0.5-0.8 ms). The
cost was connection pressure: every tile held one of the 50 pooled connections,
so changing the grid's thumbnail size fired ~42 concurrent reads against the
same pool the catalogue query needed. Measured on this library, that query went
0.67 ms idle -> 19.19 ms under the burst, and 2.55 ms once thumbnails came from
disk instead.

Bytes are copied out to the `thumb_cache_dir` layout defined by
`thumb_cache_path()` BEFORE this migration runs, so the drop is not lossy. Even
if it were, a missing thumbnail is regenerated on first request.

Revision ID: 0024
Revises: 0023
"""
from alembic import op

revision = "0024"
down_revision = "0023"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("DROP TABLE IF EXISTS photo_thumbnails")


def downgrade() -> None:
    # Recreates the table empty. Thumbnails regenerate on demand; the 32 GB of
    # bytes that lived here are not restored (the disk cache holds them).
    op.execute("""
        CREATE TABLE IF NOT EXISTS photo_thumbnails (
            photo_id    INTEGER NOT NULL REFERENCES photos(id) ON DELETE CASCADE,
            size        VARCHAR(8) NOT NULL,
            data        BYTEA NOT NULL,
            created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
            CONSTRAINT pk_photo_thumbnails PRIMARY KEY (photo_id, size)
        )
    """)
