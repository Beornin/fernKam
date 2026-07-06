"""
Move stray RAW files into their album's RAW/ subfolder.

Walks the library (or a starting folder), finds RAW-extension files that are
NOT already inside a RAW/ subfolder, and moves them into
`<their album>/RAW/`, updating the matching `photos.album_path` in the DB so
the catalog stays in sync. Runs immediately (no preview) — see the
raw-jpg-stacks plan for the rationale.
"""
from __future__ import annotations

import os
import time
from pathlib import Path
from typing import Optional

from fernkam.config import get_settings
from fernkam.media_types import RAW_EXTENSIONS
from fernkam.workflows.shared import format_elapsed


def _to_album_path(rel_parent: Path) -> str:
    """Match the convention used by importers/filesystem.py: no leading slash,
    '/' for the library root."""
    s = str(rel_parent).replace("\\", "/")
    return "/" if s == "." else s


def run(starting_folder: Optional[str] = None) -> None:
    start = time.perf_counter()
    settings = get_settings()
    library_root = Path(settings.library_root)
    root = Path(starting_folder) if starting_folder else library_root
    print(f"Scanning for stray RAW files under: {root}")

    from fernkam.db.models.photos import Photo
    from fernkam.db.session import get_sync_session_factory
    from sqlalchemy import update

    SessionLocal = get_sync_session_factory()

    moved = 0
    skipped = 0
    errors = 0

    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if not d.startswith(".")]
        dir_path = Path(dirpath)
        if dir_path.name.upper() == "RAW":
            continue  # already inside a RAW folder

        raw_files = [f for f in filenames if Path(f).suffix.lower() in RAW_EXTENSIONS]
        if not raw_files:
            continue

        dest_dir = dir_path / "RAW"
        try:
            old_album_path = _to_album_path(dir_path.relative_to(library_root))
        except ValueError:
            old_album_path = None
        new_album_path = (
            (old_album_path.rstrip("/") + "/RAW") if old_album_path and old_album_path != "/" else "RAW"
        )

        for filename in raw_files:
            src = dir_path / filename
            dest = dest_dir / filename
            if dest.exists():
                print(f"SKIP (destination already exists): {src}")
                skipped += 1
                continue

            try:
                dest_dir.mkdir(parents=True, exist_ok=True)
                src.replace(dest)
            except OSError as e:
                print(f"ERROR moving {src}: {e}")
                errors += 1
                continue

            print(f"Moved: {src} -> {dest}")
            moved += 1

            if old_album_path is not None:
                try:
                    with SessionLocal() as session:
                        session.execute(
                            update(Photo)
                            .where(Photo.album_path == old_album_path, Photo.filename == filename)
                            .values(album_path=new_album_path)
                        )
                        session.commit()
                except Exception as e:
                    print(f"WARNING: DB update failed for {filename}: {e}")

    print(f"Done: {moved} moved, {skipped} skipped, {errors} errors.")
    print(f"Process took: {format_elapsed(start)}")


if __name__ == "__main__":
    run()
