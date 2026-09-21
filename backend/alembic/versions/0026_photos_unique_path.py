"""De-duplicate photos and make (album_path, filename) unique.

The scanner builds `existing_photos` as a dict keyed by (album_path, filename).
A dict collapses duplicates, so a second row for the same file is invisible to
every phase of the scan: it is never matched against disk, never refreshed, and
never deleted. It simply accumulates.

Two scans running at once is all it takes — each sees the file as new and
inserts it. The live catalogue had 2,833 such rows, all created by two scans
started two minutes apart.

The symptom is a photo in the grid that will not open: one of the pair points at
a path the file has since moved away from, and no amount of rescanning clears it
because the scanner cannot see it.

Fixing this in the importer would mean trusting every future caller to get it
right. A unique index makes the duplicate impossible at the only layer that
cannot be bypassed.

Revision ID: 0026
Revises: 0025
"""
from alembic import op

revision = "0026"
down_revision = "0025"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Keep the copy carrying real work (faces, tags, embedding, rating) and
    # fall back to the oldest id. Measured on the live catalogue the extras were
    # essentially empty — 0 tags, 0 embeddings, 0 ratings, 1 face — but ordering
    # by substance costs nothing and means the tie-break never loses data.
    op.execute("""
        DELETE FROM photos p USING (
            SELECT id, ROW_NUMBER() OVER (
                PARTITION BY album_path, filename
                ORDER BY
                    (EXISTS (SELECT 1 FROM faces f WHERE f.photo_id = photos.id)) DESC,
                    (EXISTS (SELECT 1 FROM photo_tags t WHERE t.photo_id = photos.id)) DESC,
                    (embedding_v IS NOT NULL) DESC,
                    rating DESC,
                    id ASC
            ) AS rn
            FROM photos
        ) dup
        WHERE p.id = dup.id AND dup.rn > 1
    """)
    op.execute("""
        CREATE UNIQUE INDEX IF NOT EXISTS uq_photos_album_filename
        ON photos (album_path, filename)
    """)


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS uq_photos_album_filename")
