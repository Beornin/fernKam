"""Portfolio keywords and tags: keeps wildlife folders and their pictures in step, in one pass.

For the folders picked (each with everything under it):
1. A species folder ("Common (Latin)", "Common - note (Latin)", a genus "(Tantilla)" or "(Taxodium sp.)")
   is the record. Every picture in it, and in subfolders without a Latin name of their own (RAW/, dng/),
   gets the species tag with that Latin name, approved. With no such tag: a tag GBIF knows under the same
   accepted name is used (a merged or older name), or the one species the folder's pictures are already
   approved as; failing both, a tag named like the folder is made next to the tags its neighbours use.
2. Species tags are linked to GBIF with their IUCN category and whether they were introduced to the US
   (the Status tags), and every picture gets its Camera › maker › model tag: what Learn all tags does.
3. Research words and Darwin Core fields (what Export for web gives a copy) are refreshed; a picture whose
   words changed is marked for write-back, and Maintenance > DB -> Files puts them into the file.
4. RAW/ files are stacked with their JPGs, then every picture without a licensing Ref gets one
   (reference_tags: REF-<n>, shared by a stack), marked for write-back like the research words.
Listed, not changed: Latin names GBIF spells differently or doesn't know (a typo?), folders with pictures but no Latin name, and
pictures in a species folder already approved as another species. Preview changes nothing.
"""
from __future__ import annotations

import asyncio
import json
import re
import time
from collections import Counter, defaultdict
from pathlib import Path
from typing import Optional

from fernkam.workflows.shared import format_elapsed


def species_folder(album: str, root: str) -> Optional[str]:
    """The species folder an album belongs to: itself or the nearest folder above it (not above root)
    whose name carries a Latin name. 'A/Crayfish/Wingtail Crayfish (Procambarus latipleurum)/dng' -> its parent."""
    from fernkam.workflows.export_web import folder_species
    parts = album.split("/")
    for i in range(len(parts), len(root.split("/")) - 1, -1):
        if folder_species(parts[i - 1]):
            return "/".join(parts[:i])
    return None


def run(paths: list[str], dry_run: bool = True) -> None:
    start = time.perf_counter()
    asyncio.run(_run_async(paths, dry_run))
    print(f"Process took: {format_elapsed(start)}")


async def _run_async(paths: list[str], dry_run: bool) -> None:
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
    from sqlalchemy.pool import NullPool

    from fernkam.config import get_settings

    s = get_settings()
    lib = Path(s.library_root).resolve()
    roots = []
    for p in paths:
        p = Path(p.strip().strip('"')).resolve()
        if not p.is_dir() or not p.is_relative_to(lib):
            print(f"ERROR: {p} is not a folder inside the library ({lib})")
            return
        # The catalogue's spelling of the folder (Windows paths ignore case; the catalogue doesn't).
        rel = p.relative_to(lib).as_posix()
        roots.append("" if rel == "." else rel)
    print(f"{'Preview: ' if dry_run else ''}{', '.join(r or '(whole library)' for r in roots)}")
    # Own unpooled engine: this runs in a worker thread under its own loop (see export_web).
    engine = create_async_engine(s.pg_url, poolclass=NullPool)
    try:
        async with async_sessionmaker(engine)() as db:
            from sqlalchemy import text
            for i, r in enumerate(roots):
                real = (await db.execute(text("""SELECT substr(album_path, 1, length(:r)) FROM photos
                    WHERE lower(album_path) = lower(:r) OR lower(album_path) LIKE lower(:r) || '/%' LIMIT 1"""), {"r": r})).scalar()
                roots[i] = real if real is not None else r
                n = (await db.execute(text("""SELECT count(*) FROM photos WHERE status = 1
                    AND (:r = '' OR album_path = :r OR album_path LIKE :r || '/%')"""), {"r": roots[i]})).scalar()
                print(f"   {roots[i] or '(whole library)'}: {n} pictures")
            taxa: dict = {}
            await _folder_tags(db, roots, taxa, dry_run)
            await _status_and_cameras(db, dry_run)
            await _research(db, roots, lib, s, taxa, dry_run)
            await _refs(db, roots, dry_run)
    finally:
        await engine.dispose()


