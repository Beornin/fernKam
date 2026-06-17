"""
Port of SortingVideoWorkflow.java

Steps:
1. Move video files from raw_dir into sort_me_dir (staging).
2. Walk sort_me_dir, read date from EXIF / filename, copy each file into
   export_root/<YYYY>/<MM>/<filename>.
   - Google Pixel filenames (PXL_YYYYMMDD...) are parsed directly.
   - Other files use EXIF DateTimeOriginal; file-modified-date as fallback.
   - Files with no determinable date land in export_root directly.
"""

import datetime
import os
import shutil
import time
from pathlib import Path
from typing import Optional

try:
    import exifread
    _EXIFREAD_AVAILABLE = True
except ImportError:
    _EXIFREAD_AVAILABLE = False
    print("Warning: exifread not installed. EXIF date extraction unavailable. "
          "Run: pip install exifread")

from fernkam.workflows.shared import ALL_EXTENSIONS, PICTURE_EXTENSIONS, RAW_EXTENSIONS, VIDEO_EXTENSIONS, format_elapsed

DEFAULT_RAW_DIR     = r"D:\Pictures and Videos\AA_RAW"
DEFAULT_SORT_ME_DIR = r"D:\Pictures and Videos\AB_TO_SORT\SORT ME"
DEFAULT_EXPORT_ROOT = r"D:\Pictures and Videos\AC_SORTED"


def run(raw_dir: str = DEFAULT_RAW_DIR,
        sort_me_dir: str = DEFAULT_SORT_ME_DIR,
        export_root: str = DEFAULT_EXPORT_ROOT) -> None:
    _video_moving(raw_dir, sort_me_dir)

    start = time.perf_counter()
    files = _gather_files(sort_me_dir, ALL_EXTENSIONS)
    print(f"Sorting {len(files)} items...")

    for i, file_path in enumerate(files):
        if (i + 1) % 10 == 0:
            print(f"Progress: {i + 1} / {len(files)}")
        try:
            dest_dir = _resolve_destination(file_path, export_root)
            dest_dir.mkdir(parents=True, exist_ok=True)
            _safe_copy(file_path, dest_dir / file_path.name)
        except Exception as e:
            print(f"Error processing {file_path.name}: {e}")

    print(f"Total process took: {format_elapsed(start)}")


def _video_moving(raw_dir: str, sort_me_dir: str) -> None:
    start = time.perf_counter()
    files = _gather_files(raw_dir, VIDEO_EXTENSIONS)
    staging = Path(sort_me_dir)
    staging.mkdir(parents=True, exist_ok=True)
    for f in files:
        _safe_copy(f, staging / f.name)
    print(f"Video move took: {format_elapsed(start)} ({len(files)} files)")


def _gather_files(directory: str, extensions: set) -> list:
    result = []
    for root, _, filenames in os.walk(directory):
        for name in filenames:
            p = Path(root) / name
            if p.suffix.lower() in extensions:
                try:
                    if p.stat().st_size > 0:
                        result.append(p)
                except OSError:
                    pass
    return result


def _resolve_destination(file_path: Path, export_root: str) -> Path:
    name = file_path.name

    # Case A: Google Pixel format  PXL_YYYYMMDD...
    if name.startswith("PXL_") and len(name) >= 12:
        year  = name[4:8]
        month = name[8:10]
        return Path(export_root, year, month)

    # Case B: EXIF or file-system date → "YYYY-MM"
    date_str = _get_date(file_path)
    if date_str:
        parts = date_str.split("-")
        if len(parts) >= 2:
            return Path(export_root, parts[0], parts[1])

    # Case C: fallback
    return Path(export_root)


def _get_date(file_path: Path) -> Optional[str]:
    ext = file_path.suffix.lower()
    fmt = "%Y-%m"

    # EXIF for images and RAW files
    if ext in (PICTURE_EXTENSIONS | RAW_EXTENSIONS) and _EXIFREAD_AVAILABLE:
        try:
            with open(file_path, "rb") as f:
                tags = exifread.process_file(f, stop_tag="EXIF DateTimeOriginal", details=False)
            tag = tags.get("EXIF DateTimeOriginal") or tags.get("Image DateTime")
            if tag:
                dt = datetime.datetime.strptime(str(tag), "%Y:%m:%d %H:%M:%S")
                return dt.strftime(fmt)
        except Exception:
            pass

    # Fallback: file modification time (works for videos too)
    try:
        mtime = os.path.getmtime(file_path)
        dt = datetime.datetime.fromtimestamp(mtime)
        return dt.strftime(fmt)
    except Exception:
        return None


def _safe_copy(src: Path, dst: Path) -> None:
    """Copy src to dst. If dst already exists, prefix the name with an incrementing number."""
    final = dst
    count = 0
    while final.exists():
        final = dst.parent / f"{count}_{dst.name}"
        count += 1
    shutil.copy2(src, final)


if __name__ == "__main__":
    run()
