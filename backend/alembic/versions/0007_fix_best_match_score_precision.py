"""fix_best_match_score_precision

Revision ID: 0007
Revises: 0006
Create Date: 2026-05-29

"""
from __future__ import annotations
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

revision: str = '0007'
down_revision: Union[str, None] = '0006'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.alter_column('faces', 'best_match_score',
               existing_type=sa.Numeric(precision=3, scale=4),
               type_=sa.Numeric(precision=4, scale=4),
               existing_nullable=True)


def downgrade() -> None:
    op.alter_column('faces', 'best_match_score',
               existing_type=sa.Numeric(precision=4, scale=4),
               type_=sa.Numeric(precision=3, scale=4),
               existing_nullable=True)