async def _folder_tags(db, roots: list[str], taxa: dict, dry_run: bool) -> None:
    from sqlalchemy import text

    from fernkam.api.routers.tags import create_tag
    from fernkam.workflows.export_web import _match_key, folder_species

    print("\n1. Species folders")
    photos = (await db.execute(text("""
        SELECT p.id, p.album_path FROM photos p
        WHERE p.status = 1 AND (p.album_path = ANY(:a) OR p.album_path LIKE ANY(:like))"""),
        {"a": roots, "like": [f"{r}/%" if r else "%" for r in roots]})).all()
    by_folder, unnamed = defaultdict(list), Counter()
    for pid, album in photos:
        root = next(r for r in roots if r == "" or album == r or album.startswith(r + "/"))
        if f := species_folder(album, root):
            by_folder[f].append(pid)
        else:
            unnamed[album] += 1
    latin_of = lambda name: ((folder_species(name) or {}).get("latin") or "").lower()
    tags = (await db.execute(text("""SELECT t.id, t.name, t.parent_id, ts.taxon_key FROM tags t
        LEFT JOIN tag_species ts ON ts.tag_id = t.id WHERE t.path <@ 'Wildlife' AND NOT t.is_fact"""))).all()
    by_latin, by_key = defaultdict(list), {}
    # Only species tags ("Common (Latin)") can be a folder's tag or show where a new one goes:
    # a group tag like "Birds" on every picture is not the species of a new folder.
    species_ids = {t.id for t in tags if latin_of(t.name)}
    for t in tags:
        if latin_of(t.name):
            by_latin[latin_of(t.name)].append(t)
        if t.taxon_key:
            by_key[t.taxon_key] = t
    approved = defaultdict(set)   # photo -> approved species tags
    for pid, tid in (await db.execute(text("""SELECT x.photo_id, x.tag_id FROM photo_tags x JOIN tags t ON t.id = x.tag_id
            WHERE x.verified_at IS NOT NULL AND t.path <@ 'Wildlife' AND NOT t.is_fact AND x.photo_id = ANY(:p)"""),
            {"p": [p for p, _ in photos]})).all():
        approved[pid].add(tid)
    unknown, respelt, conflicts, made, fixed = [], [], Counter(), [], 0
    for folder, pids in sorted(by_folder.items()):
        fs = folder_species(folder.rsplit("/", 1)[-1])
        cands = by_latin.get(fs["latin"].lower(), [])
        tag = (next((t for t in cands if t.name.lower() == f"{fs['common']} ({fs['latin']})".lower()), None)
               or max(cands, key=lambda t: sum(t.id in approved[p] for p in pids), default=None))
        key = await _match_key(fs["latin"], taxa)
        if not tag and key:   # a merged or older name: GBIF's accepted name is already a tag
            accepted = taxa.get(("accepted", key))
            if accepted is None:
                from fernkam.species_range import _base, _client, get
                async with _client() as c:
                    accepted = (await get(c, f"{_base()}/species/{key}")).json().get("acceptedKey") or key
                taxa[("accepted", key)] = accepted
            tag = by_key.get(accepted) or by_key.get(key)
        if not tag:           # the pictures already agree on one species: that is the folder's tag
            common = Counter(t for p in pids for t in approved[p] if t in species_ids).most_common(1)
            if common and common[0][1] >= 0.8 * len(pids):
                tag = next(t for t in tags if t.id == common[0][0])
        if not key and not re.search(r"\bsp\.?$", fs["latin"]):
            unknown.append(f"{fs['latin']}  ({folder})")
        elif spelt := taxa.get(("spelled", re.sub(r"\s+(cf|aff)\.", "", fs["latin"]).strip())):
            respelt.append(f"{fs['latin']} -> {spelt}  ({folder})")
        if not tag:
            parent = Counter(t for f2, p2 in by_folder.items() if f2.rsplit("/", 1)[0] == folder.rsplit("/", 1)[0]
                             for p in p2 for t in approved[p] if t in species_ids).most_common(1)
            if not parent:   # its neighbours are outside the folders picked: ask the catalogue
                sib = (await db.execute(text("""
                    SELECT x.tag_id, count(*) AS n FROM photos p JOIN photo_tags x ON x.photo_id = p.id
                    WHERE p.status = 1 AND starts_with(p.album_path, :up) AND x.verified_at IS NOT NULL
                      AND x.tag_id = ANY(:sp) GROUP BY 1 ORDER BY 2 DESC LIMIT 1"""),
                    {"up": folder.rsplit("/", 1)[0] + "/", "sp": list(species_ids)})).first()
                parent = [(sib.tag_id, sib.n)] if sib else []
            parent_id = next((t.parent_id for t in tags if parent and t.id == parent[0][0]), None)
            name = f"{fs['common']} ({fs['latin']})"
            if not key or not parent_id:
                print(f"   no tag for {name} and none made ({'GBIF does not know it' if not key else 'no neighbouring tags to file it with'}): {folder}")
                continue
            made.append(name)
            if dry_run:
                fixed += len(pids)
                continue
            tag = await create_tag(db, name=name, parent_id=parent_id, is_person=False)
            by_latin[fs["latin"].lower()].append(tag)
        todo = [p for p in pids if tag.id not in approved[p]]
        conflicts[folder] += sum(1 for p in todo if approved[p] & species_ids)
        fixed += len(todo)
        if todo and not dry_run:
            await db.execute(text("""INSERT INTO photo_tags (photo_id, tag_id, verified_at) SELECT unnest(CAST(:ids AS int[])), :t, now()
                ON CONFLICT (photo_id, tag_id) DO UPDATE SET verified_at = now() WHERE photo_tags.verified_at IS NULL"""),
                {"ids": todo, "t": tag.id})
            await db.execute(text("DELETE FROM tag_rejections WHERE tag_id = :t AND photo_id = ANY(:ids)"), {"ids": todo, "t": tag.id})
            await db.execute(text("UPDATE photos SET file_sync_dirty = true WHERE id = ANY(:ids)"), {"ids": todo})
        if todo:
            print(f"   {len(todo):>5} of {len(pids):>5}  {folder}  ->  {tag.name}")
    if not dry_run:
        await db.commit()
    print(f"   {len(by_folder)} species folders; {fixed} pictures {'would get' if dry_run else 'got'} their folder's tag"
          + (f"; new tags: {', '.join(made)}" if made else ""))
    for u in respelt:
        print(f"   GBIF spells it differently (both names become keywords): {u}")
    for u in unknown:
        print(f"   GBIF has no single match for this Latin name (a typo, or a name it lacks): {u}")
    for f, n in conflicts.items():
        if n:
            print(f"   {n} pictures in {f} were already approved as another species too: worth a look")
    for album, n in sorted(unnamed.items()):
        print(f"   no Latin name in the folder, left alone: {album} ({n})")


