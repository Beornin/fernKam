"""face_blur_score — add blur_score column to faces table

Revision ID: 0017
Revises: 0016
Create Date: 2026-06-28
"""
from alembic import op
import sqlalchemy as sa

revision = "0017"
down_revision = "0016"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "faces",
        sa.Column("blur_score", sa.Numeric(precision=8, scale=2), nullable=True),
    )


def downgrade():
    op.drop_column("faces", "blur_score")
