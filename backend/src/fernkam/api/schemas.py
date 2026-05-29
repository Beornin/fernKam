from __future__ import annotations

from datetime import datetime
from typing import Any, Optional
from uuid import UUID

from pydantic import BaseModel, ConfigDict


class TagOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    digikam_id: Optional[int]
    name: str
    path: str
    parent_id: Optional[int]
    icon: Optional[str]
    color: Optional[str]
    is_person: bool
    children: list["TagOut"] = []


class CameraOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    make: Optional[str]
    model: Optional[str]


class LensOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    make: Optional[str]
    model: Optional[str]


class FaceOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    photo_id: int
    person_tag_id: Optional[int]
    person_name: Optional[str] = None
    x: Optional[int]
    y: Optional[int]
    w: Optional[int]
    h: Optional[int]
    status: str
    region_name: Optional[str]
    score: Optional[float] = None  # similarity score (suggested) or det score (new)


class FaceUpdate(BaseModel):
    person_tag_id: Optional[int] = None
    status: Optional[str] = None
    region_name: Optional[str] = None


class BatchDetectResult(BaseModel):
    processed: int
    faces_found: int
    suggested: int
    errors: int
    details: list[dict] = []


class PersonOut(BaseModel):
    id: int
    tag_id: int
    name: str
    face_count: int = 0
    avatar_face_id: Optional[str] = None


class PhotoSummary(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    digikam_id: Optional[int]
    filename: str
    album_path: str
    taken_at: Optional[datetime]
    rating: int
    color_label: int
    media_type: str
    width: Optional[int]
    height: Optional[int]


class PhotoDetail(PhotoSummary):
    sha256: Optional[str]
    file_size: Optional[int]
    modified_at: Optional[datetime]
    imported_at: datetime
    title: Optional[str]
    caption: Optional[str]
    latitude: Optional[float]
    longitude: Optional[float]
    altitude: Optional[float]
    orientation: Optional[int]
    color_depth: Optional[int]
    color_model: Optional[int]
    exif: Optional[dict[str, Any]]
    camera: Optional[CameraOut]
    lens: Optional[LensOut]
    tags: list[TagOut] = []
    faces: list[FaceOut] = []


class PhotoUpdate(BaseModel):
    rating: Optional[int] = None
    color_label: Optional[int] = None
    title: Optional[str] = None
    caption: Optional[str] = None


class AlbumNode(BaseModel):
    name: str
    path: str
    photo_count: int
    children: list["AlbumNode"] = []


class PhotoPage(BaseModel):
    total: int
    page: int
    page_size: int
    items: list[PhotoSummary]


class PersonOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    name: str
    tag_id: Optional[int]
    face_count: int = 0
