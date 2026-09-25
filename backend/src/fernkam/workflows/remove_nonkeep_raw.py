"""
Port of RemoveNonKeepRAWWorkflow.java

Walks a folder tree and moves any RAW file to the Trash when no matching
picture (JPG/TIF/etc.) exists where fernKam keeps them: the same directory,
its jpg/ subfolder, or, for a RAW inside a RAW/ folder (the Portfolio
convention, see promote_destination), the parent directory and its jpg/.
Matching is a substring check: the RAW stem must appear inside a picture stem.
"""

import time
from collections import defaultdict
from pathlib import Path

try:
    from send2trash import send2trash
    _SEND2TRASH_AVAILABLE = True
except ImportError:
    _SEND2TRASH_AVAILABLE = False
    print("Warning: send2trash not installed. Run: pip install send2trash")

from fernkam.media_types import ALL_EXTENSIONS, PICTURE_EXTENSIONS, RAW_EXTENSIONS
from fernkam.workflows.shared import format_elapsed, gather_files

DEFAULT_STARTING_FOLDER = r"D:\Pictures and Videos\AA_RAW"


def run(starting_folder: str = DEFAULT_STARTING_FOLDER, dry_run: bool = True) -> None:
    """Trash RAW files that have no matching picture.

    `dry_run` defaults to True: this deletes originals, so the caller has to ask
    for it explicitly rather than getting it by forgetting a flag.
    """
    start = time.perf_counter()
    print(f"Loading files from: {starting_folder}")

    all_files = gather_files(starting_folder, ALL_EXTENSIONS)

    # Build per-directory index of picture base names.
    jpg_by_dir: dict = defaultdict(set)
    for f in all_files:
        if f.suffix.lower() in PICTURE_EXTENSIONS:
            jpg_by_dir[f.parent].add(_base_name(f.name))

    nef_files = [f for f in all_files if f.suffix.lower() in RAW_EXTENSIONS]
    to_delete = select_orphans(nef_files, jpg_by_dir)

    print(f"Found {len(nef_files)} RAW files. {len(to_delete)} have no matching picture.")

    if dry_run:
        print("DRY RUN — nothing was deleted. These would be moved to Trash:")
        freed = 0
        for f in to_delete:
            try:
                freed += f.stat().st_size
            except OSError:
                pass
            print(f"  WOULD TRASH: {f}")
        print(f"DRY RUN complete: {len(to_delete)} file(s), {freed / 1e9:.2f} GB would be freed.")
        print(f"Process took: {format_elapsed(start)}")
        return

    if not _SEND2TRASH_AVAILABLE:
        print("send2trash unavailable — cannot move files to Trash. Install with: pip install send2trash")
        return

    for f in to_delete:
        print(f"Moving to trash: {f.name}")
        try:
            send2trash(str(f))
        except Exception as e:
            print(f"Failed to trash {f.name}: {e}")

    print(f"Process took: {format_elapsed(start)}")


def select_orphans(raw_files: list, pictures_by_dir: dict) -> list:
    """RAWs with no matching picture in any folder a picture may live in."""
    orphans = []
    for f in raw_files:
        d = f.parent
        dirs = [d, d / "jpg"]
        if d.name.upper() == "RAW":   # Portfolio: Album/RAW/x.NEF beside Album/x.jpg
            dirs += [d.parent, d.parent / "jpg"]
        stem = _base_name(f.name)
        if not any(stem in pic for x in dirs for pic in pictures_by_dir.get(x, ())):
            orphans.append(f)
    return orphans


def _base_name(filename: str) -> str:
    return Path(filename).stem.lower()


if __name__ == "__main__":
    run()
