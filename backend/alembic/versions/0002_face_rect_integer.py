"""face rect columns: Numeric(8,6) -> Integer (DigiKam stores pixel coords)

Revision ID: 0002
Revises: 0001
Create Date: 2026-05-23

"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0002"
down_revision: Union[str, None] = "0001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    for col in ("x", "y", "w", "h"):
        op.alter_column(
            "faces",
            col,
            type_=sa.Integer(),
            postgresql_using=f"{col}::integer",
        )


def downgrade() -> None:
    for col in ("x", "y", "w", "h"):
        op.alter_column(
            "faces",
            col,
            type_=sa.Numeric(8, 6),
            postgresql_using=f"{col}::numeric(8,6)",
        )
