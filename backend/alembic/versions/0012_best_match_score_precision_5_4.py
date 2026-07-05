"""best_match_score_precision_5_4 — allow a perfect 1.0 cosine score

Revision ID: 0012
Revises: 0011
Create Date: 2026-06-28

best_match_score was Numeric(4,4) (max abs < 1.0000), but a perfect cosine
match (identical embeddings / duplicate face) yields exactly 1.0, which raises
"A field with precision 4, scale 4 must round to an absolute value less than 1".
Widen to Numeric(5,4) to match det_score and the ORM model.
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0012"
down_revision: Union[str, None] = "0011"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.alter_column(
        "faces", "best_match_score",
        existing_type=sa.Numeric(precision=4, scale=4),
        type_=sa.Numeric(precision=5, scale=4),
        existing_nullable=True,
    )


def downgrade() -> None:
    op.alter_column(
        "faces", "best_match_score",
        existing_type=sa.Numeric(precision=5, scale=4),
        type_=sa.Numeric(precision=4, scale=4),
        existing_nullable=True,
    )
