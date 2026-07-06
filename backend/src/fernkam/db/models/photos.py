from __future__ import annotations

import uuid
from datetime import datetime
from typing import Optional

import sqlalchemy as sa
from sqlalchemy import (
    BigInteger,
    Boolean,
    Computed,
    Float,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    Numeric,
    SmallInteger,
    String,
    Text,
    UniqueConstraint,
    func,
    types,
)
from sqlalchemy.dialects.postgresql import JSONB, TSVECTOR, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

try:
    from pgvector.sqlalchemy import Vector  # type: ignore
except ImportError:  # pragma: no cover
    Vector = None  # type: ignore

from fernkam.db.base import Base


class LtreeType(types.TypeDecorator):
    """PostgreSQL ltree column type. Wraps bound values with text2ltree() so
    asyncpg does not error on 'ltree = character varying'."""
    impl = Text
    cache_ok = True

    def get_col_spec(self, **kw):
        return "ltree"

    def bind_expression(self, bindvalue):
        return func.text2ltree(bindvalue)


class Camera(Base):
    __tablename__ = "cameras"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    make: Mapped[Optional[str]] = mapped_column(String(128))
    model: Mapped[Optional[str]] = mapped_column(String(128))
    serial: Mapped[Optional[str]] = mapped_column(String(128))

    __table_args__ = (UniqueConstraint("make", "model", "serial", name="uq_cameras_make_model_serial"),)

    photos: Mapped[list["Photo"]] = relationship(back_populates="camera")


class Lens(Base):
    __tablename__ = "lenses"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    make: Mapped[Optional[str]] = mapped_column(String(128))
    model: Mapped[Optional[str]] = mapped_column(String(128))

    __table_args__ = (UniqueConstraint("make", "model", name="uq_lenses_make_model"),)

    photos: Mapped[list["Photo"]] = relationship(back_populates="lens")


class PhotoStack(Base):
    """Groups a RAW file with its JPG/TIF/edited derivatives (cataloging only)."""
    __tablename__ = "photo_stacks"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    album_path: Mapped[str] = mapped_column(Text, nullable=False)
    stem_key: Mapped[str] = mapped_column(Text, nullable=False)
    cover_photo_id: Mapped[Optional[int]] = mapped_column(ForeignKey("photos.id", ondelete="SET NULL"))
    member_count: Mapped[int] = mapped_column(Integer, default=0)
    has_raw: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (
        UniqueConstraint("album_path", "stem_key", name="uq_photo_stacks_album_stem"),
    )

    members: Mapped[list["Photo"]] = relationship(back_populates="stack", foreign_keys="Photo.stack_id")
    cover: Mapped[Optional["Photo"]] = relationship(foreign_keys=[cover_photo_id], post_update=True)


