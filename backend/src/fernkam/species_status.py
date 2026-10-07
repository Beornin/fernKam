"""Conservation and origin facts for species tags, from GBIF.

For each species tag with a Latin name ("Gopher Tortoise (Gopherus
polyphemus)"): its IUCN Red List category, and whether the Global Register of
Introduced and Invasive Species (GRIIS) lists it for the contiguous United
States. They become fact tags under Status on every photo carrying the
species (facts are never learned; see tags.is_fact), kept in step at every
Learn all tags. Least Concern isn't tagged (most species are), and a species
missing from GRIIS isn't tagged "native": zoo animals aren't. The same
lookup links the tag to its GBIF taxon, so its range prior needs no clicks.

Only species names go to GBIF.
"""
from __future__ import annotations

import asyncio
import logging
import re

from sqlalchemy import text

from fernkam.species_range import LINKABLE_RANKS, _base, _client, get
from fernkam.tag_learning import _LATIN

logger = logging.getLogger(__name__)

GRIIS_US = "32ad19ed-6b89-447a-9242-795c0897f345"   # GRIIS United States (Contiguous), v2.0 2022
IUCN = {"EX": "IUCN Extinct", "EW": "IUCN Extinct in the Wild", "CR": "IUCN Critically Endangered",
        "EN": "IUCN Endangered", "VU": "IUCN Vulnerable", "NT": "IUCN Near Threatened"}
INTRODUCED = "Introduced to the US"
SPECIES_RANKS = {"SPECIES", "SUBSPECIES", "VARIETY"}


def status_names(iucn: str | None, introduced: bool) -> list[str]:
    return ([IUCN[iucn]] if iucn in IUCN else []) + ([INTRODUCED] if introduced else [])


async def fetch(db, progress=None) -> int:
    """Look up species tags that have a Latin name and no status yet. Commits."""
    rows = (await db.execute(text("""
        SELECT t.id, t.name FROM tags t
        WHERE t.path <@ 'Wildlife' AND NOT t.is_fact AND NOT t.is_person
          AND NOT EXISTS (SELECT 1 FROM species_status s WHERE s.tag_id = t.id)"""))).all()
    todo = [(r.id, m[1]) for r in rows if (m := _LATIN.search(r.name))]
    sem = asyncio.Semaphore(4)
    done = 0
    async with _client() as c:
        async def one(tid: int, latin: str):
            async with sem:
                m = (await get(c, f"{_base()}/species/match", {"name": latin, "strict": "true"})).raise_for_status().json()
                # "Boa", "Hibiscus sp.": GBIF calls the genus a higher-rank match.
                genus = m.get("matchType") == "HIGHERRANK" and m.get("rank") == "GENUS" \
                    and m.get("canonicalName") == latin.split()[0]
                if not genus and (m.get("matchType") not in ("EXACT", "FUZZY") or m.get("confidence", 0) < 90):
                    return tid, None, None, False, None
                key = m.get("acceptedUsageKey") or m.get("usageKey")
                if m.get("rank") not in SPECIES_RANKS:   # a genus or family: no Red List category
                    return tid, key, None, False, m
                r = await get(c, f"{_base()}/species/{key}/iucnRedListCategory")
                if r.status_code not in (200, 204, 404):   # not assessed: 204 or 404
                    r.raise_for_status()
                iucn = r.json().get("code") if r.status_code == 200 and r.content.strip() else None
                g = (await get(c, f"{_base()}/species/search",
                               {"datasetKey": GRIIS_US, "q": latin, "limit": 10})).raise_for_status().json()
                # The same species, or its species-level entry for a subspecies tag. Not
                # a listed subspecies of it: GRIIS lists the dog, Canis lupus familiaris.
                species = " ".join(latin.split()[:2]).lower()
                introduced = any((x.get("canonicalName") or "").lower() in (latin.lower(), species)
                                 for x in g.get("results", []))
                return tid, key, iucn, introduced, m

        for coro in asyncio.as_completed([one(t, latin) for t, latin in todo]):
            try:
                tid, key, iucn, introduced, m = await coro
            except Exception as exc:  # noqa: BLE001 — retried at the next run
                logger.warning("GBIF status lookup failed: %s", exc)
                continue
            if m:
                await link(db, tid, key, m)
            await db.execute(text("""
                INSERT INTO species_status (tag_id, taxon_key, iucn, introduced_us) VALUES (:t, :k, :i, :u)
                ON CONFLICT (tag_id) DO UPDATE SET taxon_key = EXCLUDED.taxon_key, iucn = EXCLUDED.iucn,
                    introduced_us = EXCLUDED.introduced_us, checked_at = now()
            """), {"t": tid, "k": key, "i": iucn, "u": introduced})
            done += 1
            if done % 50 == 0:
                await db.commit()
                if progress:
                    await progress(done, len(todo))
    await db.commit()
    return done


