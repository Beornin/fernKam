"""video_duration — add duration_secs to photos

Revision ID: 0015
Revises: 0014
Create Date: 2026-06-28
"""
from __future__ import annotations
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

revision: str = "0015"
down_revision: Union[str, None] = "0014"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("photos", sa.Column("duration_secs", sa.Float, nullable=True))
    op.create_index(
        "ix_photos_duration_secs", "photos", ["duration_secs"],
        postgresql_where=sa.text("duration_secs IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index("ix_photos_duration_secs", table_name="photos")
    op.drop_column("photos", "duration_secs")
