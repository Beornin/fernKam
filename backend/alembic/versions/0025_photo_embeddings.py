"""Add CLIP photo embeddings for semantic search (Roadmap phase 2.1).

`photos.embedding_v` holds the 512-d projected CLIP ViT-B/32 image vector. The
text tower projects into the same space, so one HNSW index serves text->image
search, image->image similarity, and kNN tag propagation.

Mirrors the proven `faces.embedding_v` setup: same extension, same cosine
opclass, same partial-index-on-NOT-NULL shape so the index only covers rows
that have been embedded.

Revision ID: 0025
Revises: 0024
"""
from alembic import op

revision = "0025"
down_revision = "0024"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")
    op.execute("ALTER TABLE photos ADD COLUMN IF NOT EXISTS embedding_v vector(512)")
    op.execute("ALTER TABLE photos ADD COLUMN IF NOT EXISTS embedded_at TIMESTAMPTZ")
    # Partial index: only embedded rows are searchable, and the index stays out
    # of the way during the initial backfill when most rows are still NULL.
    op.execute("""
        CREATE INDEX IF NOT EXISTS ix_photos_embedding_v_hnsw
        ON photos USING hnsw (embedding_v vector_cosine_ops)
        WHERE embedding_v IS NOT NULL
    """)


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_photos_embedding_v_hnsw")
    op.execute("ALTER TABLE photos DROP COLUMN IF EXISTS embedded_at")
    op.execute("ALTER TABLE photos DROP COLUMN IF EXISTS embedding_v")
