"""Filesystem scanner to import photos/videos from library root."""
from __future__ import annotations

import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from fernkam.config import get_settings
from fernkam.db.models.photos import Photo
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
    
    # Delete DB photos whose files are gone from disk
    for key, photo_id in existing_photos.items():
        if key not in disk_keys:
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
