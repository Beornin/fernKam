from __future__ import annotations

import uuid
from datetime import datetime
from typing import Optional

import sqlalchemy as sa
from sqlalchemy import (
    BigInteger,
    Boolean,
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
from sqlalchemy.dialects.postgresql import JSONB, UUID
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

    camera: Mapped[Optional["Camera"]] = relationship(back_populates="photos")
    lens: Mapped[Optional["Lens"]] = relationship(back_populates="photos")
    photo_tags: Mapped[list["PhotoTag"]] = relationship(back_populates="photo", cascade="all, delete-orphan")
    faces: Mapped[list["Face"]] = relationship(back_populates="photo", cascade="all, delete-orphan")

    __table_args__ = (
        Index("ix_photos_taken_at", "taken_at"),
        Index("ix_photos_rating", "rating"),
        Index("ix_photos_sha256", "sha256"),
        Index("ix_photos_filename_trgm", "filename", postgresql_using="gin", postgresql_ops={"filename": "gin_trgm_ops"}),
        Index("ix_photos_exif_gin", "exif", postgresql_using="gin"),
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
        Index("ix_faces_person_tag_id", "person_tag_id"),
        Index("ix_faces_photo_id", "photo_id"),
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