class Photo(Base):
    __tablename__ = "photos"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    digikam_id: Mapped[Optional[int]] = mapped_column(BigInteger, unique=True)

    # File identity
    filename: Mapped[str] = mapped_column(String(512), nullable=False)
    album_path: Mapped[str] = mapped_column(Text, nullable=False)
    sha256: Mapped[Optional[str]] = mapped_column(String(64))
    file_size: Mapped[Optional[int]] = mapped_column(BigInteger)
    media_type: Mapped[str] = mapped_column(String(16), default="image")  # image | video

    # Dates
    taken_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    modified_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    imported_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    # User metadata
    rating: Mapped[int] = mapped_column(SmallInteger, default=0)
    title: Mapped[Optional[str]] = mapped_column(Text)
    caption: Mapped[Optional[str]] = mapped_column(Text)
    color_label: Mapped[int] = mapped_column(SmallInteger, default=0)

    # Geo (stored as plain lat/lon floats; PostGIS geo col added by migration if available)
    latitude: Mapped[Optional[float]] = mapped_column(Numeric(10, 7))
    longitude: Mapped[Optional[float]] = mapped_column(Numeric(10, 7))
    altitude: Mapped[Optional[float]] = mapped_column(Numeric(10, 2))

    # Video duration (seconds, populated by ffprobe at import / backfill)
    duration_secs: Mapped[Optional[float]] = mapped_column(Float)

    # Reverse-geocoded place fields (populated by background task)
    country_code: Mapped[Optional[str]] = mapped_column(String(4))
    country: Mapped[Optional[str]] = mapped_column(Text)
    state: Mapped[Optional[str]] = mapped_column(Text)
    city: Mapped[Optional[str]] = mapped_column(Text)

    # Camera & lens
    camera_id: Mapped[Optional[int]] = mapped_column(ForeignKey("cameras.id"))
    lens_id: Mapped[Optional[int]] = mapped_column(ForeignKey("lenses.id"))

    # EXIF dump (everything from DigiKam's ImageMetadata + raw)
    exif: Mapped[Optional[dict]] = mapped_column(JSONB)

    # Sync tracking
    meta_synced_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    file_modified_at_sync: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    file_sync_dirty: Mapped[bool] = mapped_column(Boolean, default=False)
    faces_scanned_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))

    # Status flags (mirrors DigiKam ImageInformation.status)
    status: Mapped[int] = mapped_column(SmallInteger, default=1)
    orientation: Mapped[Optional[int]] = mapped_column(SmallInteger)
    width: Mapped[Optional[int]] = mapped_column(Integer)
    height: Mapped[Optional[int]] = mapped_column(Integer)
    color_depth: Mapped[Optional[int]] = mapped_column(SmallInteger)
    color_model: Mapped[Optional[int]] = mapped_column(SmallInteger)

    # Stacks (RAW + JPG/TIF/edited derivatives grouped together)
    stack_id: Mapped[Optional[int]] = mapped_column(ForeignKey("photo_stacks.id", ondelete="SET NULL"))
    stack_role: Mapped[Optional[str]] = mapped_column(String(16))  # "raw" | "derivative"

    # Full-text search vector (STORED generated column; read-only to the ORM).
    # Weighted: filename(A) > title(B) > caption(C). Tag-name search is OR'd in
    # at query time via the trigram index on tags.name.
    search_tsv: Mapped[Optional[str]] = mapped_column(
        TSVECTOR,
        Computed(
            "setweight(to_tsvector('english', coalesce(filename, '')), 'A') || "
            "setweight(to_tsvector('english', coalesce(title, '')), 'B') || "
            "setweight(to_tsvector('english', coalesce(caption, '')), 'C')",
            persisted=True,
        ),
        nullable=True,
    )

    camera: Mapped[Optional["Camera"]] = relationship(back_populates="photos")
    lens: Mapped[Optional["Lens"]] = relationship(back_populates="photos")
    photo_tags: Mapped[list["PhotoTag"]] = relationship(back_populates="photo", cascade="all, delete-orphan")
    faces: Mapped[list["Face"]] = relationship(back_populates="photo", cascade="all, delete-orphan")
    stack: Mapped[Optional["PhotoStack"]] = relationship(back_populates="members", foreign_keys=[stack_id])

    __table_args__ = (
        Index("ix_photos_filename_trgm", "filename", postgresql_using="gin", postgresql_ops={"filename": "gin_trgm_ops"}),
        Index("ix_photos_album_path", "album_path", postgresql_ops={"album_path": "text_pattern_ops"}),
        Index("ix_photos_unscanned", "id",
              postgresql_where=sa.text("faces_scanned_at IS NULL AND status = 1 AND media_type = 'image'")),
        Index("ix_photos_sync_dirty", "id",
              postgresql_where=sa.text("file_sync_dirty = TRUE")),
        # Phase 0B: sort/filter + search foundation
        Index("ix_photos_taken_at", sa.text("taken_at DESC NULLS LAST")),
        Index("ix_photos_taken_at_id", sa.text("taken_at DESC NULLS LAST, id DESC")),
        Index("ix_photos_imported_at", sa.text("imported_at DESC")),
        Index("ix_photos_rating", "rating", postgresql_where=sa.text("rating > 0")),
        Index("ix_photos_color_label", "color_label", postgresql_where=sa.text("color_label > 0")),
        Index("ix_photos_media_type", "media_type"),
        Index("ix_photos_stack_id", "stack_id", postgresql_where=sa.text("stack_id IS NOT NULL")),
        Index("ix_photos_camera_id", "camera_id", postgresql_where=sa.text("camera_id IS NOT NULL")),
        Index("ix_photos_lens_id", "lens_id", postgresql_where=sa.text("lens_id IS NOT NULL")),
        Index("ix_photos_no_date", "id", postgresql_where=sa.text("taken_at IS NULL AND status = 1")),
        Index("ix_photos_has_gps", "id", postgresql_where=sa.text("latitude IS NOT NULL AND longitude IS NOT NULL")),
        Index("ix_photos_exif_gin", "exif", postgresql_using="gin", postgresql_ops={"exif": "jsonb_path_ops"}),
        Index("ix_photos_sha256", "sha256", postgresql_where=sa.text("sha256 IS NOT NULL")),
        Index("ix_photos_search_tsv", "search_tsv", postgresql_using="gin"),
    )


