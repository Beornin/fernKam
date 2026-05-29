"""
Remove all face data from fernKam database and image XMP metadata.

This script removes:
- All entries from the faces table
- All person tags (is_person=True)
- The "People" tag and all its children
- All photo_tags associations for person tags
- XMP RegionInfo from image files

Usage:
    python scripts/remove_faces.py --dry-run    # Show what would be deleted
    python scripts/remove_faces.py --commit     # Actually perform deletion
"""
import argparse
import asyncio
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Optional

# Add backend to path
sys.path.insert(0, str(Path(__file__).parent.parent / "backend" / "src"))

from fernkam.config import get_settings
from fernkam.db.session import async_session_factory
from fernkam.db.models.photos import Face, Photo, PhotoTag, Tag
from fernkam.thumbnails import photo_disk_path
from sqlalchemy import delete, select, text
from sqlalchemy.ext.asyncio import AsyncSession


# Exiftool paths
_EXIFTOOL_PATHS = [
    r"C:\Program Files\digiKam\exiftool.exe",
    r"C:\Program Files (x86)\digiKam\exiftool.exe",
    "/usr/bin/exiftool",
    "/usr/local/bin/exiftool",
]


def _find_exiftool() -> Optional[str]:
    """Find exiftool executable."""
    from shutil import which
    et = which("exiftool")
    if et:
        return et
    for p in _EXIFTOOL_PATHS:
        if Path(p).exists():
            return p
    return None


async def count_items(db: AsyncSession) -> dict:
    """Count items that would be deleted."""
    counts = {
        "faces": 0,
        "person_tags": 0,
        "people_tag_children": 0,
        "photo_tags_associations": 0,
        "photos_with_regioninfo": 0,
    }

    # Count faces
    counts["faces"] = (await db.execute(select(func.count(Face.id)))).scalar_one()

    # Count person tags
    counts["person_tags"] = (await db.execute(
        select(func.count(Tag.id)).where(Tag.is_person == True)
    )).scalar_one()

    # Find People tag and count its children
    people_tag = (await db.execute(
        select(Tag).where(Tag.name.ilike("people"))
    )).scalar_one_or_none()

    if people_tag:
        # Count all tags whose path starts with People's path using ltree operator
        people_path = str(people_tag.path)
        counts["people_tag_children"] = (await db.execute(
            select(func.count(Tag.id)).where(text(f"path <@ '{people_path}'"))
        )).scalar_one()

    # Count photo_tags associations for person tags
    counts["photo_tags_associations"] = (await db.execute(
        select(func.count()).select_from(PhotoTag)
        .join(Tag, PhotoTag.tag_id == Tag.id)
        .where(Tag.is_person == True)
    )).scalar_one()

    # Count photos that might have RegionInfo (estimate)
    counts["photos_with_regioninfo"] = (await db.execute(
        select(func.count(Photo.id)).where(Photo.status == 1)
    )).scalar_one()

    return counts


async def get_people_tag_ids(db: AsyncSession) -> list[int]:
    """Get all tag IDs under the People hierarchy."""
    people_tag = (await db.execute(
        select(Tag).where(Tag.name.ilike("people"))
    )).scalar_one_or_none()

    if not people_tag:
        return []

    people_path = str(people_tag.path)
    result = await db.execute(
        select(Tag.id).where(text(f"path <@ '{people_path}'"))
    )
    return [row[0] for row in result]


async def backup_database(db: AsyncSession, backup_path: Path) -> bool:
    """Create a SQL backup of faces and tags tables."""
    try:
        # Use pg_dump to backup specific tables
        settings = get_settings()
        pg_url = settings.pg_url_sync.replace("postgresql://", "").replace("postgresql+psycopg2://", "")
        
        # Parse connection string
        if "@" in pg_url:
            auth, host_db = pg_url.split("@")
            user_pass = auth.split(":")
            user = user_pass[0]
            password = user_pass[1] if len(user_pass) > 1 else ""
            host_port, dbname = host_db.split("/")
            host = host_port.split(":")[0]
        else:
            raise ValueError("Invalid PostgreSQL URL")

        # Build pg_dump command
        cmd = [
            "pg_dump",
            f"--host={host}",
            f"--username={user}",
            f"--dbname={dbname}",
            "--table=faces",
            "--table=tags",
            "--table=photo_tags",
            "--format=plain",
            "--file", str(backup_path),
        ]

        # Set PGPASSWORD environment variable
        import os
        env = os.environ.copy()
        env["PGPASSWORD"] = password

        result = subprocess.run(cmd, env=env, capture_output=True, text=True)
        return result.returncode == 0
    except Exception as e:
        print(f"Backup failed: {e}")
        return False


