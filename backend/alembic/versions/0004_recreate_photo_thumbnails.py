"""Recreate photo_thumbnails table (was incorrectly dropped in 492d3537f49b)

Revision ID: 0004
Revises: 492d3537f49b
Create Date: 2026-05-28

"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = '0004'
down_revision: Union[str, None] = '492d3537f49b'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'photo_thumbnails',
        sa.Column('photo_id', sa.BigInteger(), nullable=False),
        sa.Column('size', sa.String(length=8), nullable=False),
        sa.Column('data', postgresql.BYTEA(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=True),
        sa.ForeignKeyConstraint(['photo_id'], ['photos.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('photo_id', 'size'),
    )


def downgrade() -> None:
    op.drop_table('photo_thumbnails')
