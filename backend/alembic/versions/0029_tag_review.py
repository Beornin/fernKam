"""Tag review: every tag is checked by the user before any model learns from it.

photo_tags.verified_at  NULL = unverified (read from a file, imported from
                        digiKam, copied by a workflow). Set when the user
                        approves the tag, or adds it themselves in fernKam.
photo_tags.model_score  the tag's classifier score for an unverified link,
                        so doubtful tags can be reviewed first.
tag_rejections          the user said this tag is wrong for this photo. Used
                        as a negative example, and never suggested again.
tag_suggestions         pending model suggestions, waiting for review.
tag_models              one linear classifier per tag over photos.embedding_v,
                        trained only on approved tags and rejections, with its
                        measured accuracy.

Every existing tag starts unverified. See fernkam/tag_learning.py and the Tag
Review page.

Revision ID: 0029
Revises: 0028
"""
from alembic import op

revision = "0029"
down_revision = "0028"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE photo_tags ADD COLUMN IF NOT EXISTS verified_at TIMESTAMPTZ")
    op.execute("ALTER TABLE photo_tags ADD COLUMN IF NOT EXISTS model_score REAL")
    op.execute("""
        CREATE INDEX IF NOT EXISTS ix_photo_tags_unverified
        ON photo_tags (tag_id, photo_id) WHERE verified_at IS NULL
    """)
    op.execute("""
        CREATE TABLE IF NOT EXISTS tag_rejections (
            photo_id    INTEGER NOT NULL REFERENCES photos(id) ON DELETE CASCADE,
            tag_id      INTEGER NOT NULL REFERENCES tags(id) ON DELETE CASCADE,
            rejected_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            was_tagged  BOOLEAN NOT NULL DEFAULT false,
            PRIMARY KEY (photo_id, tag_id)
        )
    """)
    op.execute("CREATE INDEX IF NOT EXISTS ix_tag_rejections_tag ON tag_rejections (tag_id, photo_id)")
    op.execute("""
        CREATE TABLE IF NOT EXISTS tag_suggestions (
            photo_id   INTEGER NOT NULL REFERENCES photos(id) ON DELETE CASCADE,
            tag_id     INTEGER NOT NULL REFERENCES tags(id) ON DELETE CASCADE,
            score      REAL NOT NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            PRIMARY KEY (photo_id, tag_id)
        )
    """)
    op.execute("CREATE INDEX IF NOT EXISTS ix_tag_suggestions_tag ON tag_suggestions (tag_id, score DESC)")
    op.execute("""
        CREATE TABLE IF NOT EXISTS tag_models (
            tag_id            INTEGER PRIMARY KEY REFERENCES tags(id) ON DELETE CASCADE,
            trained_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
            embedding         VARCHAR(32) NOT NULL,
            coef              BYTEA NOT NULL,
            positives         INTEGER NOT NULL,
            negatives         INTEGER NOT NULL,
            labels_at_train   INTEGER NOT NULL,
            cv_agreement      REAL,
            cv_recall         REAL,
            reviewed_accepted INTEGER NOT NULL DEFAULT 0,
            reviewed_rejected INTEGER NOT NULL DEFAULT 0
        )
    """)


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS tag_models")
    op.execute("DROP TABLE IF EXISTS tag_suggestions")
    op.execute("DROP TABLE IF EXISTS tag_rejections")
    op.execute("DROP INDEX IF EXISTS ix_photo_tags_unverified")
    op.execute("ALTER TABLE photo_tags DROP COLUMN IF EXISTS model_score")
    op.execute("ALTER TABLE photo_tags DROP COLUMN IF EXISTS verified_at")