async def _status_and_cameras(db, dry_run: bool) -> None:
    from sqlalchemy import text

    from fernkam import camera_tags, species_status

    print("\n2. GBIF links, IUCN / introduced status, camera tags (all species tags and pictures, as Learn all does)")
    if dry_run:
        n = (await db.execute(text("""SELECT count(*) FROM tags t WHERE t.path <@ 'Wildlife' AND NOT t.is_fact AND NOT t.is_person
            AND t.name ~ '\\([A-Z][a-z]+' AND NOT EXISTS (SELECT 1 FROM species_status s WHERE s.tag_id = t.id)"""))).scalar()
        print(f"   {n} species tags not checked with GBIF yet")
        return
    checked = await species_status.fetch(db)
    status = await species_status.apply(db)
    cams = await camera_tags.apply(db)
    await db.commit()
    print(f"   {checked} species tags checked with GBIF; {status} status links and {cams} camera links changed")


async def _research(db, roots: list[str], lib: Path, s, taxa: dict, dry_run: bool) -> None:
    from sqlalchemy import text

    from fernkam.workflows.export_web import _metadata

    print("\n3. Research words and Darwin Core")
    rows = (await db.execute(text("""
        SELECT p.id, p.album_path, p.filename, p.title, p.caption, p.state, p.county, p.research, c.make, c.model, l.model AS lens
        FROM photos p LEFT JOIN cameras c ON c.id = p.camera_id LEFT JOIN lenses l ON l.id = p.lens_id
        WHERE p.status = 1 AND (p.album_path = ANY(:a) OR p.album_path LIKE ANY(:like))
        ORDER BY p.album_path, p.filename"""), {"a": roots, "like": [f"{r}/%" if r else "%" for r in roots]})).all()
    changed = shown = 0
    for n, r in enumerate(rows, 1):
        exif = {"Make": r.make, "Model": r.model, "LensModel": r.lens}
        m = await _metadata(db, lib / r.album_path / r.filename, lib, s, taxa, exif, row=r)
        new = {"keywords": [k for k in m["keywords"] if not k.startswith("taxonomy:")], "dwc": m["dwc"]}
        if new != (r.research or {}):
            changed += 1
            if shown < 3:
                shown += 1
                print(f"   {r.album_path}/{r.filename}\n      keywords: {', '.join(new['keywords'])}"
                      f"\n      Darwin Core: {', '.join(f'{k}={v}' for k, v in new['dwc'].items()) or '-'}")
            if not dry_run:
                await db.execute(text("UPDATE photos SET research = CAST(:r AS jsonb), file_sync_dirty = true WHERE id = :id"),
                                 {"r": json.dumps(new), "id": r.id})
        if n % 1000 == 0:
            if not dry_run:
                await db.commit()
            print(f"   {n}/{len(rows)} checked, {changed} with new words", flush=True)
    if not dry_run:
        await db.commit()
    print(f"   {changed} of {len(rows)} pictures {'would get' if dry_run else 'got'} new research words"
          + ("" if dry_run else "; Maintenance > DB -> Files writes them into the files."))


