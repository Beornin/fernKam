"""Strip DigiKam/MWG face-region metadata (XMP RegionInfo) from the library.

Removes only the face bounding-box regions — XMP-mwg-rs:RegionInfo (MWG
standard) and XMP-MP:RegionInfoMP (the Microsoft-Photo-compatible struct
DigiKam actually writes in this library) — so DigiKam can run a completely
fresh face-detection pass. Any existing People/Name keyword tags (Subject /
HierarchicalSubject / Keywords) are left untouched.

Two-phase design for speed on large libraries:
  Phase 1 (scan)  - ONE exiftool process reads RegionInfo/RegionInfoMP
                    recursively under --root and returns only the files where
                    either tag is actually present. Files with nothing to
                    remove are never touched (no wasted write I/O, no mtime
                    churn).
  Phase 2 (strip) - ONE exiftool process deletes both tags from just that
                    filtered list, fed via an argfile (-@) so there's no
                    Windows command-line length limit and no per-file process
                    spawn overhead.

Usage (from backend/):
    uv run python scripts/strip_face_regions.py                     # dry run, uses settings.library_root
    uv run python scripts/strip_face_regions.py --root "D:/Pictures" # dry run, explicit root
    uv run python scripts/strip_face_regions.py --execute            # actually strip
    uv run python scripts/strip_face_regions.py --execute --backup   # keep *_original backups
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from fernkam.config import get_settings  # noqa: E402
from fernkam.media_types import IMAGE_EXTENSIONS  # noqa: E402
from fernkam.metadata_sync import _et  # noqa: E402

# Face regions only ever apply to still images + their XMP sidecars — never
# to videos. Restricting -ext to this whitelist skips every video file
# outright, which is what made the unfiltered full-library scan take so
# long (QuickTime/MP4 atom parsing is comparatively slow in exiftool).
SCAN_EXTENSIONS: list[str] = sorted(e.lstrip(".") for e in IMAGE_EXTENSIONS) + ["xmp"]


def scan_files_with_regions(exiftool: str, root: Path) -> list[str]:
    """Single-process recursive scan; returns absolute paths that have a face region.

    Streams exiftool's -progress output live to the console (inherited stderr)
    instead of blocking silently, and writes JSON to a temp file rather than a
    pipe so there's no risk of a pipe-buffer deadlock on a huge result set.
    """
    fd, out_path = tempfile.mkstemp(suffix=".json")
    os.close(fd)
    args = [exiftool, "-r", "-json", "-struct", "-fast2", "-progress", "-G1:1",
            "-XMP-mwg-rs:RegionInfo", "-XMP-MP:RegionInfoMP"]
    for ext in SCAN_EXTENSIONS:
        args += ["-ext", ext]
    args.append(str(root))
    try:
        with open(out_path, "wb") as out_fh:
            proc = subprocess.Popen(args, stdout=out_fh, stderr=None, stdin=subprocess.DEVNULL)
            proc.wait()
        with open(out_path, "r", encoding="utf-8", errors="replace") as fh:
            data = json.load(fh)
    finally:
        try:
            os.unlink(out_path)
        except OSError:
            pass
    return [
        obj["SourceFile"]
        for obj in data
        if any(k != "SourceFile" and v for k, v in obj.items())
    ]


def strip_regions(exiftool: str, files: list[str], backup: bool) -> tuple[int, int]:
    """Single-process batch delete of both RegionInfo tags from the given files."""
    if not files:
        return 0, 0

    fd, argfile_path = tempfile.mkstemp(suffix=".args")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write("-XMP-mwg-rs:RegionInfo=\n")
            fh.write("-XMP-MP:RegionInfoMP=\n")
            if not backup:
                fh.write("-overwrite_original\n")
            fh.write("-P\n")  # preserve file modification date/time
            for f in files:
                fh.write(f"{f}\n")

        fd_out, out_path = tempfile.mkstemp(suffix=".log")
        os.close(fd_out)
        try:
            with open(out_path, "wb") as out_fh:
                proc = subprocess.Popen(
                    [exiftool, "-progress", "-@", argfile_path],
                    stdout=out_fh, stderr=None, stdin=subprocess.DEVNULL,
                )
                proc.wait()
            with open(out_path, "r", encoding="utf-8", errors="replace") as fh:
                stdout = fh.read()
        finally:
            try:
                os.unlink(out_path)
            except OSError:
                pass
        updated = 0
        for line in stdout.splitlines():
            if "image files updated" in line:
                try:
                    updated = int(line.strip().split()[0])
                except (ValueError, IndexError):
                    pass
        errors = len(files) - updated
        return updated, max(0, errors)
    finally:
        try:
            os.unlink(argfile_path)
        except OSError:
            pass


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", default=None, help="Library root to scan (default: settings.library_root)")
    parser.add_argument("--execute", action="store_true", help="Actually strip regions (default is dry-run/report only)")
    parser.add_argument("--backup", action="store_true", help="Keep exiftool's *_original backup files (default: off)")
    args = parser.parse_args()

    exiftool = _et()
    if not exiftool:
        print("exiftool not found on PATH or in known install locations.", file=sys.stderr)
        sys.exit(1)

    root = Path(args.root) if args.root else Path(get_settings().library_root)
    if not root.exists():
        print(f"Root does not exist: {root}", file=sys.stderr)
        sys.exit(1)

    print(f"Scanning {root} for XMP face regions (exiftool: {exiftool}) ...")
    t0 = time.time()
    files = scan_files_with_regions(exiftool, root)
    print(f"Found {len(files):,} file(s) with a face region in {time.time() - t0:.1f}s")

    if not files:
        print("Nothing to do.")
        return

    if not args.execute:
        print("\nDry run only — no files modified. Re-run with --execute to strip regions.")
        preview = files[:20]
        for f in preview:
            print(f"  {f}")
        if len(files) > len(preview):
            print(f"  ... and {len(files) - len(preview)} more")
        return

    print(f"\nStripping RegionInfo from {len(files):,} file(s) (backup={'on' if args.backup else 'off'}) ...")
    t1 = time.time()
    updated, errors = strip_regions(exiftool, files, backup=args.backup)
    print(f"Done in {time.time() - t1:.1f}s: {updated:,} updated, {errors} errors")


if __name__ == "__main__":
    main()