async def clear_xmp_regioninfo(db: AsyncSession, exiftool_path: str) -> dict:
    """Clear RegionInfo from all photo files."""
    stats = {"processed": 0, "success": 0, "errors": 0}

    # Get all photos
    result = await db.execute(
        select(Photo).where(Photo.status == 1)
    )
    photos = result.scalars().all()

    print(f"Processing {len(photos)} photos for XMP cleanup...")

    for photo in photos:
        stats["processed"] += 1
        src = photo_disk_path(photo.album_path, photo.filename)
        
        if not src.exists():
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
        except Exception as e:
            stats["errors"] += 1

        if stats["processed"] % 100 == 0:
            print(f"  Progress: {stats['processed']}/{len(photos)}")

    return stats


async def remove_faces(db: AsyncSession) -> int:
    """Delete all faces from the database."""
    result = await db.execute(delete(Face))
    return result.rowcount


async def remove_person_tags(db: AsyncSession) -> int:
    """Delete all person tags (is_person=True)."""
    result = await db.execute(
        delete(Tag).where(Tag.is_person == True)
    )
    return result.rowcount


async def remove_people_hierarchy(db: AsyncSession) -> int:
    """Delete People tag and all its children."""
    people_tag_ids = await get_people_tag_ids(db)
    if not people_tag_ids:
        return 0
    
    result = await db.execute(
        delete(Tag).where(Tag.id.in_(people_tag_ids))
    )
    return result.rowcount


async def remove_photo_tags_associations(db: AsyncSession) -> int:
    """Delete photo_tags associations for person tags."""
    result = await db.execute(
        delete(PhotoTag)
        .where(
            PhotoTag.tag_id.in_(
                select(Tag.id).where(Tag.is_person == True)
            )
        )
    )
    return result.rowcount


async def main():
    parser = argparse.ArgumentParser(description="Remove face data from fernKam")
    parser.add_argument("--dry-run", action="store_true", help="Show what would be deleted without making changes")
    parser.add_argument("--commit", action="store_true", help="Actually perform deletion")
    args = parser.parse_args()

    if not args.dry_run and not args.commit:
        print("Error: Must specify --dry-run or --commit")
        sys.exit(1)

    print("=" * 60)
    print("Face Removal Script")
    print("=" * 60)
    print(f"Mode: {'DRY RUN' if args.dry_run else 'COMMIT'}")
    print()

    async with async_session_factory() as db:
        # Count items
        print("Counting items to be removed...")
        counts = await count_items(db)
        print(f"  Faces: {counts['faces']}")
        print(f"  Person tags (is_person=True): {counts['person_tags']}")
        print(f"  People tag children: {counts['people_tag_children']}")
        print(f"  Photo-tag associations: {counts['photo_tags_associations']}")
        print(f"  Photos to process for XMP cleanup: {counts['photos_with_regioninfo']}")
        print()

        if args.dry_run:
            print("DRY RUN - No changes will be made")
            print("Run with --commit to actually perform deletion")
            return

        if args.commit:
            print("COMMIT MODE - Making changes...")
            print()

            # Create backup
            backup_path = Path(f"backup_faces_{datetime.now().strftime('%Y%m%d_%H%M%S')}.sql")
            print(f"Creating backup: {backup_path}")
            backup_ok = await backup_database(db, backup_path)
            if not backup_ok:
                print("WARNING: Backup failed. Proceeding anyway...")
            else:
                print("Backup created successfully")
            print()

            # Remove faces
            print("Removing faces...")
            faces_deleted = await remove_faces(db)
            print(f"  Deleted {faces_deleted} faces")
            await db.commit()
            print()

            # Remove photo_tags associations
            print("Removing photo-tag associations...")
            photo_tags_deleted = await remove_photo_tags_associations(db)
            print(f"  Deleted {photo_tags_deleted} associations")
            await db.commit()
            print()

            # Remove person tags
            print("Removing person tags...")
            person_tags_deleted = await remove_person_tags(db)
            print(f"  Deleted {person_tags_deleted} person tags")
            await db.commit()
            print()

            # Remove People hierarchy
            print("Removing People tag hierarchy...")
            people_deleted = await remove_people_hierarchy(db)
            print(f"  Deleted {people_deleted} tags from People hierarchy")
            await db.commit()
            print()

            # Clear XMP RegionInfo
            exiftool = _find_exiftool()
            if exiftool:
                print(f"Clearing XMP RegionInfo using exiftool: {exiftool}")
                xmp_stats = await clear_xmp_regioninfo(db, exiftool)
                print(f"  Processed: {xmp_stats['processed']}")
                print(f"  Success: {xmp_stats['success']}")
                print(f"  Errors: {xmp_stats['errors']}")
            else:
                print("WARNING: exiftool not found. Skipping XMP cleanup.")
            print()

            print("=" * 60)
            print("Deletion complete")
            print("=" * 60)


if __name__ == "__main__":
    from sqlalchemy import func
    asyncio.run(main())
