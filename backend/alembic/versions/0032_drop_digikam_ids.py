"""Drop the digiKam ID columns.

They mapped rows to digiKam's MariaDB for the one-time importer, which is
removed along with MariaDB. All four were empty on the live catalogue.

Revision ID: 0032
Revises: 0031
"""
from alembic import op

revision = "0032"
down_revision = "0031"
branch_labels = None
depends_on = None

_COLUMNS = (("photos", "digikam_id", "BIGINT"), ("tags", "digikam_id", "INTEGER"),
            ("faces", "digikam_image_id", "BIGINT"), ("faces", "digikam_tag_id", "INTEGER"))


def upgrade() -> None:
    for table, column, _ in _COLUMNS:
        op.execute(f"ALTER TABLE {table} DROP COLUMN IF EXISTS {column}")


def downgrade() -> None:
    for table, column, sql_type in _COLUMNS:
        op.execute(f"ALTER TABLE {table} ADD COLUMN IF NOT EXISTS {column} {sql_type}")
