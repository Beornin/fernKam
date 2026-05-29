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
    
    Returns dict with stats: added, updated, skipped, errors, total
    """
    settings = get_settings()
    main_library = Path(settings.library_root)
    library_root = Path(custom_path) if custom_path else main_library
    
    if not library_root.exists():
        return {"error": f"Library root does not exist: {library_root}"}
    
    stats = {"added": 0, "updated": 0, "skipped": 0, "errors": 0, "total": 0}
    
    # Get all existing photos from DB for comparison
    existing_photos = {}
    result = await db.execute(select(Photo.id, Photo.album_path, Photo.filename, Photo.modified_at))
    for row in result:
        key = (row.album_path, row.filename)
        existing_photos[key] = {"id": row.id, "mtime": row.modified_at}
    
    # Walk the library directory
    for root, dirs, files in os.walk(library_root):
        # Skip hidden directories
        dirs[:] = [d for d in dirs if not d.startswith(".")]
        
        for filename in files:
            ext = Path(filename).suffix.lower()
            if ext not in ALL_EXTENSIONS:
                continue
            
            stats["total"] += 1
            
            # Calculate relative path from main library root (so photo_disk_path resolves correctly)
            full_path = Path(root) / filename
            try:
                rel_path = full_path.relative_to(main_library)
            except ValueError:
                # custom_path is outside main library - use custom_path as base
                rel_path = full_path.relative_to(library_root)
            album_path = str(rel_path.parent).replace("\\", "/") if rel_path.parent != Path(".") else "/"
            
            # Get file mtime
            try:
                file_mtime = datetime.fromtimestamp(full_path.stat().st_mtime, tz=timezone.utc)
            except OSError as e:
                stats["errors"] += 1
                continue
            
            # Check if photo exists in DB
            key = (album_path, filename)
            existing = existing_photos.get(key)
            
            if existing:
                # Check if file has been modified
                if existing["mtime"] and file_mtime > existing["mtime"]:
                    # Update existing photo
                    try:
                        await update_photo_metadata(db, existing["id"], full_path, file_mtime)
                        stats["updated"] += 1
                    except Exception as e:
                        stats["errors"] += 1
                else:
                    stats["skipped"] += 1
            else:
                # New photo - import it
                try:
                    await import_new_photo(db, full_path, album_path, filename, file_mtime)
                    stats["added"] += 1
                except Exception as e:
                    stats["errors"] += 1
            
            # Report progress periodically
            if progress_callback and stats["total"] % 100 == 0:
                await progress_callback(stats)
    
    await db.commit()
    
    if progress_callback:
        await progress_callback(stats)
    
    return stats


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
        camera=metadata.get("camera"),
        lens=metadata.get("lens"),
        rating=metadata.get("rating", 0),
        color_label=metadata.get("color_label"),
        title=metadata.get("title"),
        caption=metadata.get("caption"),
        latitude=metadata.get("latitude"),
        longitude=metadata.get("longitude"),
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
    metadata = read_file_metadata(file_path)
    
    updates = {
        "modified_at": file_mtime,
        "file_modified_at_sync": file_mtime,
    }
    
    # Only update fields that exist in metadata
    for field in ["width", "height", "file_size", "taken_at", "camera", "lens", "rating", "color_label", "title", "caption", "latitude", "longitude"]:
        if field in metadata:
            updates[field] = metadata[field]
    
    await db.execute(
        update(Photo).where(Photo.id == photo_id).values(**updates)
    )
