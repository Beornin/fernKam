"""saved_searches — smart albums / saved filter definitions

Revision ID: 0013
Revises: 0012
Create Date: 2026-06-28
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "0013"
down_revision: Union[str, None] = "0012"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "saved_searches",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("name", sa.Text, nullable=False),
        sa.Column("description", sa.Text, nullable=True),
        sa.Column("filters", JSONB, nullable=False, server_default="{}"),
        sa.Column("sort", sa.Text, nullable=False, server_default="taken_at_desc"),
        sa.Column("pin_to_sidebar", sa.Boolean, nullable=False, server_default="false"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )
    op.create_index("ix_saved_searches_name", "saved_searches", ["name"])


def downgrade() -> None:
    op.drop_index("ix_saved_searches_name", table_name="saved_searches")
    op.drop_table("saved_searches")
