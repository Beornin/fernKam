"""Species range priors from GBIF for Tag Review.

tag_species       a tag linked to a GBIF taxon (usually a species), and the
                  class used to correct for recording effort (e.g. Aves).
gbif_cell_counts  records of a taxon in the 3° × 3° box around a 1° cell of
                  the library, per month (1-12); month 0 is all months and
                  marks the cell as fetched even when the count is 0.

See fernkam/species_range.py.

Revision ID: 0031
Revises: 0030
"""
from alembic import op

revision = "0031"
down_revision = "0030"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE IF NOT EXISTS tag_species (
            tag_id           INTEGER PRIMARY KEY REFERENCES tags(id) ON DELETE CASCADE,
            taxon_key        INTEGER NOT NULL,
            scientific_name  TEXT NOT NULL,
            common_name      TEXT,
            rank             VARCHAR(16),
            class_key        INTEGER NOT NULL,
            class_name       TEXT,
            linked_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
            range_fetched_at TIMESTAMPTZ
        )
    """)
    op.execute("""
        CREATE TABLE IF NOT EXISTS gbif_cell_counts (
            taxon_key  INTEGER NOT NULL,
            cell       VARCHAR(16) NOT NULL,
            month      SMALLINT NOT NULL,
            n          INTEGER NOT NULL,
            fetched_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            PRIMARY KEY (taxon_key, cell, month)
        )
    """)


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS gbif_cell_counts")
    op.execute("DROP TABLE IF EXISTS tag_species")
