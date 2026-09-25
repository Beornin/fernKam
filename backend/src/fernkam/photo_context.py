"""Where and when a photo was taken, as features for Tag Review.

Two context "experts" join the image models in each tag's ensemble
(tag_learning), learned from the user's own approvals like everything else:

  place   closeness to the places the library is shot at: Gaussian bumps at
          two scales (about 30 km and 300 km) around clusters of the library's
          GPS positions. Learns "herons: the marsh, the coast".
  season  day of year and time of day as smooth cycles (two harmonics of the
          year, one of the day). Learns "ospreys: April to September".

A photo without GPS has no place vector, one without a date no season vector;
the ensemble's per-combination stackers judge it by what it has. Real-world
range data (GBIF) is a third context expert, in species_range.

Neither expert can find photos (there is no index to search); they re-score
candidates the image models found.
"""
from __future__ import annotations

import math
import threading
from dataclasses import dataclass
from typing import Optional

import numpy as np
from sqlalchemy import text

EARTH_KM = 6371.0
PLACE_CENTERS = 24
PLACE_SCALES_KM = (30.0, 300.0)


@dataclass(frozen=True)
class ContextSpace:
    """A pseudo image space: features computed from photo metadata."""
    key: str
    dim: int
    label: str
    retrievable: bool = False
    is_clip: bool = False


SEASON = ContextSpace("season", 6, "Season")


def place_space(n_centers: int) -> ContextSpace:
    return ContextSpace("place", n_centers * len(PLACE_SCALES_KM), "Place")


def unit_xyz(lat: np.ndarray, lon: np.ndarray) -> np.ndarray:
    la, lo = np.radians(lat), np.radians(lon)
    return np.column_stack([np.cos(la) * np.cos(lo), np.cos(la) * np.sin(lo), np.sin(la)])


def place_features(lat: np.ndarray, lon: np.ndarray, centers: np.ndarray) -> np.ndarray:
    """(n, len(centers) * scales): exp(-d²/2σ²) for each centre and scale, d the
    straight-line distance through the earth (equal to the great-circle
    distance at these scales, and cheap)."""
    xyz = unit_xyz(lat, lon)
    d = np.linalg.norm(xyz[:, None, :] - centers[None, :, :], axis=-1) * EARTH_KM
    return np.concatenate([np.exp(-(d ** 2) / (2 * s ** 2)) for s in PLACE_SCALES_KM], axis=1).astype(np.float32)


def season_features(day_of_year: np.ndarray, hour: np.ndarray) -> np.ndarray:
    """(n, 6): the year as two harmonics (a broad season and a sharper peak),
    the day as one."""
    y = 2 * math.pi * (np.asarray(day_of_year, float) - 1) / 365.25
    h = 2 * math.pi * np.asarray(hour, float) / 24.0
    return np.column_stack([np.cos(y), np.sin(y), np.cos(2 * y), np.sin(2 * y),
                            np.cos(h), np.sin(h)]).astype(np.float32)


def fit_centers(lat: np.ndarray, lon: np.ndarray, k: int = PLACE_CENTERS) -> np.ndarray:
    """Cluster centres of the library's GPS positions (unit vectors)."""
    xyz = unit_xyz(lat, lon)
    _, first = np.unique(np.round(xyz, 4), axis=0, return_index=True)
    if len(first) <= k:
        return xyz[np.sort(first)]
    from sklearn.cluster import MiniBatchKMeans
    km = MiniBatchKMeans(n_clusters=k, random_state=0, n_init=3, batch_size=4096).fit(xyz)
    c = km.cluster_centers_
    return c / np.linalg.norm(c, axis=1, keepdims=True)


_centers_cache: dict = {}
_lock = threading.Lock()


async def library_centers(db) -> Optional[np.ndarray]:
    """Place centres for the whole library, recomputed when photos with GPS
    are added or removed."""
    stamp = tuple((await db.execute(text(
        "SELECT count(*), coalesce(max(id), 0) FROM photos "
        "WHERE status = 1 AND latitude IS NOT NULL AND longitude IS NOT NULL"))).one())
    with _lock:
        if _centers_cache.get("stamp") == stamp:
            return _centers_cache["centers"]
    if stamp[0] == 0:
        return None
    rows = (await db.execute(text(
        "SELECT latitude, longitude FROM photos "
        "WHERE status = 1 AND latitude IS NOT NULL AND longitude IS NOT NULL"))).all()
    arr = np.array([(float(a), float(b)) for a, b in rows])
    centers = fit_centers(arr[:, 0], arr[:, 1])
    with _lock:
        _centers_cache.update(stamp=stamp, centers=centers)
    return centers


async def photo_meta(db, ids: list[int]) -> dict[int, tuple]:
    """id -> (lat, lon, day of year, hour); None where unknown."""
    out: dict[int, tuple] = {}
    for start in range(0, len(ids), 5000):
        rows = (await db.execute(text("""
            SELECT id, latitude, longitude,
                   extract(doy FROM taken_at)::int, extract(hour FROM taken_at)::int,
                   extract(month FROM taken_at)::int
            FROM photos WHERE id = ANY(:ids)
        """), {"ids": ids[start:start + 5000]})).all()
        for r in rows:
            gps = r[1] is not None and r[2] is not None and not (float(r[1]) == 0 and float(r[2]) == 0)
            out[r[0]] = (float(r[1]) if gps else None, float(r[2]) if gps else None, r[3], r[4], r[5])
    return out


def context_vectors(space: ContextSpace, meta: dict[int, tuple], ids: list[int],
                    centers: Optional[np.ndarray]) -> tuple[np.ndarray, np.ndarray]:
    """(n, dim) features and the has-feature mask for ids."""
    x = np.zeros((len(ids), space.dim), np.float32)
    m = np.zeros(len(ids), bool)
    if space.key == "place":
        rows = [i for i, pid in enumerate(ids) if meta.get(pid, (None,))[0] is not None]
        if rows and centers is not None:
            lat = np.array([meta[ids[i]][0] for i in rows])
            lon = np.array([meta[ids[i]][1] for i in rows])
            x[rows] = place_features(lat, lon, centers)
            m[rows] = True
    elif space.key == "season":
        rows = [i for i, pid in enumerate(ids) if meta.get(pid, (None,) * 4)[2] is not None]
        if rows:
            doy = np.array([meta[ids[i]][2] for i in rows])
            hour = np.array([meta[ids[i]][3] or 12 for i in rows])
            x[rows] = season_features(doy, hour)
            m[rows] = True
    return x, m