async def _refs(db, roots: list[str], dry_run: bool) -> None:
    from sqlalchemy import text

    from fernkam import reference_tags
    from fernkam.services.stacks import detect_stacks

    print("\n4. Stacks and licensing Refs")
    if not dry_run:   # stack first, so a RAW and its JPG share one new Ref
        for r in roots:
            st = await detect_stacks(db, album_path=r or None)
            print(f"   {r or '(whole library)'}: {st['stacks_created']} new stacks, {st['photos_grouped']} pictures in stacks")
    ids = [i for (i,) in (await db.execute(text("""
        SELECT p.id FROM photos p WHERE p.status = 1 AND (p.album_path = ANY(:a) OR p.album_path LIKE ANY(:like))
          AND NOT EXISTS (SELECT 1 FROM photo_tags x JOIN tags t ON t.id = x.tag_id
                          WHERE x.photo_id = p.id AND t.path <@ 'Reference' AND nlevel(t.path) = 2)
        ORDER BY p.id"""), {"a": roots, "like": [f"{r}/%" if r else "%" for r in roots]})).all()]
    if not dry_run:
        for i in range(0, len(ids), 2000):
            await reference_tags.ensure(db, ids[i:i + 2000])
            await db.commit()
    print(f"   {len(ids)} pictures {'would get' if dry_run else 'got'} a Ref")
