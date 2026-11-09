"""One person per name: a unique index on lower(name) for person tags.

Only created when no two people already share a name; otherwise the People page's merge creates it
once the last pair is merged (people.ensure_unique_person_names). Find-or-create code reuses an
existing person by name, so the index only ever stops a real duplicate.

Revision ID: 0038
Revises: 0037
"""
from alembic import op

revision = "0038"
down_revision = "0037"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        DO $$ BEGIN
            IF NOT EXISTS (SELECT lower(name) FROM tags WHERE is_person GROUP BY 1 HAVING count(*) > 1) THEN
                CREATE UNIQUE INDEX IF NOT EXISTS uq_tags_person_name ON tags (lower(name)) WHERE is_person;
            ELSE
                RAISE NOTICE 'two people share a name: merging them adds uq_tags_person_name';
            END IF;
        END $$
    """)


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS uq_tags_person_name")