async def link(db, tag_id: int, key: int, m: dict) -> None:
    """Link the tag to its matched taxon for the range prior (species_range),
    once, when the tag is first looked up: an Unlink sticks, and a hand-picked
    link is kept."""
    # The group whose records correct for effort: the class, or for ray-finned
    # fish (no class in GBIF's backbone) the order.
    group = m.get("classKey") or m.get("orderKey")
    if m.get("rank") not in LINKABLE_RANKS or not group or key == group:
        return
    await db.execute(text("""
        INSERT INTO tag_species (tag_id, taxon_key, scientific_name, common_name, rank, class_key, class_name)
        SELECT :t, :k, :sn, trim(split_part(name, '(', 1)), :r, :ck, :cl FROM tags WHERE id = :t
        ON CONFLICT (tag_id) DO NOTHING
    """), {"t": tag_id, "k": key, "sn": m.get("canonicalName") or m.get("scientificName"),
           "r": m["rank"], "ck": group, "cl": m.get("class") or m.get("order")})


async def _status_tag_ids(db) -> dict[str, int]:
    """The Status fact tags, created on first use."""
    root = (await db.execute(text("SELECT id FROM tags WHERE path = 'Status'"))).scalar()
    if root is None:
        root = (await db.execute(text("""INSERT INTO tags (name, path, parent_id, is_person, is_fact)
            VALUES ('Status', 'Status', NULL, false, true) RETURNING id"""))).scalar_one()
    ids = {}
    for name in [*IUCN.values(), INTRODUCED]:
        path = "Status." + re.sub(r"[^A-Za-z0-9_]", "_", name)
        tid = (await db.execute(text("SELECT id FROM tags WHERE path = CAST(:p AS ltree)"), {"p": path})).scalar()
        if tid is None:
            tid = (await db.execute(text("""INSERT INTO tags (name, path, parent_id, is_person, is_fact)
                VALUES (:n, CAST(:p AS ltree), :r, false, true) RETURNING id"""),
                {"n": name, "p": path, "r": root})).scalar_one()
        ids[name] = tid
    return ids


async def apply(db) -> int:
    """Put each Status tag on exactly the photos carrying a species with that
    status (approved or not yet checked; a rejected species link isn't a link).
    Changed photos are flagged for write-back. Returns how many links changed.
    Does not commit."""
    tag_ids = await _status_tag_ids(db)
    pairs = [(r.tag_id, tag_ids[n]) for r in (await db.execute(text(
        "SELECT tag_id, iucn, introduced_us FROM species_status"))).all()
        for n in status_names(r.iucn, r.introduced_us)]
    params = {"sp": [a for a, _ in pairs], "st": [b for _, b in pairs], "all": list(tag_ids.values())}
    want = """SELECT DISTINCT pt.photo_id, m.st FROM photo_tags pt
              JOIN unnest(CAST(:sp AS int[]), CAST(:st AS int[])) AS m(sp, st) ON pt.tag_id = m.sp"""
    gone = (await db.execute(text(f"""
        DELETE FROM photo_tags x WHERE x.tag_id = ANY(CAST(:all AS int[]))
          AND NOT EXISTS (SELECT 1 FROM ({want}) w WHERE w.photo_id = x.photo_id AND w.st = x.tag_id)
        RETURNING x.photo_id"""), params)).scalars().all()
    added = (await db.execute(text(f"""
        INSERT INTO photo_tags (photo_id, tag_id, verified_at) SELECT photo_id, st, now() FROM ({want}) w
        ON CONFLICT (photo_id, tag_id) DO NOTHING RETURNING photo_id"""), params)).scalars().all()
    changed = list(set(gone) | set(added))
    if changed:
        await db.execute(text("UPDATE photos SET file_sync_dirty = true WHERE id = ANY(:ids)"), {"ids": changed})
    return len(gone) + len(added)
