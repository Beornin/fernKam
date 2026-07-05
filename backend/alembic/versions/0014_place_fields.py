"""place_fields — country/state/city from reverse geocoding

Revision ID: 0014
Revises: 0013
Create Date: 2026-06-28
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0014"
down_revision: Union[str, None] = "0013"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("photos", sa.Column("country_code", sa.String(4), nullable=True))
    op.add_column("photos", sa.Column("country", sa.Text, nullable=True))
    op.add_column("photos", sa.Column("state", sa.Text, nullable=True))
    op.add_column("photos", sa.Column("city", sa.Text, nullable=True))
    op.create_index("ix_photos_country_code", "photos", ["country_code"],
                    postgresql_where=sa.text("country_code IS NOT NULL"))
    op.create_index("ix_photos_city", "photos", ["city"],
                    postgresql_where=sa.text("city IS NOT NULL"))


def downgrade() -> None:
    op.drop_index("ix_photos_city", table_name="photos")
    op.drop_index("ix_photos_country_code", table_name="photos")
    op.drop_column("photos", "city")
    op.drop_column("photos", "state")
    op.drop_column("photos", "country")
    op.drop_column("photos", "country_code")
