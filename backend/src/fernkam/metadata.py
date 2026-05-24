"""Write photo metadata back to image files via ExifTool.

Uses DigiKam-compatible XMP fields:
  - XMP:Subject / XMP:HierarchicalSubject / IPTC:Keywords  (tags)
  - XMP:Rating / EXIF:Rating                               (rating)
  - XMP:Description / IPTC:Caption-Abstract                (caption)
  - XMP:Title                                              (title)
  - XMP:Label                                              (color label)
  - XMP-mwg-rs:RegionInfo                                  (face regions, MWG standard)

For RAW files that don't support embedded XMP, exiftool automatically
creates/updates a sidecar <filename>.xmp file (same behaviour as DigiKam).
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Optional

from fernkam.config import get_settings
from fernkam.thumbnails import photo_disk_path

logger = logging.getLogger(__name__)

_FALLBACK_EXIFTOOL_PATHS = [
    r"C:\Program Files\digiKam\exiftool.exe",
    r"C:\Program Files (x86)\digiKam\exiftool.exe",
    "/usr/bin/exiftool",
    "/usr/local/bin/exiftool",
]

COLOR_LABEL_NAMES: dict[int, str] = {
    1: "Red",
    2: "Orange",
    3: "Yellow",
    4: "Green",
    5: "Blue",
    6: "Purple",
    7: "Gray",
}


def find_exiftool() -> Optional[str]:
    et = shutil.which("exiftool")
    if et:
        return et
    for p in _FALLBACK_EXIFTOOL_PATHS:
        if Path(p).exists():
            return p
    return None


def _build_payload(
    *,
    tags: Optional[list[str]],
    tag_paths: Optional[list[str]],
    rating: Optional[int],
    caption: Optional[str],
    title: Optional[str],
    color_label: Optional[int],
    face_regions: Optional[list[dict]],
    img_width: Optional[int],
    img_height: Optional[int],
) -> dict:
    """Build the exiftool JSON payload dict."""
    p: dict = {}

    if tags is not None:
        p["Subject"] = tags
        p["Keywords"] = tags
        if tag_paths:
            # DigiKam stores hierarchical tags with | separator in HierarchicalSubject
            p["HierarchicalSubject"] = [t.replace("/", "|") for t in tag_paths]

    if rating is not None:
        star = max(0, min(5, rating))
        p["Rating"] = star
        p["RatingPercent"] = star * 20

    if caption is not None:
        p["Description"] = caption
        p["Caption-Abstract"] = caption

    if title is not None:
        p["Title"] = title

    if color_label is not None:
        p["Label"] = COLOR_LABEL_NAMES.get(color_label, "")

    if face_regions is not None and img_width and img_height:
        region_list = []
        for r in face_regions:
            if None in (r.get("x"), r.get("y"), r.get("w"), r.get("h")):
                continue
            cx = round((r["x"] + r["w"] / 2) / img_width, 6)
            cy = round((r["y"] + r["h"] / 2) / img_height, 6)
            nw = round(r["w"] / img_width, 6)
            nh = round(r["h"] / img_height, 6)
            entry: dict = {
                "Area": {"X": cx, "Y": cy, "W": nw, "H": nh, "Unit": "normalized"},
                "Type": "Face",
            }
            if r.get("name"):
                entry["Name"] = r["name"]
            region_list.append(entry)

        p["RegionInfo"] = {
            "AppliedToDimensions": {
                "W": img_width,
                "H": img_height,
                "Unit": "pixel",
            },
            "RegionList": region_list,
        }

    return p


def _run_exiftool_sync(src: Path, payload: dict) -> None:
    et = find_exiftool()
    if not et:
        logger.warning("exiftool not found — skipping metadata write for %s", src.name)
        return

    payload["SourceFile"] = str(src)
    tmp_fd, tmp_path = tempfile.mkstemp(suffix=".json")
    try:
        with os.fdopen(tmp_fd, "w") as f:
            json.dump([payload], f)

        result = subprocess.run(
            [et, "-overwrite_original", f"-json={tmp_path}", str(src)],
            capture_output=True,
            timeout=60,
        )
        if result.returncode != 0:
            logger.warning(
                "exiftool write failed for %s: %s",
                src.name,
                result.stderr.decode(errors="replace"),
            )
        else:
            logger.debug("exiftool wrote metadata to %s", src.name)
    except Exception as exc:
        logger.warning("exiftool exception for %s: %s", src.name, exc)
    finally:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass


async def write_photo_metadata(
    photo_id: int,
    album_path: str,
    filename: str,
    *,
    tags: Optional[list[str]] = None,
    tag_paths: Optional[list[str]] = None,
    rating: Optional[int] = None,
    caption: Optional[str] = None,
    title: Optional[str] = None,
    color_label: Optional[int] = None,
    face_regions: Optional[list[dict]] = None,
    img_width: Optional[int] = None,
    img_height: Optional[int] = None,
) -> None:
    """Async fire-and-forget metadata write.

    face_regions items: {x, y, w, h, name}  (pixel coords)
    """
    src = photo_disk_path(album_path, filename)
    if not src.exists():
        logger.debug("source file not found for photo %d, skipping write", photo_id)
        return

    payload = _build_payload(
        tags=tags,
        tag_paths=tag_paths,
        rating=rating,
        caption=caption,
        title=title,
        color_label=color_label,
        face_regions=face_regions,
        img_width=img_width,
        img_height=img_height,
    )
    if not payload:
        return

    loop = asyncio.get_event_loop()
    await loop.run_in_executor(None, _run_exiftool_sync, src, payload)
