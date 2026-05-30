"""add_faces_scanned_at

Revision ID: 0005
Revises: 492d3537f49b
Create Date: 2026-05-29

"""
from __future__ import annotations
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

revision: str = '0005'
down_revision: Union[str, None] = '0004'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('photos', sa.Column('faces_scanned_at', sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    op.drop_column('photos', 'faces_scanned_at')
