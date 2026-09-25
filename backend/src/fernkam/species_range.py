"""Where and when a species is actually seen, from GBIF, as a prior.

The place and season experts (photo_context) only know the user's own photos:
they cannot tell that a heron in Norway in January is unlikely if the user has
never been to Norway. GBIF, the Global Biodiversity Information Facility, holds
billions of dated, located records (eBird, iNaturalist, museum collections).

A species tag is linked to a GBIF taxon once (Tag Review, "Link species").
Then, for every 1° cell the library has photos in, fernKam asks GBIF how many
records of the species fall in a 3° × 3° box around it, per month, and the
same for its whole class (all birds, all mammals...). The ratio is the
species' share of what gets recorded there and then, which corrects for
places where people simply record a lot. Counts are cached in
gbif_cell_counts; a fetch per new place, not per photo.

This becomes a third context expert ("Range") in the tag's ensemble, so its
weight is learned per tag like the others, and it flags photos taken where
and when the species is not recorded. Only aggregate counts are fetched;
nothing about the user's photos is sent except the boxes being asked about.
"""
from __future__ import annotations

import asyncio
import logging
import math
from typing import Callable, Optional

import httpx
import numpy as np
from sqlalchemy import text

from fernkam.photo_context import ContextSpace

logger = logging.getLogger(__name__)

