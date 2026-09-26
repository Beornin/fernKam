"""Develop RAWs into JPGs with DxO PureRAW, without opening it.

PureRAW has no documented command line, but its Lightroom plugin starts it
with one, and fernKam does the same:

    PureRAWv6.exe --as-lightroom-last-settings-plugin --lr-version=14.5
                  --batch-file <text file, one RAW path per line>

It processes the listed RAWs with the settings last used in *plugin* mode
(separate from the app's own), writes each result beside its RAW, deletes the
batch file and exits. `preview=True` opens PureRAW's settings window first,
which is how that profile is set. Without --lr-version it refuses with a
"Sorry!" dialog.

Output convention (Nikon intake): AA_RAW/<shoot>/x.NEF -> AA_RAW/<shoot>/jpg/x.jpg.
Plugin mode ignores PureRAW's subfolder setting, so the JPG is moved into jpg/
here. Anything else it writes (a DNG, a renamed file) means its settings
changed. The run then reports that and stops, rather than filing the wrong files.
"""
from __future__ import annotations

import os
import subprocess
import tempfile
import time
from collections import defaultdict
from pathlib import Path
from typing import Callable, Optional

from fernkam.config import get_settings
from fernkam.task_manager import fmt_eta
from fernkam.workflows.remove_nonkeep_raw import select_orphans
from fernkam.workflows.shared import PICTURE_EXTENSIONS, RAW_EXTENSIONS, gather_files

LR_VERSION = "14.5"          # PureRAW only checks that a version is given
QUIET_SECONDS = 120          # a shoot with a file newer than this is still being copied in
STALL_SECONDS = 15 * 60      # no new JPG for this long: PureRAW is stuck (an error dialog, say)
SECONDS_PER_RAW = 13         # PureRAW's own estimate for a 46 MP NEF on the RTX 3090


def plan(root: Path, whole_intake: bool, now: Optional[float] = None) -> tuple[dict[Path, list[Path]], list[str]]:
    """RAWs to develop, per shoot folder, and the reasons others were skipped.

    With `whole_intake` (the intake folder, or the automatic run) only *fresh*
    shoots are developed: no pictures yet, and nothing copied in for
    QUIET_SECONDS. A shoot that already has JPGs is being culled, and its RAWs
    without a JPG are mostly rejects. Developing those again would bring back
    what was just deleted. Picking that shoot on its own develops them anyway.
    """
    now = time.time() if now is None else now
    files = gather_files(str(root), PICTURE_EXTENSIONS | RAW_EXTENSIONS)
    pictures: dict[Path, set[str]] = defaultdict(set)
    raws: dict[Path, list[Path]] = defaultdict(list)
    for f in files:
        if f.suffix.lower() in PICTURE_EXTENSIONS:
            pictures[f.parent].add(f.stem.lower())
        else:
            raws[f.parent].append(f)

    todo, skipped = {}, []
    for shoot, rs in sorted(raws.items()):
        pending = select_orphans(sorted(rs), pictures)
        if not pending:
            continue
        if whole_intake:
            has_pictures = pictures.get(shoot) or pictures.get(shoot / "jpg")
            if has_pictures:
                skipped.append(f"{shoot.name}: already has JPGs (being culled); "
                               f"pick this shoot on its own to develop its {len(pending)} other RAW(s)")
                continue
            newest = max(p.stat().st_ctime for p in rs)
            if now - newest < QUIET_SECONDS:
                skipped.append(f"{shoot.name}: files still arriving; it will be developed once copying stops")
                continue
        todo[shoot] = pending
    return todo, skipped


_IMAGE_OUT = {".jpg", ".jpeg", ".dng", ".tif", ".tiff"}


def wrong_outputs(todo: dict[Path, list[Path]], before: dict[Path, set[str]]) -> list[Path]:
    """New image files that aren't '<RAW name>.jpg': PureRAW's profile has
    changed (a DNG, a renamed JPG). Other new files (temporary ones) are ignored."""
    bad = []
    for shoot, rs in todo.items():
        want = {f"{r.stem}.jpg".lower() for r in rs}
        bad += [shoot / n for n in set(os.listdir(shoot)) - before[shoot]
                if Path(n).suffix.lower() in _IMAGE_OUT and n.lower() not in want and (shoot / n).is_file()]
    return bad


