"""Bidirectional metadata sync between image files (XMP) and the database.

DigiKam parity:
  - DB → File  : write tags, rating, caption, face regions to XMP/sidecar
  - File → DB  : read XMP from file and update DB (pick up external edits)
  - Conflict resolution: last-writer-wins by default; optionally prefer DB or File

File format (DigiKam-compatible XMP):
  - XMP:Subject / XMP:HierarchicalSubject (tags)
  - XMP:Rating, XMP:Label (rating, color label)
  - XMP:Title, XMP:Description (title, caption)
  - XMP-mwg-rs:RegionInfo (face regions, MWG standard)
  - IPTC:Keywords (tags, for Lightroom compat)
"""
from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

_EXIFTOOL_PATHS = [
    r"C:\Program Files\digiKam\exiftool.exe",
    r"C:\Program Files (x86)\digiKam\exiftool.exe",
    "/usr/bin/exiftool",
    "/usr/local/bin/exiftool",
]

COLOR_LABEL_FROM_NAME: dict[str, int] = {
    "red": 1, "orange": 2, "yellow": 3, "green": 4,
    "blue": 5, "purple": 6, "gray": 7, "grey": 7,
}
COLOR_LABEL_TO_NAME: dict[int, str] = {
    1: "Red", 2: "Orange", 3: "Yellow", 4: "Green",
    5: "Blue", 6: "Purple", 7: "Gray",
}


def _et() -> Optional[str]:
    et = shutil.which("exiftool")
    if et:
        return et
    for p in _EXIFTOOL_PATHS:
        if Path(p).exists():
            return p
    return None


def _run_et(args: list[str], timeout: int = 120) -> Optional[dict]:
    """Run exiftool and return parsed JSON output, or None on error."""
    et = _et()
    if not et:
        logger.warning("exiftool not found")
        return None
    try:
        result = subprocess.run([et, "-json", "-struct", *args],
                                capture_output=True, stdin=subprocess.DEVNULL,
                                timeout=timeout)
        if result.returncode != 0:
            logger.warning("exiftool error: %s", result.stderr.decode(errors="replace"))
            return None
        data = json.loads(result.stdout.decode("utf-8", errors="replace"))
        return data[0] if data else {}
    except Exception as exc:
        logger.warning("exiftool exception: %s", exc)
        return None


async def read_file_metadata_async(file_path: Path) -> dict:
    """Async wrapper - runs blocking exiftool in thread executor."""
    import asyncio
    from functools import partial
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, read_file_metadata, file_path)


# ═══════════════════════════ READ FROM FILE ══════════════════════════════════

