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
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

_EXIFTOOL_PATHS = [
    r"C:\Users\Ben\Documents\MY TOOLS\exiftool-13.59_64\exiftool.exe",
    r"C:\Program Files (x86)\digiKam\exiftool.exe",
    "/usr/bin/exiftool",
    "/usr/local/bin/exiftool",
]

_GPS_INVALID = frozenset({"undef", "undefined", "none", "n/a", ""})


def _gps_float(val) -> Optional[float]:
    """Convert exiftool GPS value to float, or None for absent / invalid values.

    Exiftool returns '' or 'undef' for GPS fields when no GPS data is present
    (even with -n flag), which would crash a NUMERIC column insert.
    """
    if val is None:
        return None
    if isinstance(val, (int, float)):
        return float(val)
    s = str(val).strip()
    if s.lower() in _GPS_INVALID:
        return None
    try:
        return float(s)
    except (ValueError, TypeError):
        return None


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

# Tag arguments requested from exiftool for a metadata read. Shared by the
# single-file and batched (stay_open) code paths so they stay identical.
_ET_TAGS: list[str] = [
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
]


class ExifToolSession:
    """Persistent ``exiftool -stay_open`` process for fast batched reads.

    exiftool's process startup dominates the per-file cost; keeping one process
    alive and feeding it argfiles via stdin removes that overhead and lets us
    read many files in a single ``-execute``. Thread-safe via an internal lock
    (the stay_open pipe is a single serial channel).
    """

    def __init__(self, exiftool_path: str):
        self._et = exiftool_path
        self._proc: Optional[subprocess.Popen] = None
        self._lock = threading.Lock()
        self._seq = 0

    def _ensure(self) -> None:
        if self._proc is not None and self._proc.poll() is None:
            return
        self._proc = subprocess.Popen(
            [self._et, "-stay_open", "True", "-@", "-"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,  # discard tag-warning noise; failures show as empty results
            bufsize=0,
        )

    def execute(self, args: list[str]) -> Optional[str]:
        """Run one exiftool command and return raw stdout text (None on failure)."""
        with self._lock:
            try:
                self._ensure()
                assert self._proc and self._proc.stdin and self._proc.stdout
                self._seq += 1
                tag = str(self._seq)
                payload = "\n".join(args) + f"\n-execute{tag}\n"
                self._proc.stdin.write(payload.encode("utf-8"))
                self._proc.stdin.flush()
                sentinel = f"{{ready{tag}}}".encode()
                out = bytearray()
                while True:
                    line = self._proc.stdout.readline()
                    if not line:
                        self._proc = None  # process died
                        return None
                    if line.strip() == sentinel:
                        break
                    out += line
                return out.decode("utf-8", errors="replace")
            except Exception as exc:
                logger.warning("exiftool stay_open error: %s", exc)
                self.close()
                return None

    def close(self) -> None:
        proc = self._proc
        self._proc = None
        if proc is None:
            return
        try:
            if proc.stdin:
                proc.stdin.write(b"-stay_open\nFalse\n")
                proc.stdin.flush()
            proc.wait(timeout=5)
        except Exception:
            try:
                proc.kill()
            except Exception:
                pass


_et_session: Optional[ExifToolSession] = None
_et_session_lock = threading.Lock()


def _get_et_session() -> Optional[ExifToolSession]:
    global _et_session
    et = _et()
    if not et:
        return None
    if _et_session is None:
        with _et_session_lock:
            if _et_session is None:
                _et_session = ExifToolSession(et)
    return _et_session


def _norm_sourcefile(s: str) -> str:
    """Normalise a path for matching exiftool's SourceFile output to our paths."""
    return str(s).replace("\\", "/").rstrip("/").lower()


def read_many_metadata(paths: list[Path], chunk_size: int = 100) -> dict[Path, dict]:
    """Read metadata for many files via one persistent exiftool process.

    Files are processed in chunks (one ``-execute`` per chunk, JSON-array
    output). Returns ``{Path: parsed_metadata}``. Falls back to per-file reads
    when the stay_open session is unavailable or a chunk fails to parse.
    """
    results: dict[Path, dict] = {}
    if not paths:
        return results
    session = _get_et_session()
    if session is None:
        return {p: read_file_metadata(p) for p in paths}

    for i in range(0, len(paths), chunk_size):
        chunk = paths[i:i + chunk_size]
        norm_map: dict[str, Path] = {}
        args = ["-json", *_ET_TAGS]
        for p in chunk:
            args.append(str(p))
            norm_map[_norm_sourcefile(str(p))] = p
        raw = session.execute(args)
        if not raw:
            for p in chunk:
                results[p] = read_file_metadata(p)
            continue
        try:
            data = json.loads(raw)
        except Exception:
            for p in chunk:
                results[p] = read_file_metadata(p)
            continue
        seen: set[Path] = set()
        for obj in data:
            p = norm_map.get(_norm_sourcefile(obj.get("SourceFile", "")))
            if p is None:
                continue
            results[p] = parse_exif_dict(obj, p)
            seen.add(p)
        for p in chunk:
            if p not in seen:
                results.setdefault(p, {})
    return results


async def read_many_metadata_async(paths: list[Path], chunk_size: int = 100) -> dict[Path, dict]:
    """Async wrapper - runs batched exiftool reads in a thread executor."""
    import asyncio
    from functools import partial
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, partial(read_many_metadata, paths, chunk_size))


