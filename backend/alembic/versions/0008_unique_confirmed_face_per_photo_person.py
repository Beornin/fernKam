"""unique_confirmed_face_per_photo_person

Revision ID: 0008
Revises: 0007
Create Date: 2026-06-11

Deduplicates existing confirmed faces (keeps best best_match_score, resets
the rest to unconfirmed) then adds a partial unique index so the DB enforces
the invariant going forward.
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = '0008'
down_revision: Union[str, None] = '0007'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        """
        UPDATE faces
        SET status = 'unconfirmed',
            person_tag_id = NULL
        WHERE id IN (
            SELECT id FROM (
                SELECT id,
                       ROW_NUMBER() OVER (
                           PARTITION BY photo_id, person_tag_id
                           ORDER BY COALESCE(best_match_score, 0) DESC
                       ) AS rn
                FROM faces
                WHERE status = 'confirmed'
                  AND person_tag_id IS NOT NULL
            ) t
            WHERE rn > 1
        )
        """
    )
    op.execute(
        """
        CREATE UNIQUE INDEX uq_faces_confirmed_per_photo_person
            ON faces (photo_id, person_tag_id)
            WHERE status = 'confirmed'
        """
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS uq_faces_confirmed_per_photo_person")