def read_file_metadata(file_path: Path) -> dict:
    """Read all relevant metadata from a file via exiftool.

    Returns a dict with keys:
      tags: list[str]
      tag_paths: list[str]   (hierarchical, pipe-separated)
      rating: int | None
      color_label: int | None
      title: str | None
      caption: str | None
      faces: list[{name, cx, cy, nw, nh}]  (normalized MWG coords)
      img_w: int | None
      img_h: int | None
      file_mtime: datetime
    """
    meta = _run_et([
        "-n",  # numeric output (no unit strings)
        # Image info
        "-ImageWidth", "-ImageHeight", "-Orientation", "-ColorComponents",
        # Dates
        "-DateTimeOriginal", "-CreateDate", "-MediaCreateDate",
        # GPS
        "-GPSLatitude", "-GPSLongitude", "-GPSAltitude",
        # File info
        "-FileSize", "-MIMEType",
        # Camera/lens
        "-Make", "-Model", "-SerialNumber",
        "-LensMake", "-LensModel", "-LensInfo",
        # Exposure
        "-ExposureTime", "-FNumber", "-ISO", "-FocalLength", "-FocalLengthIn35mmFormat",
        "-ShutterSpeedValue", "-ApertureValue", "-ExposureBiasValue",
        "-Flash", "-WhiteBalance", "-ExposureMode", "-ExposureProgram",
        "-MeteringMode", "-SceneCaptureType",
        # Color / quality
        "-ColorSpace", "-BitsPerSample",
        # XMP/IPTC tags, rating, caption
        "-Subject", "-HierarchicalSubject", "-Rating", "-Label",
        "-Title", "-Description", "-Caption-Abstract",
        "-XPTitle", "-ImageDescription",
        # Face regions (MWG)
        "-struct", "-RegionInfo",
        str(file_path),
    ])
    if meta is None:
        return {}

    # Tags
    subj = meta.get("Subject") or []
    if isinstance(subj, str):
        subj = [subj]
    hier = meta.get("HierarchicalSubject") or []
    if isinstance(hier, str):
        hier = [hier]

    # Rating
    rating_raw = meta.get("Rating")
    rating = int(rating_raw) if rating_raw is not None else None

    # Color label
    label_raw = (meta.get("Label") or "").lower()
    color_label = COLOR_LABEL_FROM_NAME.get(label_raw)

    # Face regions (MWG)
    faces = []
    region_info = meta.get("RegionInfo") or {}
    region_list = region_info.get("RegionList") or []
    for r in region_list:
        area = r.get("Area") or {}
        faces.append({
            "name": r.get("Name", ""),
            "type": r.get("Type", "Face"),
            "cx": float(area.get("X", 0)),
            "cy": float(area.get("Y", 0)),
            "nw": float(area.get("W", 0)),
            "nh": float(area.get("H", 0)),
        })

    file_mtime = datetime.fromtimestamp(file_path.stat().st_mtime, tz=timezone.utc)

    # Parse taken_at from EXIF date fields
    taken_at = None
    for date_field in ["DateTimeOriginal", "CreateDate", "MediaCreateDate", "TrackCreateDate"]:
        raw = meta.get(date_field)
        if raw:
            try:
                taken_at = datetime.strptime(str(raw)[:19], "%Y:%m:%d %H:%M:%S").replace(tzinfo=timezone.utc)
                break
            except (ValueError, TypeError):
                pass

    # File size: try exiftool first, then stat
    file_size_raw = meta.get("FileSize")
    file_size = int(file_size_raw) if file_size_raw is not None else None
    if file_size is None:
        try:
            file_size = file_path.stat().st_size
        except OSError:
            pass

    # Camera make/model
    camera_make = meta.get("Make") or meta.get("DeviceManufacturer")
    camera_model = meta.get("Model") or meta.get("DeviceModelName")
    camera_serial = meta.get("SerialNumber") or meta.get("CameraSerialNumber")
    camera_info = {"make": camera_make, "model": camera_model, "serial": str(camera_serial) if camera_serial is not None else None} if (camera_make or camera_model) else None

    # Lens make/model
    lens_make = meta.get("LensMake")
    lens_model = meta.get("LensModel") or meta.get("Lens") or meta.get("LensInfo")
    lens_info = {"make": lens_make, "model": str(lens_model) if lens_model else None} if (lens_make or lens_model) else None

    # Build structured exif snapshot (omit large/binary fields)
    _SKIP = {"SourceFile", "ExifToolVersion", "FilePermissions", "ThumbnailImage",
             "PreviewImage", "JpgFromRaw", "OtherImage", "ICC_Profile"}
    exif_dump = {k: v for k, v in meta.items() if k not in _SKIP and not isinstance(v, (bytes, bytearray))}

    return {
        "tags": list(subj),
        "tag_paths": [h.replace("|", "/") for h in hier],
        "rating": rating,
        "color_label": color_label,
        "title": meta.get("Title") or meta.get("XPTitle"),
        "caption": meta.get("Description") or meta.get("ImageDescription") or meta.get("Caption-Abstract"),
        "faces": faces,
        "img_w": meta.get("ImageWidth") or meta.get("ExifImageWidth"),
        "img_h": meta.get("ImageHeight") or meta.get("ExifImageHeight"),
        "width": meta.get("ImageWidth") or meta.get("ExifImageWidth"),
        "height": meta.get("ImageHeight") or meta.get("ExifImageHeight"),
        "file_size": file_size,
        "taken_at": taken_at,
        "latitude": meta.get("GPSLatitude"),
        "longitude": meta.get("GPSLongitude"),
        "altitude": meta.get("GPSAltitude"),
        "orientation": meta.get("Orientation"),
        "camera": camera_info,
        "lens": lens_info,
        "exif": exif_dump,
        "file_mtime": file_mtime,
    }


# ═══════════════════════════ WRITE TO FILE ═══════════════════════════════════

