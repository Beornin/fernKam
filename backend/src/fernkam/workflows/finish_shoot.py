"""Finish a culled shoot: bin the rejects, file the keepers.

A shoot in the RAW intake (AA_RAW/<shoot>/x.NEF, jpg/x.jpg) after the cull:

  reject   no picture left for the RAW (its JPG was deleted), or every one of
           its pictures is labelled red (X in Review Mode). The RAW and its
           pictures go to the Recycle Bin.
  keeper   goes to the shoot's destination, unless a picture of it is labelled
           green (P: Portfolio) or blue (L: the client folder).

  portfolio  <folder>/x.jpg and <folder>/RAW/x.NEF (the promote convention)
  dates      Ordered by Dates/YYYY/MM/, RAW and JPG side by side (as Sorting
             files them), by the date the photo was taken
  client     the JPG to CLIENT_FOLDER, outside the library, so it leaves the
             catalogue. The RAW is kept and filed by date.

A RAW's pictures are the ones in its folder or jpg/ whose name contains the
RAW's (x.jpg, x-Edit.jpg), the same rule as Remove non-keep RAW. Nothing is
overwritten: a keeper whose destination already has that name, or that has
no date for a dated destination, stays in the shoot and is listed. Videos
are left for Sorting.

Files are only moved here. The library scan afterwards matches moved files to
their catalogue rows by content (tags, ratings and faces follow) and drops
the rows of binned and exported files. The P/L labels are cleared first,
because they were only instructions for this step.
"""
from __future__ import annotations

import shutil
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Optional

from fernkam.workflows.shared import PICTURE_EXTENSIONS, RAW_EXTENSIONS

RED, GREEN, BLUE = 1, 4, 5          # colour labels (lib/colorLabels.ts)
DESTINATIONS = ("dates", "portfolio", "client")


@dataclass
class Plan:
    trash: list[Path] = field(default_factory=list)
    moves: list[tuple[Path, Path]] = field(default_factory=list)
    held: list[tuple[Path, str]] = field(default_factory=list)
    counts: dict[str, int] = field(default_factory=dict)    # keepers per destination
    clear_labels: list[Path] = field(default_factory=list)  # P/L pictures to unlabel


def _files(d: Path, exts) -> list[Path]:
    return sorted(f for f in d.iterdir() if f.is_file() and f.suffix.lower() in exts) if d.is_dir() else []


def plan(shoot: Path, destination: str, *, labels: dict[Path, int], dates: dict[Path, Optional[datetime]],
         library_root: Path, archive: str, portfolio_folder: str = "", client_folder: str = "") -> Plan:
    """What Finish shoot would do. Pure: `labels` and `dates` come from the
    catalogue (see run), keyed by file path."""
    if destination not in DESTINATIONS:
        raise ValueError(f"destination must be one of {DESTINATIONS}")
    pics = _files(shoot, PICTURE_EXTENSIONS) + _files(shoot / "jpg", PICTURE_EXTENSIONS)
    raws = _files(shoot, RAW_EXTENSIONS)
    # Each picture belongs to the RAW whose name it starts with, the longest
    # such (x-Edit.jpg -> x.NEF; _DSC12.jpg -> _DSC12.NEF, not _DSC1.NEF).
    # Falls back to "contains", Remove non-keep RAW's rule, so this never bins
    # a RAW that workflow would keep.
    owner: dict[Path, Optional[Path]] = {}
    for x in pics:
        s = x.stem.lower()
        for match in (s.startswith, s.__contains__):
            cands = [r for r in raws if match(r.stem.lower())]
            if cands:
                owner[x] = max(cands, key=lambda r: len(r.stem))
                break
        else:
            owner[x] = None
    groups = [(r, [x for x in pics if owner[x] == r]) for r in raws]
    groups += [(None, [x]) for x in pics if owner[x] is None]          # a JPG with no RAW

    p = Plan()
    targets: set[Path] = set()
    for raw, mine in groups:
        files = [*mine, *([raw] if raw else [])]
        kept = [x for x in mine if labels.get(x, 0) != RED]
        if not kept:                                   # deleted or rejected in the cull
            p.trash += files
            continue
        p.trash += [x for x in mine if x not in kept]  # a rejected extra version
        marks = {labels.get(x, 0) for x in kept}
        dest = "portfolio" if GREEN in marks else "client" if BLUE in marks else destination
        when = next((d for d in [*(dates.get(x) for x in kept), dates.get(raw) if raw else None] if d), None)
        date_dir = library_root / archive / f"{when.year:04d}" / f"{when.month:02d}" if when else None
        kept_files = [*kept, *([raw] if raw else [])]

        if dest == "portfolio":
            if not portfolio_folder:
                p.held += [(f, "no Portfolio folder chosen") for f in kept_files]
                continue
            base = library_root / portfolio_folder
            pairs = [(x, base / x.name) for x in kept] + ([(raw, base / "RAW" / raw.name)] if raw else [])
        elif dest == "client":
            if not client_folder:
                p.held += [(f, "CLIENT_FOLDER is not set") for f in kept_files]
                continue
            if raw and not date_dir:
                p.held += [(f, "no date taken (its RAW is filed by date)") for f in kept_files]
                continue
            pairs = [(x, Path(client_folder) / x.name) for x in kept] + ([(raw, date_dir / raw.name)] if raw else [])
        else:
            if not date_dir:
                p.held += [(f, "no date taken") for f in kept_files]
                continue
            pairs = [(f, date_dir / f.name) for f in kept_files]

        # A keeper moves as a whole or not at all, and nothing is overwritten.
        clash = next((d for _, d in pairs if d.exists() or d in targets), None)
        if clash:
            p.held += [(f, f"{clash} already exists") for f in kept_files]
            continue
        targets.update(d for _, d in pairs)
        p.moves += pairs
        p.clear_labels += [x for x in kept if labels.get(x, 0) in (GREEN, BLUE)]
        p.counts[dest] = p.counts.get(dest, 0) + 1
    return p