class Tag(Base):
    __tablename__ = "tags"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    digikam_id: Mapped[Optional[int]] = mapped_column(Integer, unique=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    path: Mapped[str] = mapped_column(LtreeType, nullable=False)
    parent_id: Mapped[Optional[int]] = mapped_column(ForeignKey("tags.id"))
    icon: Mapped[Optional[str]] = mapped_column(String(255))
    color: Mapped[Optional[str]] = mapped_column(String(16))
    is_person: Mapped[bool] = mapped_column(Boolean, default=False)

    parent: Mapped[Optional["Tag"]] = relationship("Tag", remote_side="Tag.id", back_populates="children")
    children: Mapped[list["Tag"]] = relationship("Tag", back_populates="parent")
    photo_tags: Mapped[list["PhotoTag"]] = relationship(back_populates="tag", cascade="all, delete-orphan")
    faces: Mapped[list["Face"]] = relationship(back_populates="person_tag")

    __table_args__ = (
        Index("ix_tags_name_trgm", "name", postgresql_using="gin", postgresql_ops={"name": "gin_trgm_ops"}),
        # ltree GiST index is added by migration after casting path column to ltree
    )


class PhotoTag(Base):
    __tablename__ = "photo_tags"

    photo_id: Mapped[int] = mapped_column(ForeignKey("photos.id", ondelete="CASCADE"), primary_key=True)
    tag_id: Mapped[int] = mapped_column(ForeignKey("tags.id", ondelete="CASCADE"), primary_key=True)

    photo: Mapped["Photo"] = relationship(back_populates="photo_tags")
    tag: Mapped["Tag"] = relationship(back_populates="photo_tags")

    __table_args__ = (
        Index("ix_photo_tags_tag_photo", "tag_id", "photo_id"),
        Index("ix_photo_tags_photo_tag", "photo_id", "tag_id"),
    )


class Person(Base):
    __tablename__ = "people"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    tag_id: Mapped[Optional[int]] = mapped_column(ForeignKey("tags.id"), unique=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    notes: Mapped[Optional[str]] = mapped_column(Text)

    tag: Mapped[Optional["Tag"]] = relationship()
    faces: Mapped[list["Face"]] = relationship(back_populates="person")


class Face(Base):
    __tablename__ = "faces"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    photo_id: Mapped[int] = mapped_column(ForeignKey("photos.id", ondelete="CASCADE"), nullable=False)
    person_id: Mapped[Optional[int]] = mapped_column(ForeignKey("people.id"))
    person_tag_id: Mapped[Optional[int]] = mapped_column(ForeignKey("tags.id"))

    # Bounding box (absolute pixel coords from DigiKam)
    x: Mapped[Optional[int]] = mapped_column(Integer)
    y: Mapped[Optional[int]] = mapped_column(Integer)
    w: Mapped[Optional[int]] = mapped_column(Integer)
    h: Mapped[Optional[int]] = mapped_column(Integer)

    # DigiKam face region metadata
    region_name: Mapped[Optional[str]] = mapped_column(String(255))
    region_type: Mapped[Optional[str]] = mapped_column(String(64))

    # Recognition status: unconfirmed|confirmed|ignored|unknown
    status: Mapped[str] = mapped_column(String(32), default="unconfirmed")

    # 512-dim float32 embedding stored as raw bytes (legacy / file-sync source)
    embedding: Mapped[Optional[bytes]] = mapped_column(LargeBinary)

    # Indexed pgvector copy used for similarity search (HNSW). Kept in sync with `embedding`.
    embedding_v: Mapped[Optional[list[float]]] = mapped_column(
        Vector(512) if Vector is not None else LargeBinary,
        nullable=True,
    )

    # Cached face crop (webp bytes)
    crop_data: Mapped[Optional[bytes]] = mapped_column(LargeBinary)

    # InsightFace detection confidence (0.0-1.0); stored at scan time
    det_score: Mapped[Optional[float]] = mapped_column(Numeric(5, 4), nullable=True)

    # Laplacian variance of the face crop — proxy for sharpness (higher = sharper)
    blur_score: Mapped[Optional[float]] = mapped_column(Numeric(8, 2), nullable=True)

    # Best match score against all confirmed faces (0.0-1.0)
    best_match_score: Mapped[Optional[float]] = mapped_column(Numeric(5, 4))

    file_synced_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))

    digikam_image_id: Mapped[Optional[int]] = mapped_column(BigInteger)
    digikam_tag_id: Mapped[Optional[int]] = mapped_column(Integer)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    photo: Mapped["Photo"] = relationship(back_populates="faces")
    person: Mapped[Optional["Person"]] = relationship(back_populates="faces")
    person_tag: Mapped[Optional["Tag"]] = relationship(foreign_keys=[person_tag_id])

    __table_args__ = (
        Index("ix_faces_photo_id", "photo_id"),
        Index("ix_faces_person_tag_id", "person_tag_id"),
        Index("ix_faces_status", "status"),
    )


