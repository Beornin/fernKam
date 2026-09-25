"""Record edits made to library files outside fernKam.

fernKam honours an edit made in another program (the file's value is taken)
and records what changed here, so the user is told — including, when the
same field also had an unsaved fernKam edit, the value that was replaced, so
it can be restored. See fernkam/sync_merge.py and the "Changed outside
fernKam" page.

kind: metadata | pixels | moved | damaged

Revision ID: 0028
Revises: 0027
"""
from alembic import op

revision = "0028"
down_revision = "0027"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE IF NOT EXISTS outside_changes (
            id          BIGSERIAL PRIMARY KEY,
            photo_id    INTEGER NOT NULL REFERENCES photos(id) ON DELETE CASCADE,
            detected_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            kind        VARCHAR(16) NOT NULL,
            details     JSONB NOT NULL DEFAULT '{}'::jsonb,
            dismissed   BOOLEAN NOT NULL DEFAULT false
        )
    """)
    op.execute("""
        CREATE INDEX IF NOT EXISTS ix_outside_changes_open
        ON outside_changes (detected_at DESC) WHERE NOT dismissed
    """)
    op.execute("CREATE INDEX IF NOT EXISTS ix_outside_changes_photo ON outside_changes (photo_id)")


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS outside_changes")
