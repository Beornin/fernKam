"""Filesystem scanner to import photos/videos from library root."""
from __future__ import annotations

import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import asyncio
import logging

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from fernkam.config import get_settings
from fernkam.db.models.photos import Face, Photo, PhotoTag, Tag
from fernkam.metadata_sync import read_file_metadata_async

log = logging.getLogger(__name__)

# Parallelism tuning — auto-scaled to CPU count, overridable by env vars.
_CPUS = os.cpu_count() or 4
METADATA_CONCURRENCY = int(os.getenv("FERNKAM_META_CONCURRENCY", str(min(32, _CPUS * 2))))
THUMB_CONCURRENCY = int(os.getenv("FERNKAM_THUMB_CONCURRENCY", str(max(4, _CPUS))))
BATCH_COMMIT_SIZE = 50      # photos committed per transaction

# Supported image/video extensions
IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".gif", ".bmp", ".tiff", ".tif", ".webp", ".heic", ".heif", ".raw", ".cr2", ".nef", ".arw", ".dng"}
VIDEO_EXTENSIONS = {".mp4", ".mov", ".avi", ".mkv", ".webm", ".flv", ".wmv", ".m4v", ".3gp"}
ALL_EXTENSIONS = IMAGE_EXTENSIONS | VIDEO_EXTENSIONS


async def _read_meta_safe(sem: asyncio.Semaphore, file_path: Path) -> dict:
    """Read file metadata with bounded concurrency; never raises."""
    async with sem:
        try:
            return await read_file_metadata_async(file_path)
        except Exception as exc:
            log.warning("metadata read error %s: %s", file_path, exc)
            return {}


async def _gen_thumbs_for_photo(
    sem: asyncio.Semaphore,
    file_path: Path,
    photo_id: int,
    media_type: str,
) -> tuple[int, dict[str, bytes]]:
    """Generate all thumbnail sizes for one photo using a thread executor."""
    from fernkam.thumbnails import generate_thumbnail_bytes
    if media_type != "image":
        return photo_id, {}
    loop = asyncio.get_event_loop()
    async with sem:
        results = await asyncio.gather(*[
            loop.run_in_executor(None, generate_thumbnail_bytes, file_path, size)
            for size in ("sm", "md", "lg", "xl")
        ], return_exceptions=True)
    size_bytes = {
        sz: data
        for sz, data in zip(("sm", "md", "lg", "xl"), results)
        if isinstance(data, bytes) and data
    }
    return photo_id, size_bytes


