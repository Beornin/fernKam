"""exif_gin_index — GIN index on photos.exif JSONB

Revision ID: 0016
Revises: 0015
Create Date: 2026-06-28
"""
from __future__ import annotations
from typing import Sequence, Union
from alembic import op

revision: str = "0016"
down_revision: Union[str, None] = "0015"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_index(
        "ix_photos_exif_gin",
        "photos",
        ["exif"],
        postgresql_using="gin",
        postgresql_where="exif IS NOT NULL",
    )


def downgrade() -> None:
    op.drop_index("ix_photos_exif_gin", table_name="photos")
