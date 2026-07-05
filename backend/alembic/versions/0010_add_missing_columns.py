"""add_missing_columns — file_sync_dirty on photos, crop_data on faces

Revision ID: 0010
Revises: 0009
Create Date: 2026-06-25

These columns were added to 0001 for fresh installs but were never added via
a migration for existing databases, so we use ADD COLUMN IF NOT EXISTS to
make this safe to run regardless of current state.
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = '0010'
down_revision: Union[str, None] = '0009'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("""
        ALTER TABLE photos
        ADD COLUMN IF NOT EXISTS file_sync_dirty BOOLEAN NOT NULL DEFAULT FALSE
    """)
    op.execute("""
        ALTER TABLE faces
        ADD COLUMN IF NOT EXISTS crop_data BYTEA
    """)


def downgrade() -> None:
    op.execute("ALTER TABLE photos DROP COLUMN IF EXISTS file_sync_dirty")
    op.execute("ALTER TABLE faces DROP COLUMN IF EXISTS crop_data")