async def scan_library(
    db: AsyncSession,
    custom_path: Optional[str] = None,
    progress_callback: Optional[callable] = None,
    photo_added_callback: Optional[callable] = None,
) -> dict:
    """Scan library_root (or custom_path) for new/updated photos and import them.

    - New file on disk  → import with full metadata (parallel metadata reads, batch commits)
    - Existing file     → skip (do nothing)
    - File removed      → delete from DB
    - photo_added_callback(photo_id) called per batch, immediately after batch commit

    Returns dict with stats: added, skipped, deleted, errors, total, added_ids
    """
    from sqlalchemy import delete as sa_delete
    from fernkam.thumbnails import store_thumbnail_to_db
    settings = get_settings()
    main_library = Path(settings.library_root)
    library_root = Path(custom_path) if custom_path else main_library

    if not library_root.exists():
        return {"error": f"Library root does not exist: {library_root}"}

    stats = {"added": 0, "skipped": 0, "deleted": 0, "errors": 0, "total": 0}

    # ── Phase 0: load existing photos ──────────────────────────────────────
    existing_photos: dict[tuple[str, str], int] = {}
    result = await db.execute(select(Photo.id, Photo.album_path, Photo.filename))
    for row in result:
        existing_photos[(row.album_path, row.filename)] = row.id

    # Always walk from the root so new directories are discovered.
    # Already-imported files are skipped in O(1) via the existing_photos dict,
    # so this is cheap even for large libraries.
    scan_dirs = [library_root]

    # ── Phase 1: walk filesystem, collect new files ────────────────────────
    new_files: list[tuple[Path, str, str, datetime]] = []  # (path, album_path, filename, mtime)
    disk_keys: set[tuple[str, str]] = set()

    for scan_dir in scan_dirs:
        for root, dirs, files in os.walk(scan_dir):
            dirs[:] = [d for d in dirs if not d.startswith(".")]
            for filename in files:
                ext = Path(filename).suffix.lower()
                if ext not in ALL_EXTENSIONS:
                    continue
                full_path = Path(root) / filename
                try:
                    rel_path = full_path.relative_to(main_library)
                except ValueError:
                    rel_path = full_path.relative_to(library_root)
                album_path = str(rel_path.parent).replace("\\", "/") if rel_path.parent != Path(".") else "/"
                key = (album_path, filename)
                disk_keys.add(key)
                if key in existing_photos:
                    stats["skipped"] += 1
                else:
                    try:
                        mtime = datetime.fromtimestamp(full_path.stat().st_mtime, tz=timezone.utc)
                    except OSError:
                        mtime = datetime.now(timezone.utc)
                    new_files.append((full_path, album_path, filename, mtime))

    stats["total"] = stats["skipped"] + len(new_files)
    log.info("[SCAN] %d new files to import, %d existing", len(new_files), stats["skipped"])

    # ── Phase 2: import in batches with concurrent metadata reads ──────────
    meta_sem = asyncio.Semaphore(METADATA_CONCURRENCY)
    thumb_sem = asyncio.Semaphore(THUMB_CONCURRENCY)
    added_ids: list[int] = []

    for batch_start in range(0, len(new_files), BATCH_COMMIT_SIZE):
        batch = new_files[batch_start: batch_start + BATCH_COMMIT_SIZE]

        # Read metadata for ALL files in batch concurrently
        metadatas: list[dict] = list(await asyncio.gather(
            *[_read_meta_safe(meta_sem, fp) for fp, *_ in batch]
        ))

        # Insert photos to DB (sequential within batch, savepoint per photo)
        batch_photos: list[tuple[int, Path, str]] = []  # (photo_id, path, media_type)
        for (full_path, album_path, filename, file_mtime), metadata in zip(batch, metadatas):
            try:
                async with db.begin_nested():  # savepoint — isolates per-photo failures
                    photo = await import_new_photo(
                        db, full_path, album_path, filename, file_mtime,
                        metadata=metadata, generate_thumbs=False,
                    )
                batch_photos.append((photo.id, full_path, photo.media_type))
                added_ids.append(photo.id)
                stats["added"] += 1
            except Exception as exc:
                log.warning("import error %s: %s", full_path, exc)
                stats["errors"] += 1

        # Generate thumbnails for all photos in batch concurrently
        if batch_photos:
            thumb_results = await asyncio.gather(*[
                _gen_thumbs_for_photo(thumb_sem, fp, pid, mt)
                for pid, fp, mt in batch_photos
            ], return_exceptions=True)
            for res in thumb_results:
                if isinstance(res, Exception):
                    continue
                photo_id, size_bytes = res
                for size, data in size_bytes.items():
                    try:
                        await store_thumbnail_to_db(photo_id, size, data, db)
                    except Exception:
                        pass

        await db.commit()
        log.info("[SCAN] batch %d-%d committed (%d added, %d errors)",
                 batch_start, batch_start + len(batch), stats["added"], stats["errors"])

        # Fire callback per photo immediately so callers can pipeline face
        # detection while the next import batch is in flight.
        if photo_added_callback and batch_photos:
            for photo_id, _, _ in batch_photos:
                try:
                    await photo_added_callback(photo_id)
                except Exception as cbe:
                    log.warning("photo_added_callback error for %d: %s", photo_id, cbe)

        if progress_callback:
            await progress_callback(stats)

    # ── Phase 3: delete removed files ─────────────────────────────────────
    for key, photo_id in existing_photos.items():
        if key in disk_keys:
            continue
        album_path_str, _ = key
        if custom_path:
            photo_abs = main_library / album_path_str
            try:
                photo_abs.relative_to(library_root)
            except ValueError:
                continue
        await db.execute(sa_delete(Photo).where(Photo.id == photo_id))
        stats["deleted"] += 1

    await db.commit()

    stats["added_ids"] = added_ids
    if progress_callback:
        await progress_callback(stats)
    return stats


