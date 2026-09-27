"""tags.is_fact: tags that record a fact about a photo, not what is in it.

Who took it, where it was posted, "needs review": Tag Review neither learns
nor suggests them, and doesn't queue them for approval.

Revision ID: 0034
Revises: 0033
"""
from alembic import op

revision = "0034"
down_revision = "0033"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE tags ADD COLUMN IF NOT EXISTS is_fact BOOLEAN NOT NULL DEFAULT false")


def downgrade() -> None:
    op.execute("ALTER TABLE tags DROP COLUMN IF EXISTS is_fact")