def parse_exif_dict(meta: dict, file_path: Path) -> dict:
    """Convert a raw exiftool JSON object into fernKam's structured metadata."""
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
        "latitude": _gps_float(meta.get("GPSLatitude")),
        "longitude": _gps_float(meta.get("GPSLongitude")),
        "altitude": _gps_float(meta.get("GPSAltitude")),
        "orientation": meta.get("Orientation"),
        "camera": camera_info,
        "lens": lens_info,
        "exif": exif_dump,
        "file_mtime": file_mtime,
    }


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
    meta = _run_et([*_ET_TAGS, str(file_path)])
    if meta is None:
        return {}
    return parse_exif_dict(meta, file_path)


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
            capture_output=True, stdin=subprocess.DEVNULL, timeout=30,
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


# ═══════════════════════════ BATCH HELPERS ═══════════════════════════════════

def build_photo_payload(photo, tags: list, faces: list) -> Optional[dict]:
    """Build an exiftool JSON payload dict for one photo.

    Returns None if the source file does not exist.
    Named faces (confirmed + DigiKam-imported region_name) are written as
    MWG-RS regions so DigiKam reads them back correctly.
    """
    from fernkam.thumbnails import photo_disk_path

    src = photo_disk_path(photo.album_path, photo.filename)
    if not src.exists():
        return None

    payload: dict = {"SourceFile": str(src)}

    tag_names = [t.name for t in tags]
    tag_paths = [str(t.path).replace(".", "/") for t in tags]

    # Merge confirmed person names into Subject/HierarchicalSubject (DigiKam style)
    for f in faces:
        if f.x is None:
            continue
        person_name = f.person_tag.name if f.person_tag else (f.region_name or "")
        if person_name and person_name not in tag_names:
            tag_names.append(person_name)
            tag_paths.append(f"People/{person_name}")

    if tag_names:
        payload["Subject"] = tag_names
        payload["Keywords"] = tag_names
        payload["HierarchicalSubject"] = [t.replace("/", "|") for t in tag_paths]

    if photo.rating is not None and photo.rating >= 0:
        payload["Rating"] = max(0, min(5, photo.rating))
        payload["RatingPercent"] = max(0, min(5, photo.rating)) * 20

    if photo.color_label:
        payload["Label"] = COLOR_LABEL_TO_NAME.get(photo.color_label, "")

    if photo.title:
        payload["Title"] = photo.title

    if photo.caption:
        payload["Description"] = photo.caption
        payload["Caption-Abstract"] = photo.caption

    # Face regions: DigiKam stops reading at the first entry with empty Name,
    # so only write faces that have a name.
    img_w = photo.width or 0
    img_h = photo.height or 0
    face_regions = []
    for f in faces:
        if f.x is None or not img_w or not img_h:
            continue
        person_name = f.person_tag.name if f.person_tag else (f.region_name or "")
        if not person_name.strip():
            continue
        cx = round((f.x + f.w / 2) / img_w, 6)
        cy = round((f.y + f.h / 2) / img_h, 6)
        nw = round(f.w / img_w, 6)
        nh = round(f.h / img_h, 6)
        face_regions.append({
            "Area": {"X": cx, "Y": cy, "W": nw, "H": nh, "Unit": "normalized"},
            "Type": "Face",
            "Name": person_name,
        })

    if face_regions:
        payload["RegionInfo"] = {
            "AppliedToDimensions": {"W": img_w, "H": img_h, "Unit": "pixel"},
            "RegionList": face_regions,
        }

    return payload


def write_metadata_batch(payloads: list[dict]) -> tuple[int, int]:
    """Write metadata for a batch of photos in a single exiftool call.

    Each payload must be a dict with a "SourceFile" key and whatever XMP fields
    should be written.  One exiftool process handles the whole batch, which
    amortises the ~200 ms startup overhead over many files.

    Returns (ok_count, error_count).
    """
    et = _et()
    if not et:
        return 0, len(payloads)
    if not payloads:
        return 0, 0

    tmp_fd, tmp_path = tempfile.mkstemp(suffix=".json")
    try:
        with os.fdopen(tmp_fd, "w", encoding="utf-8") as fh:
            json.dump(payloads, fh, ensure_ascii=False)

        result = subprocess.run(
            [et, "-overwrite_original", f"-json={tmp_path}"],
            capture_output=True, stdin=subprocess.DEVNULL, timeout=300,
        )
        if result.returncode != 0:
            logger.warning(
                "exiftool batch write failed (%d files): %s",
                len(payloads),
                result.stderr.decode(errors="replace")[:500],
            )
            return 0, len(payloads)
        return len(payloads), 0
    except Exception as exc:
        logger.warning("exiftool batch write exception: %s", exc)
        return 0, len(payloads)
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
    import asyncio
    payload = build_photo_payload(photo, tags, faces)
    if payload is None:
        return False
    loop = asyncio.get_event_loop()
    ok_count, _ = await loop.run_in_executor(None, write_metadata_batch, [payload])
    return ok_count > 0


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