async def _get_or_create_camera(db: AsyncSession, camera_info: dict):
    """Find or create a Camera record using a savepoint to isolate failures."""
    from fernkam.db.models.photos import Camera
    make = camera_info.get("make")
    model = camera_info.get("model")
    serial = camera_info.get("serial")
    # Try read first (no savepoint needed)
    existing = (await db.execute(
        select(Camera).where(Camera.make == make, Camera.model == model, Camera.serial == serial)
    )).scalar_one_or_none()
    if existing:
        return existing
    # Use savepoint so a constraint violation doesn't kill the parent transaction
    async with db.begin_nested():
        cam = Camera(make=make, model=model, serial=serial)
        db.add(cam)
        await db.flush()
    # Re-fetch in case of concurrent insert
    return (await db.execute(
        select(Camera).where(Camera.make == make, Camera.model == model, Camera.serial == serial)
    )).scalar_one()


async def _get_or_create_lens(db: AsyncSession, lens_info: dict):
    """Find or create a Lens record using a savepoint to isolate failures."""
    from fernkam.db.models.photos import Lens
    make = lens_info.get("make")
    model = lens_info.get("model")
    existing = (await db.execute(
        select(Lens).where(Lens.make == make, Lens.model == model)
    )).scalar_one_or_none()
    if existing:
        return existing
    async with db.begin_nested():
        lens = Lens(make=make, model=model)
        db.add(lens)
        await db.flush()
    return (await db.execute(
        select(Lens).where(Lens.make == make, Lens.model == model)
    )).scalar_one()


def _ltree_label(name: str) -> str:
    """Sanitize a string to a valid ltree label (letters, digits, underscores only)."""
    return re.sub(r"[^A-Za-z0-9]", "_", name.strip()) or "_"


async def _get_or_create_person_tag(db: AsyncSession, name: str) -> int:
    """Return the id of a person Tag with the given name, creating it (under People) if needed."""
    # Try exact name match with is_person=True first
    tag = (await db.execute(
        select(Tag).where(Tag.name == name, Tag.is_person == True)
    )).scalar_one_or_none()
    if tag:
        return tag.id

    # Fallback: any tag with this name
    tag = (await db.execute(
        select(Tag).where(Tag.name == name)
    )).scalar_one_or_none()
    if tag:
        if not tag.is_person:
            tag.is_person = True
        return tag.id

    # Create "People" root tag if missing
    people = (await db.execute(
        select(Tag).where(Tag.name == "People", Tag.parent_id == None)
    )).scalar_one_or_none()
    if people is None:
        people = Tag(name="People", path="People", is_person=False)
        db.add(people)
        await db.flush()

    # Create person tag under People
    ltree_path = f"People.{_ltree_label(name)}"
    tag = Tag(name=name, path=ltree_path, parent_id=people.id, is_person=True)
    db.add(tag)
    await db.flush()
    return tag.id


