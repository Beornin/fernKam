"""photos.county: the US county a photo's GPS falls inside (Census boundaries, us_counties.py).
"" means looked up and in no county (offshore, outside the US).

Revision ID: 0037
Revises: 0036
"""
from alembic import op

revision = "0037"
down_revision = "0036"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE photos ADD COLUMN IF NOT EXISTS county TEXT")


def downgrade() -> None:
    op.execute("ALTER TABLE photos DROP COLUMN IF EXISTS county")
