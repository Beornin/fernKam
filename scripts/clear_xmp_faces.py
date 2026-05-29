"""
Clear XMP RegionInfo from all photo files.

This script removes face region data from image XMP metadata.
Run this independently after the face removal script has completed database deletions.

Usage:
    python scripts/clear_xmp_faces.py
"""
import asyncio
import subprocess
import sys
from pathlib import Path

# Add backend to path
sys.path.insert(0, str(Path(__file__).parent.parent / "backend" / "src"))

from fernkam.config import get_settings
from fernkam.db.session import async_session_factory
from fernkam.db.models.photos import Photo
from fernkam.thumbnails import photo_disk_path
from sqlalchemy import select


# Exiftool paths
_EXIFTOOL_PATHS = [
    r"C:\Program Files\digiKam\exiftool.exe",
    r"C:\Program Files (x86)\digiKam\exiftool.exe",
    "/usr/bin/exiftool",
    "/usr/local/bin/exiftool",
]


def _find_exiftool() -> str:
    """Find exiftool executable."""
    from shutil import which
    et = which("exiftool")
    if et:
        return et
    for p in _EXIFTOOL_PATHS:
        if Path(p).exists():
            return p
    raise FileNotFoundError("exiftool not found. Please install exiftool.")


async def clear_xmp_regioninfo(exiftool_path: str) -> dict:
    """Clear RegionInfo from all photo files."""
    stats = {"processed": 0, "success": 0, "errors": 0, "skipped": 0}

    async with async_session_factory() as db:
        # Get all photos
        result = await db.execute(
            select(Photo).where(Photo.status == 1)
        )
        photos = result.scalars().all()

    total = len(photos)
    print(f"Processing {total} photos for XMP cleanup...")
    print(f"Using exiftool: {exiftool_path}")
    print()

    for i, photo in enumerate(photos):
        stats["processed"] += 1
        src = photo_disk_path(photo.album_path, photo.filename)
        
        if not src.exists():
            stats["skipped"] += 1
            continue

        try:
            # Clear RegionInfo using exiftool
            result = subprocess.run(
                [exiftool_path, "-overwrite_original", "-RegionInfo=", str(src)],
                capture_output=True,
                timeout=30,
            )
            if result.returncode == 0:
                stats["success"] += 1
            else:
                stats["errors"] += 1
        except subprocess.TimeoutExpired:
            stats["errors"] += 1
        except Exception as e:
            stats["errors"] += 1

        # Progress reporting every 1000 photos
        if stats["processed"] % 1000 == 0:
            elapsed = stats["processed"]
            print(f"  Progress: {elapsed}/{total} ({elapsed/total*100:.1f}%) - Success: {stats['success']}, Errors: {stats['errors']}, Skipped: {stats['skipped']}")

    print()
    print("=" * 60)
    print("XMP Cleanup Complete")
    print("=" * 60)
    print(f"Total processed: {stats['processed']}")
    print(f"Success: {stats['success']}")
    print(f"Errors: {stats['errors']}")
    print(f"Skipped (file not found): {stats['skipped']}")

    return stats


def main():
    print("=" * 60)
    print("XMP Face Region Cleanup")
    print("=" * 60)
    print()

    try:
        exiftool = _find_exiftool()
        asyncio.run(clear_xmp_regioninfo(exiftool))
    except FileNotFoundError as e:
        print(f"Error: {e}")
        sys.exit(1)
    except KeyboardInterrupt:
        print("\nInterrupted by user")
        sys.exit(1)


if __name__ == "__main__":
    main()