BACKBONE = "d7dddbf4-2cf0-4f39-9b2a-bb099caae36c"   # GBIF backbone taxonomy dataset
LINKABLE_RANKS = {"SPECIES", "SUBSPECIES", "VARIETY", "GENUS", "FAMILY"}
MAX_CELLS = 800          # most-photographed 1° cells (home, trips)
BOX_MARGIN = 1           # degrees around a cell: 3° × 3° boxes
CONCURRENCY = 4
MIN_EFFORT = 20          # class records needed before "not recorded here" means anything
RARE_SHARE = 0.002       # below this share of the class's records: rare here and then
RANGE = ContextSpace("range", 4, "Range (GBIF)")
MONTHS = ["", "Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]


def _base() -> str:
    from fernkam.config import get_settings
    return get_settings().gbif_url.rstrip("/")


def _client(timeout: float = 30) -> httpx.AsyncClient:
    return httpx.AsyncClient(timeout=timeout, headers={"User-Agent": "fernKam (photo organiser)"})


def _result(r: dict) -> Optional[dict]:
    rank = (r.get("rank") or "").upper()
    if rank not in LINKABLE_RANKS or not r.get("classKey"):
        return None
    common = next((v["vernacularName"] for v in r.get("vernacularNames") or []
                   if (v.get("language") or "").lower() in ("eng", "en")), None)
    return {
        "taxon_key": int(r.get("acceptedKey") or r.get("nubKey") or r.get("key") or r.get("usageKey")),
        "scientific_name": r.get("canonicalName") or r.get("scientificName"),
        "common_name": common,
        "rank": rank,
        "class_key": int(r["classKey"]),
        "class_name": r.get("class"),
        "family": r.get("family"),
        "synonym": (r.get("taxonomicStatus") or r.get("status") or "").upper() not in ("ACCEPTED", "DOUBTFUL", ""),
    }


async def search(q: str) -> list[dict]:
    """GBIF taxa matching a common or scientific name, best first."""
    q = q.strip()
    out: list[dict] = []
    async with _client(15) as c:
        m = await c.get(f"{_base()}/species/match", params={"name": q})
        if m.status_code == 200 and m.json().get("matchType") not in (None, "NONE", "HIGHERRANK"):
            hit = _result(m.json())
            if hit:
                out.append(hit)
        r = await c.get(f"{_base()}/species/search",
                        params={"q": q, "datasetKey": BACKBONE, "limit": 20})
        r.raise_for_status()
        for item in r.json().get("results", []):
            hit = _result(item)
            if hit and all(hit["taxon_key"] != o["taxon_key"] for o in out):
                out.append(hit)
    return out[:12]


def cell_of(lat: float, lon: float) -> str:
    return f"{math.floor(lat)},{math.floor(lon)}"


async def library_cells(db, cap: int = MAX_CELLS) -> list[str]:
    rows = (await db.execute(text("""
        SELECT floor(latitude)::int AS la, floor(longitude)::int AS lo, count(*) AS n
        FROM photos WHERE status = 1 AND latitude IS NOT NULL AND longitude IS NOT NULL
          AND NOT (latitude = 0 AND longitude = 0)
        GROUP BY 1, 2 ORDER BY 3 DESC LIMIT :cap
    """), {"cap": cap})).all()
    return [f"{r.la},{r.lo}" for r in rows]


async def fetch_box(client: httpx.AsyncClient, taxon_key: int, cell: str) -> dict[int, int]:
    """Records of a taxon in the box around a cell: {month: n, 0: all months}."""
    la, lo = (int(v) for v in cell.split(","))
    lat = f"{max(-90, la - BOX_MARGIN)},{min(90, la + 1 + BOX_MARGIN)}"
    lon = f"{max(-180, lo - BOX_MARGIN)},{min(180, lo + 1 + BOX_MARGIN)}"
    r = await client.get(f"{_base()}/occurrence/search", params={
        "taxonKey": taxon_key, "decimalLatitude": lat, "decimalLongitude": lon,
        "occurrenceStatus": "PRESENT", "hasCoordinate": "true", "hasGeospatialIssue": "false",
        "limit": 0, "facet": "month", "facetLimit": 12, "month.facetLimit": 12,
    })
    r.raise_for_status()
    body = r.json()
    counts = {0: int(body.get("count") or 0)}
    for facet in body.get("facets") or []:
        if (facet.get("field") or "").upper() == "MONTH":
            for c in facet.get("counts") or []:
                counts[int(c["name"])] = int(c["count"])
    return counts


async def _missing_cells(db, taxon_key: int, cells: list[str]) -> list[str]:
    have = {r[0] for r in (await db.execute(text(
        "SELECT cell FROM gbif_cell_counts WHERE taxon_key = :k AND month = 0"), {"k": taxon_key})).all()}
    return [c for c in cells if c not in have]


async def fetch_range(db, tag_id: int, progress: Optional[Callable] = None) -> dict:
    """Fetch the linked species' and its class's counts for every library cell
    not cached yet. Commits as it goes."""
    link = (await db.execute(text(
        "SELECT taxon_key, class_key FROM tag_species WHERE tag_id = :t"), {"t": tag_id})).first()
    if not link:
        raise ValueError("tag is not linked to a species")
    cells = await library_cells(db)
    todo = [(link.taxon_key, c) for c in await _missing_cells(db, link.taxon_key, cells)]
    todo += [(link.class_key, c) for c in await _missing_cells(db, link.class_key, cells)]
    sem = asyncio.Semaphore(CONCURRENCY)
    done = 0

    async with _client() as client:
        async def one(taxon: int, cell: str):
            async with sem:
                return taxon, cell, await fetch_box(client, taxon, cell)

        for coro in asyncio.as_completed([one(t, c) for t, c in todo]):
            taxon, cell, counts = await coro
            await db.execute(text("DELETE FROM gbif_cell_counts WHERE taxon_key = :k AND cell = :c"),
                             {"k": taxon, "c": cell})
            await db.execute(text("""
                INSERT INTO gbif_cell_counts (taxon_key, cell, month, n) VALUES (:k, :c, :m, :n)
            """), [{"k": taxon, "c": cell, "m": m, "n": n} for m, n in counts.items() if n or m == 0])
            await db.commit()
            done += 1
            if progress:
                await progress(done, len(todo))
    await db.execute(text("UPDATE tag_species SET range_fetched_at = now() WHERE tag_id = :t"), {"t": tag_id})
    await db.commit()
    return {"fetched": done, "cells": len(cells)}


class RangeTable:
    """A linked species' counts and its class's, per cell and month."""

    def __init__(self, species: dict, klass: dict, fetched: set[str]):
        self.species, self.klass, self.fetched = species, klass, fetched

    @classmethod
    async def load(cls, db, tag_id: int) -> Optional["RangeTable"]:
        link = (await db.execute(text(
            "SELECT taxon_key, class_key FROM tag_species WHERE tag_id = :t"), {"t": tag_id})).first()
        if not link:
            return None
        rows = (await db.execute(text(
            "SELECT taxon_key, cell, month, n FROM gbif_cell_counts WHERE taxon_key IN (:a, :b)"
        ), {"a": link.taxon_key, "b": link.class_key})).all()
        species, klass = {}, {}
        for r in rows:
            (species if r.taxon_key == link.taxon_key else klass)[(r.cell, r.month)] = r.n
        fetched = {c for (c, m) in species if m == 0} & {c for (c, m) in klass if m == 0}
        if not fetched:
            return None
        return cls(species, klass, fetched)

    def counts(self, lat: float, lon: float, month: Optional[int]) -> Optional[tuple[float, float, int, int]]:
        """(species this month, class this month, species all year, class all
        year) around a place, or None if that place was not fetched. Without a
        month, a twelfth of the year."""
        cell = cell_of(lat, lon)
        if cell not in self.fetched:
            return None
        s_y, c_y = self.species.get((cell, 0), 0), self.klass.get((cell, 0), 0)
        if month:
            return self.species.get((cell, month), 0), self.klass.get((cell, month), 0), s_y, c_y
        return s_y / 12, c_y / 12, s_y, c_y

    def status(self, lat, lon, month) -> Optional[dict]:
        """For the tile badge: absent / rare here and then, or None."""
        if lat is None:
            return None
        got = self.counts(lat, lon, month)
        if not got:
            return None
        s_m, c_m, s_y, _ = got
        when = MONTHS[month] if month else "any month"
        if c_m < MIN_EFFORT:
            return None
        if s_m == 0:
            return {"status": "absent", "when": when, "species": 0, "class": int(c_m), "year": int(s_y)}
        if s_m / c_m < RARE_SHARE:
            return {"status": "rare", "when": when, "species": int(s_m), "class": int(c_m), "year": int(s_y)}
        return None


def range_vectors(table: Optional[RangeTable], meta: dict[int, tuple], ids: list[int]) -> tuple[np.ndarray, np.ndarray]:
    """(n, 4) features: the species' smoothed share of its class's records
    here this month and all year (logs), its records this month (log), and
    whether it was never recorded here."""
    x = np.zeros((len(ids), RANGE.dim), np.float32)
    m = np.zeros(len(ids), bool)
    if table is None:
        return x, m
    for i, pid in enumerate(ids):
        lat, lon, _doy, _hour, month = meta.get(pid, (None,) * 5)
        if lat is None:
            continue
        got = table.counts(lat, lon, month)
        if not got:
            continue
        s_m, c_m, s_y, c_y = got
        x[i] = (math.log((s_m + 0.5) / (c_m + 50)), math.log((s_y + 0.5) / (c_y + 200)),
                math.log1p(s_m), 1.0 if s_y == 0 else 0.0)
        m[i] = True
    return x, m
