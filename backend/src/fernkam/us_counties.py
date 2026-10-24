"""Which US county (and state) a GPS point falls inside, from the Census Bureau's county boundaries.

The nearest-town lookup (reverse_geocoder) names the closest listed town, which can sit across a
county or state line, and a neighbourhood it doesn't list becomes the next town over. A boundary
test answers by the line itself. The Census cartographic boundary file (1:500,000, ~11 MB) is
downloaded once into data/geo and read with the standard library and numpy: no GIS packages.
"""
from __future__ import annotations

import logging
import struct
import urllib.request
import zipfile
from functools import lru_cache
from pathlib import Path
from typing import Optional

import numpy as np

logger = logging.getLogger(__name__)

URL = "https://www2.census.gov/geo/tiger/GENZ2024/shp/cb_2024_us_county_500k.zip"


def _zip_path() -> Path:
    from fernkam.config import BACKEND_DIR
    return BACKEND_DIR / "data" / "geo" / URL.rsplit("/", 1)[1]


def ensure_file() -> Optional[Path]:
    """The boundary file, downloaded the first time; None when it can't be had (offline)."""
    path = _zip_path()
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        part = path.with_suffix(".part")
        try:
            urllib.request.urlretrieve(URL, part)
            part.replace(path)
            logger.info("downloaded US county boundaries to %s", path)
        except OSError as exc:
            logger.warning("US county boundaries unavailable (%s); counties are skipped for now", exc)
            part.unlink(missing_ok=True)
            return None
    return path


def _read_dbf(data: bytes) -> list[dict[str, str]]:
    n, header_len, rec_len = struct.unpack("<IHH", data[4:12])
    fields, pos = [], 32
    while data[pos] != 0x0D:
        name = data[pos:pos + 11].split(b"\0", 1)[0].decode("ascii")
        fields.append((name, data[pos + 16]))
        pos += 32
    rows = []
    for i in range(n):
        rec, off, row = data[header_len + i * rec_len:header_len + (i + 1) * rec_len], 1, {}
        for name, width in fields:
            raw = rec[off:off + width]
            try:
                row[name] = raw.decode("utf-8").strip()
            except UnicodeDecodeError:
                row[name] = raw.decode("latin-1").strip()
            off += width
        rows.append(row)
    return rows


def _read_shp(data: bytes) -> list[tuple[tuple[float, ...], list[np.ndarray]]]:
    """Each polygon record: its bounding box and rings as (n, 2) lon/lat arrays."""
    out, pos = [], 100
    while pos < len(data):
        length = struct.unpack(">i", data[pos + 4:pos + 8])[0] * 2
        rec = data[pos + 8:pos + 8 + length]
        pos += 8 + length
        if struct.unpack("<i", rec[:4])[0] == 0:   # null shape
            out.append(((0.0, 0.0, -1.0, -1.0), []))
            continue
        bbox = struct.unpack("<4d", rec[4:36])
        n_parts, n_points = struct.unpack("<2i", rec[36:44])
        parts = list(struct.unpack(f"<{n_parts}i", rec[44:44 + 4 * n_parts])) + [n_points]
        pts = np.frombuffer(rec, "<f8", n_points * 2, 44 + 4 * n_parts).reshape(-1, 2)
        out.append((bbox, [pts[a:b] for a, b in zip(parts, parts[1:])]))
    return out


@lru_cache(maxsize=1)
def _counties() -> list[tuple[tuple[float, ...], list[np.ndarray], str, str]]:
    path = ensure_file()
    if not path:
        return []
    with zipfile.ZipFile(path) as z:
        stem = Path(path.name).stem
        shapes = _read_shp(z.read(f"{stem}.shp"))
        attrs = _read_dbf(z.read(f"{stem}.dbf"))
    return [(bbox, rings, a["NAMELSAD"], a["STATE_NAME"]) for (bbox, rings), a in zip(shapes, attrs)]


def available() -> bool:
    return bool(_counties())


def lookup(points: list[tuple[float, float]]) -> list[Optional[tuple[str, str]]]:
    """(county, state) for each (lat, lon), e.g. ("Miami-Dade County", "Florida"); None outside every
    county (offshore, abroad). An even-odd ray test over all of a county's rings handles islands
    and holes. Each point is tried only against counties whose bounding box holds it."""
    out: list[Optional[tuple[str, str]]] = [None] * len(points)
    if not points:
        return out
    lat = np.array([p[0] for p in points], dtype=float)
    lon = np.array([p[1] for p in points], dtype=float)
    found = np.zeros(len(points), dtype=bool)
    for (xmin, ymin, xmax, ymax), rings, county, state in _counties():
        idx = np.nonzero(~found & (lon >= xmin) & (lon <= xmax) & (lat >= ymin) & (lat <= ymax))[0]
        if not idx.size:
            continue
        inside = np.zeros(idx.size, dtype=bool)
        for ring in rings:
            x1, y1, x2, y2 = ring[:-1, 0], ring[:-1, 1], ring[1:, 0], ring[1:, 1]
            step = max(1, 2_000_000 // max(1, len(x1)))   # points per chunk: edges x points stays small
            for s in range(0, idx.size, step):
                px, py = lon[idx[s:s + step], None], lat[idx[s:s + step], None]
                crosses = (y1 > py) != (y2 > py)
                with np.errstate(divide="ignore", invalid="ignore"):
                    x_at = (x2 - x1) * (py - y1) / (y2 - y1) + x1
                inside[s:s + step] ^= (np.count_nonzero(crosses & (px < x_at), axis=1) % 2).astype(bool)
        for i in idx[inside]:
            out[i] = (county, state)
        found[idx[inside]] = True
    _nearest_shore(lat, lon, found, out)
    return out


SHORE_DEG = 0.03   # ~3 km: a photo on the sand or in the surf, outside the shoreline-clipped boundary


def _nearest_shore(lat, lon, found, out) -> None:
    """The boundary file is clipped to the shoreline for maps, so a beach photo can fall just
    outside every county. Such a point takes the county with the nearest boundary point within
    SHORE_DEG; anything farther (out at sea, abroad) stays in none."""
    if found.all():
        return
    best = np.full(len(lat), SHORE_DEG ** 2)
    scale = np.cos(np.radians(lat))   # a degree of longitude shrinks away from the equator
    for (xmin, ymin, xmax, ymax), rings, county, state in _counties():
        idx = np.nonzero(~found & (lon >= xmin - SHORE_DEG) & (lon <= xmax + SHORE_DEG)
                         & (lat >= ymin - SHORE_DEG) & (lat <= ymax + SHORE_DEG))[0]
        if not idx.size:
            continue
        pts = np.concatenate(rings)
        step = max(1, 2_000_000 // len(pts))
        for s in range(0, idx.size, step):
            i = idx[s:s + step]
            d2 = (((pts[None, :, 0] - lon[i, None]) * scale[i, None]) ** 2
                  + (pts[None, :, 1] - lat[i, None]) ** 2).min(axis=1)
            closer = d2 < best[i]
            best[i[closer]] = d2[closer]
            for j in i[closer]:
                out[j] = (county, state)
