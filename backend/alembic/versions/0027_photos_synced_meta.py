"""Remember what each file's editable metadata was at the last sync.

Rating, colour label, title, caption and tags can change on both sides: in
fernKam (written back later) and in other programs editing the file directly.
With only a dirty flag there was no way to tell which side changed a field, so
a rescan simply let the file win and silently discarded pending in-app edits.

`photos.synced_meta` holds those fields as they were in the file the last time
fernKam read or wrote it, which makes a three-way merge possible (see
fernkam/sync_merge.py). NULL until a photo's file is next read or written.

Revision ID: 0027
Revises: 0026
"""
from alembic import op

revision = "0027"
down_revision = "0026"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE photos ADD COLUMN IF NOT EXISTS synced_meta JSONB")


def downgrade() -> None:
    op.execute("ALTER TABLE photos DROP COLUMN IF EXISTS synced_meta")