class PersonCentroid(Base):
    """Per-person mean embedding (centroid) for fast ranked suggestions."""
    __tablename__ = "person_centroids"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    person_tag_id: Mapped[int] = mapped_column(
        ForeignKey("tags.id", ondelete="CASCADE"), nullable=False
    )
    label: Mapped[int] = mapped_column(SmallInteger, default=0)  # sub-cluster index
    embedding_v: Mapped[Optional[list[float]]] = mapped_column(
        Vector(512) if Vector is not None else LargeBinary,
        nullable=True,
    )
    face_count: Mapped[int] = mapped_column(Integer, default=0)
    built_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (
        UniqueConstraint("person_tag_id", "label", name="uq_person_centroids_ptid_label"),
        Index("ix_person_centroids_ptid", "person_tag_id"),
    )


class AuditLog(Base):
    __tablename__ = "audit_log"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    table_name: Mapped[str] = mapped_column(String(64), nullable=False)
    row_id: Mapped[Optional[str]] = mapped_column(String(64))
    action: Mapped[str] = mapped_column(String(16), nullable=False)
    changed_by: Mapped[Optional[str]] = mapped_column(String(128))
    changed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    payload: Mapped[Optional[dict]] = mapped_column(JSONB)

    __table_args__ = (Index("ix_audit_log_table_changed_at", "table_name", "changed_at"),)


class AppLog(Base):
    """Centralized log entries: Python logger output + native stderr lines.

    Repeated identical messages are coalesced (see logs_sink.py): the first row
    is INSERTed, subsequent ones inside the coalesce window UPDATE
    `occurrences` and `last_seen_at` instead of inserting again.
    """

    __tablename__ = "app_logs"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    level: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    level_name: Mapped[str] = mapped_column(String(10), nullable=False)
    source: Mapped[str] = mapped_column(String(64), nullable=False)  # 'python' | 'stderr' | 'request'
    logger_name: Mapped[Optional[str]] = mapped_column(String(128))
    message: Mapped[str] = mapped_column(Text, nullable=False)
    file: Mapped[Optional[str]] = mapped_column(String(255))
    line: Mapped[Optional[int]] = mapped_column(Integer)
    func_name: Mapped[Optional[str]] = mapped_column("func", String(128))
    exc_info: Mapped[Optional[str]] = mapped_column(Text)
    context: Mapped[Optional[dict]] = mapped_column(JSONB)
    fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    occurrences: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    __table_args__ = (
        Index("ix_app_logs_ts", ts.desc()),
        Index("ix_app_logs_level", "level"),
        Index("ix_app_logs_source", "source"),
        Index("ix_app_logs_fp_recent", "fingerprint", last_seen_at.desc()),
    )


class SavedSearch(Base):
    """Persisted filter + sort definition (smart album / saved search)."""
    __tablename__ = "saved_searches"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    description: Mapped[Optional[str]] = mapped_column(Text)
    filters: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    sort: Mapped[str] = mapped_column(Text, nullable=False, default="taken_at_desc")
    pin_to_sidebar: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    __table_args__ = (Index("ix_saved_searches_name", "name"),)
