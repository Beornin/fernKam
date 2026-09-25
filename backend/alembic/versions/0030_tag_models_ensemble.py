"""More image models and a local vision model for Tag Review.

photo_embeddings   one vector per photo per extra image model (SigLIP 2,
                   BioCLIP 2, ...). CLIP stays in photos.embedding_v. Vectors
                   of different models differ in length, so the column has no
                   fixed dimension; each model gets a partial HNSW index on
                   v::vector(dim) when it is first indexed (embed_index.py).
tag_suggestions.source   'model' (a tag's classifiers) or 'name' (found by
                   the tag's name, before the tag has enough approved photos).
tag_models.experts one entry per image model: its weight for this tag and its
                   cross-validated numbers.
tag_checks         what a local vision model answered when asked whether a
                   photo shows a tag. Shown next to the photo and compared with
                   the user's decisions; never used as a label.

Revision ID: 0030
Revises: 0029
"""
from alembic import op

revision = "0030"
down_revision = "0029"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE IF NOT EXISTS photo_embeddings (
            photo_id   INTEGER NOT NULL REFERENCES photos(id) ON DELETE CASCADE,
            model      VARCHAR(32) NOT NULL,
            v          vector NOT NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            PRIMARY KEY (photo_id, model)
        )
    """)
    op.execute("CREATE INDEX IF NOT EXISTS ix_photo_embeddings_model ON photo_embeddings (model, photo_id)")
    op.execute("ALTER TABLE tag_suggestions ADD COLUMN IF NOT EXISTS source VARCHAR(8) NOT NULL DEFAULT 'model'")
    op.execute("ALTER TABLE tag_models ALTER COLUMN embedding TYPE VARCHAR(128)")
    op.execute("ALTER TABLE tag_models ADD COLUMN IF NOT EXISTS experts JSONB NOT NULL DEFAULT '[]'::jsonb")
    op.execute("""
        CREATE TABLE IF NOT EXISTS tag_checks (
            photo_id   INTEGER NOT NULL REFERENCES photos(id) ON DELETE CASCADE,
            tag_id     INTEGER NOT NULL REFERENCES tags(id) ON DELETE CASCADE,
            model      VARCHAR(128) NOT NULL,
            verdict    SMALLINT,          -- 1 yes, 0 no, NULL unsure
            p_yes      REAL,
            checked_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            PRIMARY KEY (photo_id, tag_id)
        )
    """)
    op.execute("CREATE INDEX IF NOT EXISTS ix_tag_checks_tag ON tag_checks (tag_id, photo_id)")


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS tag_checks")
    op.execute("ALTER TABLE tag_models DROP COLUMN IF EXISTS experts")
    op.execute("ALTER TABLE tag_suggestions DROP COLUMN IF EXISTS source")
    op.execute("DROP TABLE IF EXISTS photo_embeddings")
