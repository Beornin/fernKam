"""initial schema

Revision ID: 0001
Revises:
Create Date: 2026-05-23

"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision: str = "0001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _has_extension(conn, name: str) -> bool:
    row = conn.execute(
        sa.text("SELECT 1 FROM pg_available_extensions WHERE name = :n AND installed_version IS NOT NULL"),
        {"n": name},
    ).fetchone()
    return row is not None


def upgrade() -> None:
    conn = op.get_bind()

    # ── Extensions ────────────────────────────────────────────────────────────
    op.execute("CREATE EXTENSION IF NOT EXISTS ltree")
    op.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")

    has_vector = _has_extension(conn, "vector")
    has_postgis = _has_extension(conn, "postgis")

    if has_vector:
        op.execute("CREATE EXTENSION IF NOT EXISTS vector")
    if has_postgis:
        op.execute("CREATE EXTENSION IF NOT EXISTS postgis")

    # ── cameras ───────────────────────────────────────────────────────────────
    op.create_table(
        "cameras",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("make", sa.String(128)),
        sa.Column("model", sa.String(128)),
        sa.Column("serial", sa.String(128)),
        sa.UniqueConstraint("make", "model", "serial", name="uq_cameras_make_model_serial"),
    )

    # ── lenses ────────────────────────────────────────────────────────────────
    op.create_table(
        "lenses",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("make", sa.String(128)),
        sa.Column("model", sa.String(128)),
        sa.UniqueConstraint("make", "model", name="uq_lenses_make_model"),
    )

    # ── tags ──────────────────────────────────────────────────────────────────
    op.create_table(
        "tags",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("digikam_id", sa.Integer, unique=True),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("path", sa.Text, nullable=False),
        sa.Column("parent_id", sa.Integer, sa.ForeignKey("tags.id")),
        sa.Column("icon", sa.String(255)),
        sa.Column("color", sa.String(16)),
        sa.Column("is_person", sa.Boolean, server_default="false", nullable=False),
    )

    # ── photos ────────────────────────────────────────────────────────────────
    op.create_table(
        "photos",
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column("digikam_id", sa.BigInteger, unique=True),
        sa.Column("filename", sa.String(512), nullable=False),
        sa.Column("album_path", sa.Text, nullable=False),
        sa.Column("sha256", sa.String(64)),
        sa.Column("file_size", sa.BigInteger),
        sa.Column("media_type", sa.String(16), server_default="image", nullable=False),
        sa.Column("taken_at", sa.DateTime(timezone=True)),
        sa.Column("modified_at", sa.DateTime(timezone=True)),
        sa.Column("imported_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("rating", sa.SmallInteger, server_default="0", nullable=False),
        sa.Column("title", sa.Text),
        sa.Column("caption", sa.Text),
        sa.Column("color_label", sa.SmallInteger, server_default="0", nullable=False),
        sa.Column("latitude", sa.Numeric(10, 7)),
        sa.Column("longitude", sa.Numeric(10, 7)),
        sa.Column("altitude", sa.Numeric(10, 2)),
        sa.Column("camera_id", sa.Integer, sa.ForeignKey("cameras.id")),
        sa.Column("lens_id", sa.Integer, sa.ForeignKey("lenses.id")),
        sa.Column("exif", JSONB),
        sa.Column("status", sa.SmallInteger, server_default="1", nullable=False),
        sa.Column("orientation", sa.SmallInteger),
        sa.Column("width", sa.Integer),
        sa.Column("height", sa.Integer),
        sa.Column("color_depth", sa.SmallInteger),
        sa.Column("color_model", sa.SmallInteger),
    )

    # ── people ────────────────────────────────────────────────────────────────
    op.create_table(
        "people",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("tag_id", sa.Integer, sa.ForeignKey("tags.id"), unique=True),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("notes", sa.Text),
    )

    # ── photo_tags ────────────────────────────────────────────────────────────
    op.create_table(
        "photo_tags",
        sa.Column("photo_id", sa.BigInteger, sa.ForeignKey("photos.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("tag_id", sa.Integer, sa.ForeignKey("tags.id", ondelete="CASCADE"), primary_key=True),
    )

    # ── faces ─────────────────────────────────────────────────────────────────
    op.create_table(
        "faces",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("photo_id", sa.BigInteger, sa.ForeignKey("photos.id", ondelete="CASCADE"), nullable=False),
        sa.Column("person_id", sa.Integer, sa.ForeignKey("people.id")),
        sa.Column("person_tag_id", sa.Integer, sa.ForeignKey("tags.id")),
        sa.Column("x", sa.Numeric(8, 6)),
        sa.Column("y", sa.Numeric(8, 6)),
        sa.Column("w", sa.Numeric(8, 6)),
        sa.Column("h", sa.Numeric(8, 6)),
        sa.Column("region_name", sa.String(255)),
        sa.Column("region_type", sa.String(64)),
        sa.Column("status", sa.String(32), server_default="unconfirmed", nullable=False),
        sa.Column("digikam_image_id", sa.BigInteger),
        sa.Column("digikam_tag_id", sa.Integer),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )

    # ── audit_log ─────────────────────────────────────────────────────────────
    op.create_table(
        "audit_log",
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column("table_name", sa.String(64), nullable=False),
        sa.Column("row_id", sa.String(64)),
        sa.Column("action", sa.String(16), nullable=False),
        sa.Column("changed_by", sa.String(128)),
        sa.Column("changed_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("payload", JSONB),
    )

    # ── Indexes ───────────────────────────────────────────────────────────────
    op.create_index("ix_photos_taken_at", "photos", ["taken_at"])
    op.create_index("ix_photos_rating", "photos", ["rating"])
    op.create_index("ix_photos_sha256", "photos", ["sha256"])
    op.create_index("ix_photos_filename_trgm", "photos", ["filename"],
                    postgresql_using="gin", postgresql_ops={"filename": "gin_trgm_ops"})
    op.create_index("ix_photos_exif_gin", "photos", ["exif"],
                    postgresql_using="gin")

    op.create_index("ix_tags_name_trgm", "tags", ["name"],
                    postgresql_using="gin", postgresql_ops={"name": "gin_trgm_ops"})

    # Cast path column to ltree and add GiST index
    op.execute("ALTER TABLE tags ALTER COLUMN path TYPE ltree USING path::ltree")
    op.execute("CREATE INDEX ix_tags_path_gist ON tags USING gist (path)")

    op.create_index("ix_photo_tags_tag_photo", "photo_tags", ["tag_id", "photo_id"])
    op.create_index("ix_photo_tags_photo_tag", "photo_tags", ["photo_id", "tag_id"])

    op.create_index("ix_faces_person_tag_id", "faces", ["person_tag_id"])
    op.create_index("ix_faces_photo_id", "faces", ["photo_id"])
    op.create_index("ix_audit_log_table_changed_at", "audit_log", ["table_name", "changed_at"])

    # pgvector HNSW index (only if vector extension is available)
    if has_vector:
        op.execute("ALTER TABLE faces ADD COLUMN embedding vector(512)")
        op.execute(
            "CREATE INDEX ix_faces_embedding_hnsw ON faces "
            "USING hnsw (embedding vector_cosine_ops) WITH (m = 16, ef_construction = 64)"
        )

    # PostGIS geography column (only if postgis is available)
    if has_postgis:
        op.execute(
            "ALTER TABLE photos ADD COLUMN geo geography(Point, 4326) "
            "GENERATED ALWAYS AS ("
            "  CASE WHEN latitude IS NOT NULL AND longitude IS NOT NULL "
            "    THEN ST_SetSRID(ST_MakePoint(longitude, latitude), 4326)::geography "
            "    ELSE NULL END"
            ") STORED"
        )
        op.execute("CREATE INDEX ix_photos_geo_gist ON photos USING gist (geo)")


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_photos_geo_gist")
    op.execute("DROP INDEX IF EXISTS ix_faces_embedding_hnsw")
    op.drop_table("audit_log")
    op.drop_table("faces")
    op.drop_table("photo_tags")
    op.drop_table("people")
    op.drop_table("photos")
    op.drop_table("tags")
    op.drop_table("lenses")
    op.drop_table("cameras")
    op.execute("DROP EXTENSION IF EXISTS pg_trgm")
    op.execute("DROP EXTENSION IF EXISTS ltree")
    op.execute("DROP EXTENSION IF EXISTS postgis")
    op.execute("DROP EXTENSION IF EXISTS vector")