def write_file_metadata(
    file_path: Path,
    *,
    tags: Optional[list[str]] = None,
    tag_paths: Optional[list[str]] = None,
    rating: Optional[int] = None,
    color_label: Optional[int] = None,
    title: Optional[str] = None,
    caption: Optional[str] = None,
    faces: Optional[list[dict]] = None,   # {name, x, y, w, h} pixel coords
    img_w: Optional[int] = None,
    img_h: Optional[int] = None,
) -> bool:
    """Write metadata to file via exiftool. Returns True on success."""
    et = _et()
    if not et:
        return False

    payload: dict = {"SourceFile": str(file_path)}

    if tags is not None:
        payload["Subject"] = tags
        payload["Keywords"] = tags
        if tag_paths:
            payload["HierarchicalSubject"] = [t.replace("/", "|") for t in tag_paths]

    if rating is not None:
        payload["Rating"] = max(0, min(5, rating))
        payload["RatingPercent"] = max(0, min(5, rating)) * 20

    if color_label is not None:
        payload["Label"] = COLOR_LABEL_TO_NAME.get(color_label, "")

    if title is not None:
        payload["Title"] = title

    if caption is not None:
        payload["Description"] = caption
        payload["Caption-Abstract"] = caption

    if faces is not None and img_w and img_h:
        region_list = []
        for f in faces:
            if None in (f.get("x"), f.get("y"), f.get("w"), f.get("h")):
                continue
            cx = round((f["x"] + f["w"] / 2) / img_w, 6)
            cy = round((f["y"] + f["h"] / 2) / img_h, 6)
            nw = round(f["w"] / img_w, 6)
            nh = round(f["h"] / img_h, 6)
            entry: dict = {
                "Area": {"X": cx, "Y": cy, "W": nw, "H": nh, "Unit": "normalized"},
                "Type": "Face",
            }
            if f.get("name"):
                entry["Name"] = f["name"]
            region_list.append(entry)
        payload["RegionInfo"] = {
            "AppliedToDimensions": {"W": img_w, "H": img_h, "Unit": "pixel"},
            "RegionList": region_list,
        }

    tmp_fd, tmp_path = tempfile.mkstemp(suffix=".json")
    try:
        with os.fdopen(tmp_fd, "w", encoding="utf-8") as fh:
            json.dump([payload], fh, ensure_ascii=False)

        result = subprocess.run(
            [et, "-overwrite_original", f"-json={tmp_path}", str(file_path)],
            capture_output=True, timeout=60,
        )
        if result.returncode != 0:
            logger.warning("exiftool write failed for %s: %s",
                           file_path.name,
                           result.stderr.decode(errors="replace"))
            return False
        return True
    except Exception as exc:
        logger.warning("exiftool write exception for %s: %s", file_path.name, exc)
        return False
    finally:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass


# ═══════════════════════════ DB ↔ FILE SYNC ══════════════════════════════════

class SyncResult:
    def __init__(self) -> None:
        self.photos_processed = 0
        self.tags_updated = 0
        self.faces_updated = 0
        self.errors: list[str] = []

    def __repr__(self) -> str:
        return (f"SyncResult(photos={self.photos_processed}, "
                f"tags={self.tags_updated}, faces={self.faces_updated}, "
                f"errors={len(self.errors)})")


async def sync_db_to_file(photo, tags: list, faces: list) -> bool:
    """Write the DB state for one photo to its file. Returns True on success."""
    from fernkam.thumbnails import photo_disk_path

    src = photo_disk_path(photo.album_path, photo.filename)
    if not src.exists():
        return False

    tag_names = [t.name for t in tags]
    tag_paths = [str(t.path).replace(".", "/") for t in tags]
    face_regions = [
        {"x": f.x, "y": f.y, "w": f.w, "h": f.h,
         "name": f.person_tag.name if f.person_tag else (f.region_name or "")}
        for f in faces if f.x is not None
    ]

    ok = write_file_metadata(
        src,
        tags=tag_names,
        tag_paths=tag_paths,
        rating=photo.rating if photo.rating >= 0 else None,
        color_label=photo.color_label if photo.color_label else None,
        title=photo.title,
        caption=photo.caption,
        faces=face_regions,
        img_w=photo.width,
        img_h=photo.height,
    )
    return ok


async def sync_file_to_db(photo, db) -> dict:
    """Read XMP from file and return changes to apply to DB.

    Returns dict with keys that changed: tags, rating, color_label, title,
    caption, faces. Caller is responsible for applying changes.
    """
    from fernkam.thumbnails import photo_disk_path

    src = photo_disk_path(photo.album_path, photo.filename)
    if not src.exists():
        return {}

    file_meta = read_file_metadata(src)
    if not file_meta:
        return {}

    changes: dict = {}

    if file_meta.get("rating") is not None and file_meta["rating"] != photo.rating:
        changes["rating"] = file_meta["rating"]

    if file_meta.get("color_label") is not None and file_meta["color_label"] != photo.color_label:
        changes["color_label"] = file_meta["color_label"]

    if file_meta.get("title") and file_meta["title"] != photo.title:
        changes["title"] = file_meta["title"]

    if file_meta.get("caption") and file_meta["caption"] != photo.caption:
        changes["caption"] = file_meta["caption"]

    if file_meta.get("tags"):
        changes["tags"] = file_meta["tags"]

    if file_meta.get("faces"):
        changes["faces"] = file_meta["faces"]

    changes["file_mtime"] = file_meta.get("file_mtime")
    return changes
