"""
Port of RemoveNonKeepRAWWorkflow.java

Walks a folder tree and moves any NEF file to the Trash when no matching
JPG exists in the same directory or that directory's jpg/ subfolder.
Matching is a substring check: the NEF stem must appear inside a JPG stem.
"""

import os
import time
from collections import defaultdict
from pathlib import Path

try:
    from send2trash import send2trash
    _SEND2TRASH_AVAILABLE = True
except ImportError:
    _SEND2TRASH_AVAILABLE = False
    print("Warning: send2trash not installed. Run: pip install send2trash")

from fernkam.workflows.shared import ALL_EXTENSIONS, format_elapsed

DEFAULT_STARTING_FOLDER = r"D:\Pictures and Videos\AA_RAW"


def run(starting_folder: str = DEFAULT_STARTING_FOLDER) -> None:
    start = time.perf_counter()
    print(f"Loading files from: {starting_folder}")

    all_files = _gather_files(starting_folder)

    # Build per-directory index of JPG base names.
    jpg_by_dir: dict = defaultdict(set)
    for f in all_files:
        if f.suffix.lower() in {".jpg", ".jpeg"}:
            jpg_by_dir[f.parent].add(_base_name(f.name))

    nef_files = [f for f in all_files if f.suffix.lower() == ".nef"]
    to_delete = []
    for f in nef_files:
        nef_stem = _base_name(f.name)
        # Allowed JPG directories: same folder, or a jpg/ subfolder of it.
        candidates: set = jpg_by_dir.get(f.parent, set()) | jpg_by_dir.get(f.parent / "jpg", set())
        if not any(nef_stem in jpg for jpg in candidates):
            to_delete.append(f)

    print(f"Found {len(nef_files)} NEF files. {len(to_delete)} have no matching JPG.")

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


def _gather_files(directory: str) -> list:
    result = []
    for root, _, filenames in os.walk(directory):
        for name in filenames:
            p = Path(root) / name
            if p.suffix.lower() in ALL_EXTENSIONS:
                try:
                    if p.stat().st_size > 0:
                        result.append(p)
                except OSError:
                    pass
    return result


def _base_name(filename: str) -> str:
    return Path(filename).stem.lower()


if __name__ == "__main__":
    run()
