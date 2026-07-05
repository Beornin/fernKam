"""Shared photo query builder.

Single source of truth for filtering/searching the `photos` table. Used by the
/photos list endpoint, the /search endpoint, and (later) smart albums, so that
new filters only need to be implemented once.

Search (`text`) matches across:
  - filename + title + caption   (weighted full-text via search_tsv GIN index)
  - filename substring           (trigram ILIKE — catches partial/non-word hits)
  - tag names                    (any tag attached to the photo)
  - camera / lens make+model

Date filters are sargable (range comparison on taken_at, no func.date()).
"""
from __future__ import annotations

import base64
import dataclasses
import json
import time as _time
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from typing import Optional

from sqlalchemy import Select, and_, func, or_, select, text, union
from sqlalchemy.ext.asyncio import AsyncSession

from fernkam.db.models.photos import Camera, Face, Lens, Photo, PhotoTag, Tag

# ── Count cache: short-TTL in-process cache keyed by filter signature ──
_COUNT_CACHE: dict[str, tuple[int, float]] = {}
_COUNT_TTL = 30.0  # seconds


def _cache_key(filters: "PhotoFilters") -> str:
    return json.dumps(dataclasses.asdict(filters), default=str, sort_keys=True)


async def count_photos(filters: "PhotoFilters", db: AsyncSession, base_q=None) -> int:
    """Return total matching photo count, using a 30-second in-process TTL cache.

    Pass `base_q` (already-built query) to avoid calling build_photo_query twice.
    """
    key = _cache_key(filters)
    cached = _COUNT_CACHE.get(key)
    if cached and _time.monotonic() < cached[1]:
        return cached[0]
    if base_q is None:
        base_q = await build_photo_query(filters, db)
    n = (await db.execute(select(func.count()).select_from(base_q.subquery()))).scalar_one()
    _COUNT_CACHE[key] = (n, _time.monotonic() + _COUNT_TTL)
    return n


@dataclass
class PhotoFilters:
    """All supported photo filters. Unset (None/empty) fields are ignored."""
    album_path: Optional[str] = None
    tag_id: Optional[int] = None
    person_tag_id: Optional[int] = None
    rating_min: Optional[int] = None
    color_label: Optional[int] = None
    media_type: Optional[str] = None
    camera_id: Optional[int] = None
    lens_id: Optional[int] = None
    has_gps: Optional[bool] = None
    has_faces: Optional[bool] = None
    no_date: Optional[bool] = None
    search: Optional[str] = None
    date_from: Optional[date] = None
    date_to: Optional[date] = None
    country_code: Optional[str] = None


# Whitelisted sort expressions (keyed by the API `sort` string).
SORT_OPTIONS = {
    "taken_at_desc": Photo.taken_at.desc().nulls_last(),
    "taken_at_asc": Photo.taken_at.asc().nulls_last(),
    "rating_desc": Photo.rating.desc(),
    "filename_asc": Photo.filename.asc(),
    "imported_at_desc": Photo.imported_at.desc(),
}
DEFAULT_SORT = Photo.taken_at.desc().nulls_last()


async def _tag_subtree_ids(db: AsyncSession, tag_id: int) -> list[int]:
    """Return the tag id plus all descendant ids (ltree subtree)."""
    tag = (await db.execute(select(Tag).where(Tag.id == tag_id))).scalar_one_or_none()
    if not tag:
        return []
    rows = await db.execute(select(Tag.id).where(text(f"path <@ '{tag.path}'")))
    return [r[0] for r in rows]


def _search_clause(search: str):
    """Build the OR'd search predicate across text/tags/camera/lens."""
    like = f"%{search}%"
    # websearch_to_tsquery tolerates arbitrary user input (quotes, operators)
    # without raising, unlike to_tsquery.
    tsq = func.websearch_to_tsquery("english", search)

    via_tags = (
        select(PhotoTag.photo_id)
        .join(Tag, Tag.id == PhotoTag.tag_id)
        .where(Tag.name.ilike(like))
    )
    via_camera = select(Camera.id).where(
        or_(Camera.make.ilike(like), Camera.model.ilike(like))
    )
    via_lens = select(Lens.id).where(
        or_(Lens.make.ilike(like), Lens.model.ilike(like))
    )

    return or_(
        Photo.search_tsv.op("@@")(tsq),
        Photo.filename.ilike(like),
        Photo.id.in_(via_tags),
        Photo.camera_id.in_(via_camera),
        Photo.lens_id.in_(via_lens),
    )


