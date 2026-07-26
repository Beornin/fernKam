"""face_confirmed_by — track whether a confirmed face was set by a human or automation

Revision ID: 0019
Revises: 0018
Create Date: 2026-07-23
"""
from alembic import op
import sqlalchemy as sa

revision = "0019"
down_revision = "0018"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "faces",
        sa.Column("confirmed_by", sa.String(length=16), nullable=True),
    )
    op.create_index(
        "ix_faces_confirmed_by_score",
        "faces",
        ["confirmed_by", "best_match_score"],
        postgresql_where=sa.text("status = 'confirmed'"),
    )


def downgrade():
    op.drop_index("ix_faces_confirmed_by_score", table_name="faces")
    op.drop_column("faces", "confirmed_by")
