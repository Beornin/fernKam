"""species_status: GBIF facts about each species tag (IUCN category, introduced to the US).

Revision ID: 0035
Revises: 0034
"""
from alembic import op

revision = "0035"
down_revision = "0034"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE IF NOT EXISTS species_status (
            tag_id        INTEGER PRIMARY KEY REFERENCES tags(id) ON DELETE CASCADE,
            taxon_key     INTEGER,
            iucn          TEXT,
            introduced_us BOOLEAN NOT NULL DEFAULT false,
            checked_at    TIMESTAMPTZ NOT NULL DEFAULT now()
        )""")


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS species_status")
