"""Pull AA_RAW into SORT ME, then sort SORT ME into AC_SORTED/YYYY/MM.

Everything in the RAW intake folder and in SORT ME is moved by the date it
was taken:
  - a shoot (a folder in AA_RAW or SORT ME) moves whole, with its jpg/ and
    any other subfolders, to <export_root>/YYYY/MM/<shoot>/, dated by the
    month most of its files were taken in, so shoots stay apart;
  - loose files (a dump straight into AA_RAW or SORT ME) go to
    <export_root>/YYYY/MM/, each by its own date.
Dates come from Google Pixel names (PXL_YYYYMMDD_...), otherwise the camera
date read by exiftool (EXIF DateTimeOriginal / CreateDate, QuickTime dates
for videos).

Nothing is dated from its modified time (a copy or an edit changes that). A
loose file with no camera date, or a shoot where no file has one, waits in
SORT ME (one from AA_RAW is pulled there, keeping its folders) and is listed,
so it can be dated by hand. A file already at its destination (same name and
size) is left in place and listed. Folders the moves empty are removed.

Files are moved, not copied. The library scan afterwards matches moved files
to their catalogue rows by content, so tags and ratings follow them.
"""
from __future__ import annotations

import re
import shutil
import time
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path
from typing import Optional

from fernkam.workflows.shared import ALL_EXTENSIONS, format_elapsed, gather_files

_PIXEL = re.compile(r"^PXL_(\d{4})(\d{2})\d{2}_")


def _pixel_month(name: str) -> Optional[tuple[str, str]]:
    m = _PIXEL.match(name)
    return (m.group(1), m.group(2)) if m and 1 <= int(m.group(2)) <= 12 else None


def _free_name(dest: Path, taken: set[Path]) -> Path:
    """dest, or 1_name, 2_name, ... when that name is used already."""
    # ponytail: renames per file; a RAW and its JPG stay paired only because
    # keepers clash as pairs. Rename by stem if lone clashes ever show up.
    n, final = 0, dest
    while final.exists() or final in taken:
        n += 1
        final = dest.parent / f"{n}_{dest.name}"
    return final


def plan(raw_dir: str, sort_me_dir: str, export_root: str,
         dates: Optional[dict[Path, Optional[datetime]]] = None):
    """Returns (moves, undated, already_there), each a list of (src, dest).

    An undated file's dest is where it waits in SORT ME: itself, or for one
    from AA_RAW, the same path under SORT ME. `dates` maps files to the date
    they were taken; read with exiftool when not given (tests pass it).
    """
    raw, sort_me = Path(raw_dir), Path(sort_me_dir)
    files = ([(sort_me, f) for f in gather_files(sort_me_dir, ALL_EXTENSIONS)]
             + [(raw, f) for f in gather_files(raw_dir, ALL_EXTENSIONS)])
    if dates is None:
        from fernkam.metadata_sync import read_many_metadata
        need = [f for _, f in files if not _pixel_month(f.name)]
        meta = read_many_metadata(need) if need else {}
        dates = {f: (meta.get(f) or {}).get("taken_at") for f in need}

    def month(f: Path) -> Optional[tuple[str, str]]:
        dt = dates.get(f)
        return _pixel_month(f.name) or (dt and (f"{dt.year:04d}", f"{dt.month:02d}"))

    shoot_months: dict[Path, Counter] = defaultdict(Counter)
    for root, f in files:
        rel = f.relative_to(root)
        if len(rel.parts) > 1 and (m := month(f)):
            shoot_months[root / rel.parts[0]][m] += 1

    moves, undated, already = [], [], []
    taken: set[Path] = set()
    for root, f in files:
        rel = f.relative_to(root)
        if len(rel.parts) > 1:      # in a shoot: it moves with the shoot
            c = shoot_months.get(root / rel.parts[0])
            ym = c.most_common(1)[0][0] if c else None
        else:
            ym = month(f)
        if not ym:
            dest = f if root == sort_me else _free_name(sort_me / rel, taken)
            taken.add(dest)
            undated.append((f, dest))
            continue
        dest = Path(export_root, *ym) / rel
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
    pulls = [(src, dest) for src, dest in undated if src != dest]
    verb = "WOULD MOVE" if dry_run else "MOVE"
    for src, dest in moves:
        print(f"{verb}: {src.name} -> {dest.parent}")
    for src, dest in undated:
        print(f"NO CAMERA DATE (waits in SORT ME, date it by hand): {dest}")
    for src, dest in already:
        print(f"ALREADY THERE (left in place): {src} = {dest}")

    if not dry_run:
        for src, dest in moves + pulls:
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(src), str(dest))
        roots = {Path(raw_dir), Path(sort_me_dir)}
        for d in sorted({src.parent for src, _ in moves + pulls}, key=lambda p: -len(p.parts)):
            while d not in roots:
                try:
                    d.rmdir()   # only succeeds on an empty folder
                except OSError:
                    break
                d = d.parent

    print(f"{'DRY RUN — ' if dry_run else ''}{len(moves)} file(s) {'would be ' if dry_run else ''}moved to "
          f"{export_root}; {len(undated)} without a camera date in SORT ME "
          f"({len(pulls)} pulled from {raw_dir}); {len(already)} already there. ({format_elapsed(start)})")
