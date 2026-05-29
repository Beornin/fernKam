"""Add face embedding + sync tracking columns.

Revision ID: 0003
Revises: 0002
Create Date: 2026-05-24
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Face embedding stored as raw float32 bytes (numpy .tobytes())
    op.add_column("faces", sa.Column("embedding", sa.LargeBinary(), nullable=True))

    # Track when this row was last written to file (XMP/sidecar)
    op.add_column("faces", sa.Column(
        "file_synced_at",
        sa.DateTime(timezone=True),
        nullable=True,
        comment="Last time face data was written to the image file XMP",
    ))

    # Track when tags/rating/caption were last written to file
    op.add_column("photos", sa.Column(
        "meta_synced_at",
        sa.DateTime(timezone=True),
        nullable=True,
        comment="Last time photo metadata was written to the image file XMP",
    ))
    op.add_column("photos", sa.Column(
        "file_modified_at_sync",
        sa.DateTime(timezone=True),
        nullable=True,
        comment="file mtime when we last synced FROM the file into DB",
    ))

    op.create_index("ix_faces_embedding_notnull", "faces",
                    ["photo_id"], postgresql_where=sa.text("embedding IS NOT NULL"))


def downgrade() -> None:
    op.drop_index("ix_faces_embedding_notnull", "faces")
    op.drop_column("faces", "embedding")
    op.drop_column("faces", "file_synced_at")
    op.drop_column("photos", "meta_synced_at")
    op.drop_column("photos", "file_modified_at_sync")