async def import_new_photo(
    db: AsyncSession,
    file_path: Path,
    album_path: str,
    filename: str,
    file_mtime: datetime,
    metadata: Optional[dict] = None,
    generate_thumbs: bool = True,
) -> Photo:
    """Import a new photo into the database.

    Pass pre-read ``metadata`` (from a concurrent gather) to skip re-reading.
    Set ``generate_thumbs=False`` when the caller handles thumbnails externally.
    """
    if metadata is None:
        metadata = await read_file_metadata_async(file_path)
    
    # Determine media type
    ext = file_path.suffix.lower()
    media_type = "video" if ext in VIDEO_EXTENSIONS else "image"

    # Resolve camera/lens records
    camera_obj = None
    if metadata.get("camera"):
        try:
            camera_obj = await _get_or_create_camera(db, metadata["camera"])
        except Exception:
            pass

    lens_obj = None
    if metadata.get("lens"):
        try:
            lens_obj = await _get_or_create_lens(db, metadata["lens"])
        except Exception:
            pass

    # Create photo record
    photo = Photo(
        album_path=album_path,
        filename=filename,
        media_type=media_type,
        modified_at=file_mtime,
        file_modified_at_sync=file_mtime,
        width=metadata.get("width"),
        height=metadata.get("height"),
        file_size=metadata.get("file_size"),
        taken_at=metadata.get("taken_at"),
        imported_at=datetime.now(timezone.utc),
        camera_id=camera_obj.id if camera_obj else None,
        lens_id=lens_obj.id if lens_obj else None,
        rating=metadata.get("rating", 0),
        color_label=metadata.get("color_label"),
        title=metadata.get("title"),
        caption=metadata.get("caption"),
        latitude=metadata.get("latitude"),
        longitude=metadata.get("longitude"),
        altitude=metadata.get("altitude"),
        orientation=metadata.get("orientation"),
        exif=metadata.get("exif"),
        status=1,
    )
    
    db.add(photo)
    await db.flush()

    # Generate thumbnails and store in DB (images only)
    if generate_thumbs and media_type == "image":
        try:
            from fernkam.thumbnails import generate_thumbnail_bytes, store_thumbnail_to_db
            for size in ("sm", "md", "lg", "xl"):
                data = generate_thumbnail_bytes(file_path, size)
                if data:
                    await store_thumbnail_to_db(photo.id, size, data, db)
        except Exception:
            pass  # Thumbnail generation failure is non-fatal

    # Restore tags from HierarchicalSubject paths (DigiKam style: "People|Ava", "Locations|Paris")
    tag_paths = metadata.get("tag_paths") or []  # already converted from | to /
    tag_cache: dict[str, Tag] = {}
    for path_str in tag_paths:
        parts = [p.strip() for p in path_str.split("/") if p.strip()]
        if not parts:
            continue
        parent_tag: Optional[Tag] = None
        for i, part in enumerate(parts):
            ltree_path = ".".join(_ltree_label(p) for p in parts[: i + 1])
            if ltree_path in tag_cache:
                parent_tag = tag_cache[ltree_path]
                continue
            tag_row = (await db.execute(
                select(Tag).where(Tag.path == ltree_path)
            )).scalar_one_or_none()
            if tag_row is None:
                is_person = (parts[0].lower() == "people" and i == len(parts) - 1)
                tag_row = Tag(
                    name=part,
                    path=ltree_path,
                    parent_id=parent_tag.id if parent_tag else None,
                    is_person=is_person,
                )
                db.add(tag_row)
                await db.flush()
            tag_cache[ltree_path] = tag_row
            parent_tag = tag_row
        # Associate leaf tag with photo
        leaf = parent_tag
        if leaf:
            exists = (await db.execute(
                select(PhotoTag).where(PhotoTag.photo_id == photo.id, PhotoTag.tag_id == leaf.id)
            )).scalar_one_or_none()
            if not exists:
                db.add(PhotoTag(photo_id=photo.id, tag_id=leaf.id))

    # Restore face regions from XMP if present
    xmp_faces = metadata.get("faces") or []
    img_w = metadata.get("width") or metadata.get("img_w")
    img_h = metadata.get("height") or metadata.get("img_h")
    if xmp_faces and img_w and img_h:
        for f in xmp_faces:
            cx, cy = f.get("cx", 0), f.get("cy", 0)
            nw, nh = f.get("nw", 0), f.get("nh", 0)
            if not (nw and nh):
                continue
            px = int((cx - nw / 2) * img_w)
            py = int((cy - nh / 2) * img_h)
            pw = int(nw * img_w)
            ph = int(nh * img_h)
            name = (f.get("name") or "").strip()
            person_tag_id = None
            if name:
                person_tag_id = await _get_or_create_person_tag(db, name)
            face = Face(
                photo_id=photo.id,
                x=px, y=py, w=pw, h=ph,
                region_name=name or None,
                region_type=f.get("type", "Face"),
                status="confirmed" if name else "unknown",
                person_tag_id=person_tag_id,
            )
            db.add(face)

    return photo


async def update_photo_metadata(
    db: AsyncSession,
    photo_id: int,
    file_path: Path,
    file_mtime: datetime,
) -> None:
    """Update photo metadata from file."""
    from fernkam.metadata_sync import read_file_metadata_async
    metadata = await read_file_metadata_async(file_path)
    
    updates = {
        "modified_at": file_mtime,
        "file_modified_at_sync": file_mtime,
    }
    
    # Only update fields that exist in metadata
    for field in ["width", "height", "file_size", "taken_at", "camera", "lens", "rating", "color_label", "title", "caption", "latitude", "longitude", "altitude", "orientation", "exif"]:
        if field in metadata:
            updates[field] = metadata[field]
    
    # Handle camera/lens record creation
    if metadata.get("camera"):
        try:
            cam = await _get_or_create_camera(db, metadata["camera"])
            updates["camera_id"] = cam.id
        except Exception:
            pass

    if metadata.get("lens"):
        try:
            lens = await _get_or_create_lens(db, metadata["lens"])
            updates["lens_id"] = lens.id
        except Exception:
            pass
    
    await db.execute(
        update(Photo).where(Photo.id == photo_id).values(**updates)
    )
