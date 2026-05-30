"""add_best_match_score

Revision ID: 0006
Revises: 0005
Create Date: 2026-05-29

"""
from __future__ import annotations
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

revision: str = '0006'
down_revision: Union[str, None] = '0005'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('faces', sa.Column('best_match_score', sa.Numeric(precision=3, scale=4), nullable=True))


def downgrade() -> None:
    op.drop_column('faces', 'best_match_score')
