"""face_sort_indexes — index faces.created_at and faces.best_match_score

Both are sort keys queried over the full ~78k-row faces table with no index:
created_at is sorted by GET /api/faces/ on every call and filtered by the
incremental auto-confirm sweep (created_at > :since); best_match_score is
sorted by GET /api/faces/recent-auto.

Revision ID: 0021
Revises: 0020
Create Date: 2026-07-24
"""
from alembic import op

revision = "0021"
down_revision = "0020"
branch_labels = None
depends_on = None


def upgrade():
    op.create_index("ix_faces_created_at", "faces", ["created_at"])
    op.create_index("ix_faces_best_match_score", "faces", ["best_match_score"])


def downgrade():
    op.drop_index("ix_faces_best_match_score", table_name="faces")
    op.drop_index("ix_faces_created_at", table_name="faces")
