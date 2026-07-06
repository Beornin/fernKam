"""photo_stacks — group RAW files with their JPG/TIF derivatives

Revision ID: 0018
Revises: 0017
Create Date: 2026-07-05
"""
from alembic import op
import sqlalchemy as sa

revision = "0018"
down_revision = "0017"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "photo_stacks",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("album_path", sa.Text(), nullable=False),
        sa.Column("stem_key", sa.Text(), nullable=False),
        sa.Column("cover_photo_id", sa.BigInteger(), sa.ForeignKey("photos.id", ondelete="SET NULL"), nullable=True),
        sa.Column("member_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("has_raw", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("album_path", "stem_key", name="uq_photo_stacks_album_stem"),
    )

    op.add_column("photos", sa.Column("stack_id", sa.BigInteger(), sa.ForeignKey("photo_stacks.id", ondelete="SET NULL"), nullable=True))
    op.add_column("photos", sa.Column("stack_role", sa.String(length=16), nullable=True))
    op.create_index("ix_photos_stack_id", "photos", ["stack_id"], postgresql_where=sa.text("stack_id IS NOT NULL"))


def downgrade():
    op.drop_index("ix_photos_stack_id", table_name="photos")
    op.drop_column("photos", "stack_role")
    op.drop_column("photos", "stack_id")
    op.drop_table("photo_stacks")
