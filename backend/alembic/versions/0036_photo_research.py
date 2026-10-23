"""photos.research: research keywords and Darwin Core fields that write-back adds to the file.

Revision ID: 0036
Revises: 0035
"""
from alembic import op

revision = "0036"
down_revision = "0035"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE photos ADD COLUMN IF NOT EXISTS research JSONB")


def downgrade() -> None:
    op.execute("ALTER TABLE photos DROP COLUMN IF EXISTS research")