def _catalogue(shoot: Path, library_root: Path) -> tuple[dict[Path, int], dict[Path, Optional[datetime]]]:
    """Labels and dates for the shoot's files; exiftool dates for any not catalogued yet."""
    from sqlalchemy import text
    from fernkam.db.session import get_sync_session_factory

    album = shoot.relative_to(library_root).as_posix()
    labels, dates = {}, {}
    with get_sync_session_factory()() as db:
        for r in db.execute(text(
                "SELECT album_path, filename, color_label, taken_at FROM photos "
                "WHERE album_path IN (:a, :j)"), {"a": album, "j": f"{album}/jpg"}):
            f = library_root / r.album_path / r.filename
            labels[f], dates[f] = r.color_label or 0, r.taken_at
    missing = [f for f in _files(shoot, RAW_EXTENSIONS | PICTURE_EXTENSIONS) + _files(shoot / "jpg", PICTURE_EXTENSIONS)
               if f not in dates]
    if missing:
        from fernkam.metadata_sync import read_many_metadata
        dates.update({f: m.get("taken_at") for f, m in read_many_metadata(missing).items()})
    return labels, dates


def _clear_labels(files: list[Path], library_root: Path) -> None:
    from sqlalchemy import text
    from fernkam.db.session import get_sync_session_factory
    with get_sync_session_factory()() as db:
        for f in files:
            db.execute(text("UPDATE photos SET color_label = 0 WHERE album_path = :a AND filename = :f"),
                       {"a": f.parent.relative_to(library_root).as_posix(), "f": f.name})
        db.commit()


def run(shoot: str, destination: str = "dates", portfolio_folder: str = "", dry_run: bool = True) -> None:
    from fernkam.config import get_settings
    s = get_settings()
    root, sh = Path(s.library_root), Path(shoot)
    if not sh.is_dir() or Path(root, s.raw_intake_folder) not in sh.parents:
        print(f"ERROR: {shoot} is not a shoot folder inside {Path(root, s.raw_intake_folder)}.")
        return
    pf = portfolio_folder.strip().strip("/\\")
    if pf and not (root / pf).resolve().is_relative_to(root.resolve()):
        print(f"ERROR: the Portfolio folder must be inside the library: {portfolio_folder}")
        return
    labels, dates = _catalogue(sh, root)
    p = plan(sh, destination, labels=labels, dates=dates, library_root=root,
             archive=s.dedup_archive_folder, portfolio_folder=pf,
             client_folder=s.client_folder)

    pre = "WOULD " if dry_run else ""
    for f in p.trash:
        print(f"{pre}BIN: {f.name}")
    for src, dest in p.moves:
        print(f"{pre}MOVE: {src.name} -> {dest.parent}")
    for f, why in p.held:
        print(f"STAYS IN THE SHOOT: {f.name} ({why})")
    others = [f for f in sh.iterdir() if f.is_file() and f.suffix.lower() not in RAW_EXTENSIONS | PICTURE_EXTENSIONS]
    if others:
        print(f"LEFT FOR SORTING: {len(others)} other file(s), e.g. {others[0].name}")

    if not dry_run:
        from send2trash import send2trash
        _clear_labels(p.clear_labels, root)
        for f in p.trash:
            send2trash(str(f))
        for src, dest in p.moves:
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(src), str(dest))
        for d in (sh / "jpg", sh):
            if d.is_dir() and not any(d.iterdir()):
                d.rmdir()

    kept = ", ".join(f"{n} to {k}" for k, n in sorted(p.counts.items())) or "none kept"
    print(f"{'DRY RUN — ' if dry_run else ''}{len(p.trash)} file(s) {'would go' if dry_run else 'went'} to the "
          f"Recycle Bin; keepers: {kept}; {len(p.held)} file(s) stay in the shoot.")
