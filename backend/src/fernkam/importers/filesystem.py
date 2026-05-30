"""Filesystem scanner to import photos/videos from library root."""
from __future__ import annotations

import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from fernkam.config import get_settings
from fernkam.db.models.photos import Face, Photo, PhotoTag, Tag
from fernkam.metadata_sync import read_file_metadata_async

# Supported image/video extensions
IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".gif", ".bmp", ".tiff", ".tif", ".webp", ".heic", ".heif", ".raw", ".cr2", ".nef", ".arw", ".dng"}
VIDEO_EXTENSIONS = {".mp4", ".mov", ".avi", ".mkv", ".webm", ".flv", ".wmv", ".m4v", ".3gp"}
ALL_EXTENSIONS = IMAGE_EXTENSIONS | VIDEO_EXTENSIONS


async def scan_library(
    db: AsyncSession,
    custom_path: Optional[str] = None,
    progress_callback: Optional[callable] = None,
) -> dict:
    """Scan library_root (or custom_path) for new/updated photos and import them.

    - New file on disk  → import with full metadata
    - Existing file     → skip (do nothing)
    - File removed      → delete from DB
    
    Returns dict with stats: added, skipped, deleted, errors, total
    """
    from sqlalchemy import delete as sa_delete
    settings = get_settings()
    main_library = Path(settings.library_root)
    library_root = Path(custom_path) if custom_path else main_library
    
    if not library_root.exists():
        return {"error": f"Library root does not exist: {library_root}"}
    
    stats = {"added": 0, "skipped": 0, "deleted": 0, "errors": 0, "total": 0}
    
    # Load all existing DB photos
    existing_photos: dict[tuple[str, str], int] = {}
    result = await db.execute(select(Photo.id, Photo.album_path, Photo.filename))
    for row in result:
        key = (row.album_path, row.filename)
        existing_photos[key] = row.id

    # Determine which directories to scan.
    # If custom_path given → walk that directory fully (discovery mode).
    # Otherwise → only walk albums already known in DB (fast resync mode).
    if custom_path:
        scan_dirs = [library_root]
    else:
        known_albums = {ap for (ap, _) in existing_photos}
        scan_dirs = [library_root / ap for ap in known_albums if (library_root / ap).exists()]
        # Always include library root itself (for root-level photos, album_path == "/")
        if library_root.exists() and any(ap in ("/", "") for (ap, _) in existing_photos):
            scan_dirs.append(library_root)

    disk_keys: set[tuple[str, str]] = set()

    for scan_dir in scan_dirs:
        for root, dirs, files in os.walk(scan_dir):
            dirs[:] = [d for d in dirs if not d.startswith(".")]
            
            for filename in files:
                ext = Path(filename).suffix.lower()
                if ext not in ALL_EXTENSIONS:
                    continue
                
                stats["total"] += 1
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
                        file_mtime = datetime.fromtimestamp(full_path.stat().st_mtime, tz=timezone.utc)
                        await import_new_photo(db, full_path, album_path, filename, file_mtime)
                        await db.commit()  # atomic per photo: success → commit
                        stats["added"] += 1
                    except Exception as e:
                        await db.rollback()  # failure → rollback just this photo
                        stats["errors"] += 1
                        import logging
                        logging.getLogger(__name__).warning(f"Failed to import {full_path}: {e}")
                
                if progress_callback and stats["total"] % 100 == 0:
                    await progress_callback(stats)
    
    # Delete DB photos whose files are gone from disk.
    # When a custom_path is given, only consider photos that live inside that directory
    # so that photos from other folders are never accidentally removed.
    for key, photo_id in existing_photos.items():
        if key in disk_keys:
            continue
        album_path_str, _ = key
        if custom_path:
            # Check whether this photo's absolute path is under the scanned directory
            photo_abs = main_library / album_path_str
            try:
                photo_abs.relative_to(library_root)
            except ValueError:
                continue  # outside scan scope — leave it alone
        await db.execute(sa_delete(Photo).where(Photo.id == photo_id))
        stats["deleted"] += 1
    
    await db.commit()  # commit deletions
    
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
) -> Photo:
    """Import a new photo into the database."""
    # Extract metadata from file (non-blocking)
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
    if media_type == "image":
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