async def build_photo_query(f: PhotoFilters, db: AsyncSession) -> Select:
    """Build a SELECT(Photo) with all active filters applied (no sort/limit)."""
    q = select(Photo).where(Photo.status == 1)

    if f.album_path:
        clean = f.album_path.lstrip("/")
        q = q.where(Photo.album_path.like(f"{clean}%"))

    if f.tag_id is not None:
        ids = await _tag_subtree_ids(db, f.tag_id)
        if ids:
            via_tags = select(PhotoTag.photo_id).where(PhotoTag.tag_id.in_(ids))
            via_faces = select(Face.photo_id).where(
                Face.person_tag_id.in_(ids),
                Face.status.in_(["confirmed", "suggested"]),
            )
            q = q.where(Photo.id.in_(union(via_tags, via_faces)))
        else:
            q = q.where(text("false"))

    if f.person_tag_id is not None:
        ids = await _tag_subtree_ids(db, f.person_tag_id)
        if ids:
            via_faces = select(Face.photo_id).where(
                Face.person_tag_id.in_(ids),
                Face.status.in_(["confirmed", "suggested"]),
            )
            q = q.where(Photo.id.in_(via_faces))
        else:
            q = q.where(text("false"))

    if f.rating_min is not None:
        q = q.where(Photo.rating >= f.rating_min)
    if f.color_label is not None:
        q = q.where(Photo.color_label == f.color_label)
    if f.media_type:
        q = q.where(Photo.media_type == f.media_type)
    if f.camera_id is not None:
        q = q.where(Photo.camera_id == f.camera_id)
    if f.lens_id is not None:
        q = q.where(Photo.lens_id == f.lens_id)

    if f.has_gps is True:
        q = q.where(Photo.latitude.is_not(None), Photo.longitude.is_not(None))
    elif f.has_gps is False:
        q = q.where(or_(Photo.latitude.is_(None), Photo.longitude.is_(None)))

    if f.has_faces is True:
        q = q.where(Photo.id.in_(select(Face.photo_id)))
    elif f.has_faces is False:
        q = q.where(Photo.id.not_in(select(Face.photo_id)))

    if f.no_date is True:
        q = q.where(Photo.taken_at.is_(None))
    if f.country_code:
        q = q.where(Photo.country_code == f.country_code)

    if f.search:
        q = q.where(_search_clause(f.search.strip()))

    # Sargable date range: taken_at >= start, taken_at < end_exclusive.
    if f.date_from:
        start = datetime.combine(f.date_from, time.min, tzinfo=timezone.utc)
        q = q.where(Photo.taken_at >= start)
    if f.date_to:
        end_excl = datetime.combine(f.date_to + timedelta(days=1), time.min, tzinfo=timezone.utc)
        q = q.where(Photo.taken_at < end_excl)

    return q


def apply_sort(q: Select, sort: str) -> Select:
    """Apply a whitelisted ORDER BY to the query."""
    return q.order_by(SORT_OPTIONS.get(sort, DEFAULT_SORT))


# ── Keyset (seek) cursor helpers ─────────────────────────────────────────────

def encode_cursor(photo: Photo, sort: str) -> str:
    """Encode the last photo in a page as an opaque keyset cursor."""
    ta = photo.taken_at.isoformat() if photo.taken_at else None
    ia = photo.imported_at.isoformat() if photo.imported_at else None
    data: dict = {"s": sort, "id": photo.id, "ta": ta, "ia": ia,
                  "r": photo.rating, "fn": photo.filename}
    return base64.urlsafe_b64encode(json.dumps(data, separators=(",", ":")).encode()).decode()


def decode_cursor(cursor: str) -> dict:
    return json.loads(base64.urlsafe_b64decode(cursor.encode()))


def apply_cursor(q: Select, cursor: str) -> Select:
    """Add a keyset WHERE clause so no OFFSET seek is needed for load-more."""
    try:
        d = decode_cursor(cursor)
    except Exception:
        return q  # corrupt cursor → fall through to OFFSET

    sort = d.get("s", "taken_at_desc")
    cid: int = d["id"]

    if sort in ("taken_at_desc", "taken_at_asc"):
        ta_str = d.get("ta")
        if sort == "taken_at_desc":
            if ta_str:
                ta = datetime.fromisoformat(ta_str)
                return q.where(
                    or_(
                        Photo.taken_at < ta,
                        and_(Photo.taken_at == ta, Photo.id < cid),
                        Photo.taken_at.is_(None),
                    )
                )
            else:
                return q.where(Photo.taken_at.is_(None), Photo.id < cid)
        else:  # taken_at_asc — NULLs last
            if ta_str:
                ta = datetime.fromisoformat(ta_str)
                return q.where(
                    or_(
                        and_(Photo.taken_at.is_not(None), Photo.taken_at > ta),
                        and_(Photo.taken_at == ta, Photo.id > cid),
                        Photo.taken_at.is_(None),
                    )
                )
            else:
                return q.where(Photo.taken_at.is_(None), Photo.id > cid)

    elif sort == "imported_at_desc":
        ia_str = d.get("ia")
        if ia_str:
            ia = datetime.fromisoformat(ia_str)
            return q.where(or_(
                Photo.imported_at < ia,
                and_(Photo.imported_at == ia, Photo.id < cid),
            ))

    elif sort == "rating_desc":
        r: int = d.get("r", 0)
        return q.where(or_(
            Photo.rating < r,
            and_(Photo.rating == r, Photo.id < cid),
        ))

    elif sort == "filename_asc":
        fn: str = d.get("fn", "")
        return q.where(or_(
            Photo.filename > fn,
            and_(Photo.filename == fn, Photo.id > cid),
        ))

    return q