def collect(todo: dict[Path, list[Path]], before: dict[Path, set[str]]) -> tuple[int, list[str], list[str]]:
    """Move each new <stem>.jpg into jpg/. Returns (developed, missing, unexpected files)."""
    developed, missing, unexpected = 0, [], []
    for shoot, rs in todo.items():
        new = {n for n in set(os.listdir(shoot)) - before[shoot] if (shoot / n).is_file()}
        by_lower = {n.lower(): n for n in new}
        for r in rs:
            want = f"{r.stem}.jpg".lower()
            if want in by_lower:
                name = by_lower.pop(want)
                (shoot / "jpg").mkdir(exist_ok=True)
                (shoot / name).replace(shoot / "jpg" / name)
                developed += 1
            elif (shoot / "jpg" / f"{r.stem}.jpg").exists():
                developed += 1      # PureRAW honoured the subfolder after all
            else:
                missing.append(r.name)
        unexpected += [str(shoot / n) for n in by_lower.values()]
    return developed, missing, unexpected


def _developed(todo: dict[Path, list[Path]]) -> int:
    return sum(1 for shoot, rs in todo.items() for r in rs
               if (shoot / f"{r.stem}.jpg").exists() or (shoot / "jpg" / f"{r.stem}.jpg").exists())


def run(folder: str, dry_run: bool = True, preview: bool = False,
        progress: Optional[Callable[[str], None]] = None) -> None:
    s = get_settings()
    root = Path(folder)
    intake = Path(s.library_root) / s.raw_intake_folder
    todo, skipped = plan(root, whole_intake=root.resolve() == intake.resolve())
    for why in skipped:
        print(f"SKIP {why}")
    total = sum(len(rs) for rs in todo.values())
    for shoot, rs in todo.items():
        print(f"{'WOULD DEVELOP' if dry_run else 'DEVELOP'}: {len(rs)} RAW(s) in {shoot}")
    if not total:
        print("Nothing to develop.")
        return
    if dry_run:
        print(f"DRY RUN — {total} RAW(s) would be developed (about {fmt_eta(total * SECONDS_PER_RAW)} on the GPU).")
        return

    exe = Path(s.pureraw_exe)
    if not exe.is_file():
        print(f"ERROR: DxO PureRAW not found at {exe}. Set PURERAW_EXE in backend/.env.")
        return
    before = {shoot: set(os.listdir(shoot)) for shoot in todo}
    tmp = Path(tempfile.gettempdir())
    batch = tmp / f"fernkam-pureraw-{os.getpid()}.txt"
    batch.write_text("".join(f"{r}\n" for rs in todo.values() for r in rs), encoding="utf-8")
    # Lightroom writes this too; PureRAW reads and deletes it.
    (tmp / "tmpCollectionFile").write_text("UserIsNotInCollection", encoding="ascii")

    mode = "--as-lightroom-preview-plugin" if preview else "--as-lightroom-last-settings-plugin"
    proc = subprocess.Popen([str(exe), mode, f"--lr-version={LR_VERSION}", "--batch-file", str(batch)])
    print(f"PureRAW started on {total} RAW(s){' — set the profile in its window, then Process' if preview else ''}.")
    done, changed_at = -1, time.monotonic()
    while proc.poll() is None:
        time.sleep(5)
        # Stop at the first wrong file. Waiting for the end once cost 209 DNGs
        # (38.6 GB) when PureRAW's plugin settings had reverted to its default.
        if wrong_outputs(todo, before):
            proc.kill()
            break
        n = _developed(todo)
        if n != done:
            done, changed_at = n, time.monotonic()
            if progress:
                progress(f"PureRAW: {n:,}/{total:,} developed")
        elif not preview and time.monotonic() - changed_at > STALL_SECONDS:
            proc.kill()
            print(f"ERROR: PureRAW made no progress for {STALL_SECONDS // 60} min and was stopped "
                  f"(an error dialog in its window?).")
            break

    developed, missing, unexpected = collect(todo, before)
    print(f"Developed {developed} of {total} RAW(s) into jpg/.")
    if unexpected:
        print(f"ERROR: PureRAW wrote {len(unexpected)} file(s) that aren't '<RAW name>.jpg', e.g. "
              f"{Path(unexpected[0]).name}. Its plugin settings changed: run again with "
              f"'Open PureRAW's settings first' and choose JPG, no renaming.")
    elif missing:
        print(f"ERROR: {len(missing)} RAW(s) got no JPG, e.g. {', '.join(missing[:3])}. "
              f"Check PureRAW's log, or run this shoot again.")
