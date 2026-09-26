"""Sort phone photos and camera videos into Ordered by Dates/YYYY/MM.

Takes everything in SORT ME, plus the videos in the RAW intake folder (camera
videos skip the cull), and moves each file to <export_root>/YYYY/MM/ by the
date it was taken:
  - Google Pixel names (PXL_YYYYMMDD_...) carry the date;
  - otherwise the camera date, read by exiftool (EXIF DateTimeOriginal /
    CreateDate, QuickTime dates for videos).

A file with no camera date is not guessed from its modified time (a copy or an
edit changes that). It stays in SORT ME and is listed, so it can be dated by
hand. That listing is the check the AC_SORTED stage used to be for. A file
already at its destination (same name and size) is left in place and listed.

Files are moved, not copied. The library scan afterwards matches moved files
to their catalogue rows by content, so tags and ratings follow them.
"""
from __future__ import annotations

import re
import shutil
import time
from datetime import datetime
from pathlib import Path
from typing import Optional

from fernkam.workflows.shared import ALL_EXTENSIONS, VIDEO_EXTENSIONS, format_elapsed, gather_files

_PIXEL = re.compile(r"^PXL_(\d{4})(\d{2})\d{2}_")


def _pixel_month(name: str) -> Optional[tuple[str, str]]:
    m = _PIXEL.match(name)
    return (m.group(1), m.group(2)) if m and 1 <= int(m.group(2)) <= 12 else None


def _free_name(dest: Path, taken: set[Path]) -> Path:
    """dest, or 1_name, 2_name, ... when that name is used already."""
    n, final = 0, dest
    while final.exists() or final in taken:
        n += 1
        final = dest.parent / f"{n}_{dest.name}"
    return final


def plan(raw_dir: str, sort_me_dir: str, export_root: str,
         dates: Optional[dict[Path, Optional[datetime]]] = None):
    """Returns (moves [(src, dest)], undated [src], already_there [(src, dest)]).

    `dates` maps files to the date they were taken; read with exiftool when
    not given (tests pass it).
    """
    files = gather_files(sort_me_dir, ALL_EXTENSIONS) + gather_files(raw_dir, VIDEO_EXTENSIONS)
    if dates is None:
        from fernkam.metadata_sync import read_many_metadata
        need = [f for f in files if not _pixel_month(f.name)]
        meta = read_many_metadata(need) if need else {}
        dates = {f: (meta.get(f) or {}).get("taken_at") for f in need}

    moves, undated, already = [], [], []
    taken: set[Path] = set()
    for f in files:
        dt = dates.get(f)
        ym = _pixel_month(f.name) or (dt and (f"{dt.year:04d}", f"{dt.month:02d}"))
        if not ym:
            undated.append(f)
            continue
        dest = Path(export_root, *ym) / f.name
        if dest.exists() and dest.stat().st_size == f.stat().st_size:
            already.append((f, dest))
            continue
        dest = _free_name(dest, taken)
        taken.add(dest)
        moves.append((f, dest))
    return moves, undated, already


def run(raw_dir: str, sort_me_dir: str, export_root: str, dry_run: bool = True) -> None:
    """dry_run defaults to True so the destinations can be reviewed first."""
    start = time.perf_counter()
    moves, undated, already = plan(raw_dir, sort_me_dir, export_root)
    verb = "WOULD MOVE" if dry_run else "MOVE"
    for src, dest in moves:
        print(f"{verb}: {src.name} -> {dest.parent}")
    for f in undated:
        print(f"NO CAMERA DATE (stays in SORT ME, date it by hand): {f}")
    for src, dest in already:
        print(f"ALREADY THERE (left in place): {src} = {dest}")

    if not dry_run:
        for src, dest in moves:
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(src), str(dest))
        # Undated camera videos still leave the intake, so the next cull is clean.
        staging, taken = Path(sort_me_dir), set()
        for f in undated:
            if Path(raw_dir) in f.parents:
                staging.mkdir(parents=True, exist_ok=True)
                dest = _free_name(staging / f.name, taken)
                taken.add(dest)
                shutil.move(str(f), str(dest))

    print(f"{'DRY RUN — ' if dry_run else ''}{len(moves)} file(s) {'would be ' if dry_run else ''}moved to "
          f"{export_root}; {len(undated)} without a camera date left in SORT ME; "
          f"{len(already)} already there. ({format_elapsed(start)})")
