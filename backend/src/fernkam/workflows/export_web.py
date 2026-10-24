"""Export for the web (Flickr and the like): resized copies that carry what a researcher searches for.

Originals are never touched. Every picture picked (one file, or each picture in a folder) becomes a JPEG
of at most EXPORT_MAX_EDGE px on its long edge in the export folder, with:
- title, caption and keywords from its catalogue row, or from the original it was edited from
  ("edited_DSC6748.jpg" -> "_DSC6748.jpg"), or from its species folder's name;
- research keywords: common and Latin name (GBIF's current name and English name too, and an older
  Latin name the caption uses), genus, family, order, class, plain words ("reptile", "herps"), Flickr
  machine tags (taxonomy:binomial=...), the subject's group, conservation status in IUCN and plain
  words, and the state, county and (outside the US) country where the GPS, caption or trip folder says so;
- the camera maker, model and lens ("Nikon", "Z9", "NIKKOR Z MC 105mm f/2.8 VR S"), or "film" for a scan;
- EXPORT_NOTICE / EXPORT_COPYRIGHT / EXPORT_CREATOR, "data mining prohibited", and a "Ref:" line with
  the original's Reference tag ("REF-272036", reference_tags), so a web copy always leads back to its file;
- GPS kept, as in the original (remove_gps strips it; Flickr can hide locations near home itself).
Keywords are flat names, never tag paths. People, branch roots (Camera, Status, Wildlife...) and
EXPORT_EXCLUDE_TAGS stay off. Flickr reads title, description and tags from these fields on upload.
"""
from __future__ import annotations

import asyncio
import json
import os
import re
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Optional

from fernkam.workflows.shared import RAW_EXTENSIONS, format_elapsed

IMAGES = {".jpg", ".jpeg", ".tif", ".tiff", ".png", ".webp"}   # what Pillow opens

_EDITED = re.compile(r"^(edited|edit|final|export)(?=[_\- ])|[_\- ]+(dxo.*|edited|edit|final|export|copy|enhanced.*|\(\d+\))$", re.I)
_SPECIES = re.compile(r"^(?P<common>[^()]+?)(?:\s+-\s+(?P<note>[^()]+?))?\s*\((?P<latin>[A-Z][a-z]+(?: [a-z.]+)*)\)\s*$")

# Words people type for a GBIF class ("herps": reptiles and amphibians).
PLAIN_CLASS = {"Mammalia": ["mammal"], "Aves": ["bird"], "Amphibia": ["amphibian", "herps"], "Insecta": ["insect"],
               **dict.fromkeys(("Reptilia", "Squamata", "Testudines", "Crocodylia"), ["reptile", "herps"]),
               "Arachnida": ["arachnid"], "Malacostraca": ["crustacean"], "Gastropoda": ["snail"],
               "Actinopterygii": ["fish"], "Elasmobranchii": ["fish"], "Chilopoda": ["centipede"], "Diplopoda": ["millipede"],
               **dict.fromkeys(("Magnoliopsida", "Liliopsida", "Pinopsida", "Polypodiopsida"), ["plant"])}
# Status fact tags (species_status) in the words a searcher uses.
PLAIN_STATUS = {"IUCN Critically Endangered": ["critically endangered", "endangered species"],
                "IUCN Endangered": ["endangered species"], "IUCN Vulnerable": ["threatened species"],
                "IUCN Near Threatened": ["near threatened"], "IUCN Extinct in the Wild": ["extinct in the wild"],
                "Introduced to the US": ["introduced species", "non-native species"]}
US_STATES = ("Alabama Alaska Arizona Arkansas California Colorado Connecticut Delaware Florida Georgia Hawaii Idaho "
             "Illinois Indiana Iowa Kansas Kentucky Louisiana Maine Maryland Massachusetts Michigan Minnesota "
             "Mississippi Missouri Montana Nebraska Nevada Ohio Oklahoma Oregon Pennsylvania Tennessee Texas Utah "
             "Vermont Virginia Washington Wisconsin Wyoming").split() + [
             "New Hampshire", "New Jersey", "New Mexico", "New York", "North Carolina", "North Dakota", "Rhode Island",
             "South Carolina", "South Dakota", "West Virginia"]
# Florida's 67 counties: a caption's misspelt one ("Wakula Co.") is matched to the real name.
FL_COUNTIES = ("Alachua Baker Bay Bradford Brevard Broward Calhoun Charlotte Citrus Clay Collier Columbia DeSoto Dixie "
               "Duval Escambia Flagler Franklin Gadsden Gilchrist Glades Gulf Hamilton Hardee Hendry Hernando Highlands "
               "Hillsborough Holmes Jackson Jefferson Lafayette Lake Lee Leon Levy Liberty Madison Manatee Marion Martin "
               "Monroe Nassau Okaloosa Okeechobee Orange Osceola Pasco Pinellas Polk Putnam Sarasota Seminole Sumter "
               "Suwannee Taylor Union Volusia Wakulla Walton Washington").split() + [
               "Indian River", "Miami-Dade", "Palm Beach", "Santa Rosa", "St. Johns", "St. Lucie"]
COUNTRIES = ["Mexico", "Costa Rica", "Belize", "Guatemala", "Honduras", "Panama", "Ecuador", "Peru", "Brazil",
             "Canada", "Bahamas", "Cuba", "Venezuela", "Colombia", "Argentina", "Trinidad"]


def core_stem(name: str) -> str:
    """'edited_DSC6748.jpg' -> '_DSC6748'; '_5_I7993-DxO_DeepPRIME 3.jpg' -> '_5_I7993'."""
    stem = Path(name).stem
    while (new := _EDITED.sub("", stem)) != stem:
        stem = new
    return stem


def _key(stem: str) -> str:
    return stem.lower().strip("_- ")


def split_name(name: str) -> list[str]:
    """'Wingtail Crayfish (Procambarus latipleurum)' -> both halves, as separate keywords."""
    m = re.match(r"^(.+?)\s*\((.+)\)\s*$", name)
    return [m[1].strip(), m[2].strip()] if m else [name]


def folder_species(folder: str) -> Optional[dict]:
    """'Wingtail Crayfish - Juvenile (Procambarus latipleurum)' -> common, note, latin."""
    m = _SPECIES.match(folder)
    return m.groupdict() if m else None


def _dedupe(words) -> list[str]:
    seen, out = set(), []
    for w in words:
        w = (w or "").strip()
        if w and w.lower() not in seen:
            seen.add(w.lower())
            out.append(w)
    return out


def run(path: str, out_dir: Optional[str] = None, max_edge: Optional[int] = None,
        remove_gps: bool = False, dry_run: bool = True) -> None:
    start = time.perf_counter()
    asyncio.run(_run_async(Path(path), out_dir, max_edge, remove_gps, dry_run))
    print(f"Process took: {format_elapsed(start)}")


async def _run_async(src: Path, out_dir, max_edge, remove_gps, dry_run) -> None:
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
    from sqlalchemy.pool import NullPool
    from fernkam.config import get_settings

    s = get_settings()
    lib = Path(s.library_root).resolve()
    out = Path(out_dir or s.export_dir or Path.home() / "Pictures" / "fernKam export").resolve()
    if out.is_relative_to(lib):
        print(f"ERROR: the export folder {out} is inside the library; fernKam would catalogue the copies. Pick one outside it.")
        return
    files = [src] if src.is_file() else sorted(p for p in src.iterdir() if p.is_file() and p.suffix.lower() in IMAGES)
    files = [f for f in files if f.suffix.lower() in IMAGES]
    if not files:
        print(f"ERROR: no pictures in {src}")
        return
    edge = max_edge or s.export_max_edge
    print(f"{'Preview: ' if dry_run else ''}{len(files)} picture(s) -> {out} (longest side {edge}px, GPS {'removed' if remove_gps else 'kept'})")
    # This runs in a worker thread under its own loop, so it gets its own unpooled engine
    # (see sync_stack_tags).
    engine = create_async_engine(s.pg_url, poolclass=NullPool)
    try:
        async with async_sessionmaker(engine)() as db:
            taxa: dict = {}
            for f in files:
                dst, meta, size = await export_one(db, f, lib, out, s, taxa, edge, remove_gps, dry_run)
                print(f"\n{f.name} -> {dst.name}\n   title: {meta['title']}\n   tags: {', '.join(meta['keywords'])}"
                      + (f"\n   from: {meta['source']}  Ref: {meta['ref']}" if meta["catalogued"]
                         else "\n   (not in the catalogue; tags from the file and folder)")
                      + (f"\n   wrote {size[0]}x{size[1]}" if size else ""))
    finally:
        await engine.dispose()


async def export_one(db, f: Path, lib: Path, out: Path, s, taxa: dict, edge: int,
                     remove_gps: bool = False, dry_run: bool = False) -> tuple[Path, dict, Optional[tuple[int, int]]]:
    """One web copy: (where it goes, its metadata, its size once written). A preview writes nothing."""
    meta = await _metadata(db, f, lib, s, taxa, assign_ref=not dry_run)
    dst = out / f"{f.stem}.jpg"
    if dry_run:
        return dst, meta, None
    out.mkdir(parents=True, exist_ok=True)
    size = await asyncio.to_thread(_resize, f, dst, edge)
    await asyncio.to_thread(_write, f, dst, meta, s, remove_gps)
    await db.commit()   # the new Ref, once the copy carrying it exists
    return dst, meta, size


async def _metadata(db, f: Path, lib: Path, s, taxa: dict, exif: Optional[dict] = None, row=None,
                    assign_ref: bool = False) -> dict:
    """Title, description and keywords for a picture, and Darwin Core fields for its first species.
    exif (Make, Model, LensModel) and row (the catalogue row) can be given instead of read.
    assign_ref gives the original a Reference tag if it has none (a preview only shows an existing one)."""
    from sqlalchemy import text

    from fernkam import reference_tags
    from fernkam.camera_tags import names as camera_names
    from fernkam.metadata_sync import _run_et

    row = row if row is not None else await _original(db, f, lib)
    if exif is None:
        exif = _run_et(["-Make", "-Model", "-LensModel", "-Lens", str(f)]) or {}
    first = None   # (taxon key, Latin, common name) of the first species, for Darwin Core
    excluded = {t.strip().lower() for t in s.export_exclude_tags.split(",") if t.strip()}
    kw: list[str] = []
    title, caption, species = None, None, []
    if row:
        title, caption = row.title, row.caption
        tags = (await db.execute(text("""
            SELECT t.name, t.is_person, ts.taxon_key, ts.scientific_name,
                   (SELECT array_agg(a.name ORDER BY nlevel(a.path)) FROM tags a
                     WHERE a.path @> t.path AND a.id <> t.id AND nlevel(a.path) > 1) AS ancestors,
                   (SELECT array_agg(a.name) FROM tags a WHERE a.path @> t.path) AS branch
            FROM photo_tags x JOIN tags t ON t.id = x.tag_id LEFT JOIN tag_species ts ON ts.tag_id = t.id
            WHERE x.photo_id = :p AND (x.verified_at IS NOT NULL OR t.is_fact)
              AND NOT (t.path <@ 'Camera') AND NOT (t.path <@ 'Reference')"""), {"p": row.id})).all()
        for t in tags:
            if t.is_person or {b.lower() for b in t.branch or []} & excluded:
                continue
            latin = t.scientific_name or (folder_species(t.name) or {}).get("latin")
            if key := t.taxon_key or await _match_key(latin, taxa):   # a tag not linked to GBIF yet: look up its Latin name
                species.append(t.name)
                first = first or (key, latin, split_name(t.name)[0])
                kw += split_name(t.name) + await _taxonomy(key, latin, taxa, split_name(t.name)[0])
            else:
                kw += split_name(t.name)
            kw += [n for a in (t.ancestors or []) for n in split_name(a)]
        kw += [row.state] if row.state else []
    folder = folder_species(f.parent.name)
    if not species and folder:  # not tagged yet: the species folder names it
        kw += [folder["common"], folder["latin"], folder["note"] or ""]
        kw += await _from_species_tag(db, folder["latin"], taxa, folder["common"])
        species.append(f"{folder['common']} ({folder['latin']})")
        if key := await _match_key(folder["latin"], taxa):
            first = (key, folder["latin"], folder["common"])
            kw += await _taxonomy(key, folder["latin"], taxa, folder["common"])
    kw += [w for k in kw for w in PLAIN_STATUS.get(k, [])]
    where = places(f"{caption or ''} {row.state or ''}" if row else "", row.album_path if row else "", species)
    if row and getattr(row, "county", None) and row.county not in where:
        where.insert(0, row.county)   # the county its GPS falls in (us_counties), ahead of one a caption names
    kw += where
    kw += [n for n in old_latin(caption or "", kw) if await same_species(n, first, taxa)]
    if any(re.search(r"\bcave\b", n, re.I) for n in species):
        kw += ["cave", "cave fauna"]
    # A film scan's "camera" is the scanner (Coolscan...); saying "Nikon" would mislead.
    if "scan" not in (exif.get("Model") or "").lower():
        kw += list(camera_names(exif.get("Make"), exif.get("Model")) or ())
        kw.append(exif.get("LensModel") or exif.get("Lens") or "")
    else:
        kw += ["film", "slide film"]
    kw += [t.strip() for t in s.export_extra_tags.split(",")]
    if not title:
        title = species[0] if len(species) == 1 else (f"{folder['common']} ({folder['latin']})" if folder else core_stem(f.name))
    ref = f.name
    if row:
        ref = (await reference_tags.ensure(db, [row.id], assign_ref)).get(row.id) or f"{reference_tags.prefix(row.album_path)}-(new)"
    notice = s.export_notice.replace("\\n", "\n").strip()
    desc = "\n\n".join(x for x in (caption, notice, f"Ref: {ref}") if x)
    return {"title": title, "description": desc, "ref": ref, "catalogued": bool(row),
            "source": f"{row.album_path}/{row.filename}" if row else f.name,
            "keywords": [k for k in _dedupe(kw) if k.lower() not in excluded], "dwc": _dwc(first, taxa, where)}


def _dwc(first, taxa: dict, where: list[str]) -> dict:
    """Darwin Core (XMP-dwc) for the first species: the biodiversity standard's names for these fields."""
    if not first:
        return {}
    key, latin, common = first
    t = taxa.get(key) or {}
    same_rank = len((t.get("species") or "").split()) == len((latin or "").split())
    binomial = t.get("species") if t.get("species") and same_rank else latin
    states = [w for w in where if w in US_STATES]
    countries = [w for w in where if w in COUNTRIES] or (["United States"] if states else [])
    d = {"TaxonScientificName": binomial, "TaxonVernacularName": common, "TaxonGenus": t.get("genus"),
         "TaxonFamily": t.get("family"), "TaxonOrder": t.get("order"), "TaxonClass": t.get("class"),
         "DCCountry": countries[0] if countries else None, "DCStateProvince": states[0] if states else None,
         "DCCounty": next((w for w in where if w.endswith((" County", " Parish", " Borough"))), None)}
    return {k: v for k, v in d.items() if v}


async def _original(db, f: Path, lib: Path):
    """The catalogue row for this file, or for the original it was edited from (same folder or its RAW/)."""
    from sqlalchemy import text

    cols = "id, album_path, filename, title, caption, state, county"
    rf = f.resolve()
    if rf.is_relative_to(lib):
        album = rf.parent.relative_to(lib).as_posix()
        rows = (await db.execute(text(f"SELECT {cols} FROM photos WHERE status = 1 AND album_path IN (:a, :a || '/RAW')"),
                                 {"a": album})).all()
        exact = next((r for r in rows if r.album_path == album and r.filename == f.name), None)
    else:
        exact = None
        rows = (await db.execute(text(f"SELECT {cols} FROM photos WHERE status = 1 AND filename ILIKE :n"),
                                 {"n": f"%{core_stem(f.name).strip('_- ')}%"})).all()
    key = _key(core_stem(f.name))
    hits = [r for r in rows if _key(Path(r.filename).stem) == key and r is not exact]
    if len({r.album_path.removesuffix("/RAW") for r in hits}) != 1:
        return exact  # no original, or the same name in several folders: don't guess
    return sorted(hits, key=lambda r: Path(r.filename).suffix.lower() in RAW_EXTENSIONS)[0]


async def _from_species_tag(db, latin: str, taxa: dict, common: str = "") -> list[str]:
    """For a folder-named species: the matching species tag's group and the species' status."""
    from sqlalchemy import text

    from fernkam.species_status import status_names

    r = (await db.execute(text("""
        SELECT ts.taxon_key, ts.scientific_name, ss.iucn, ss.introduced_us,
               (SELECT array_agg(a.name ORDER BY nlevel(a.path)) FROM tags a
                 WHERE a.path @> t.path AND a.id <> t.id AND nlevel(a.path) > 1) AS ancestors
        FROM tags t JOIN tag_species ts ON ts.tag_id = t.id LEFT JOIN species_status ss ON ss.tag_id = t.id
        WHERE lower(ts.scientific_name) = lower(:l) OR t.name ILIKE :n LIMIT 1"""),
        {"l": latin, "n": f"%({latin})"})).first()
    if not r:
        return [latin.split()[0]]
    return ([*(r.ancestors or []), *status_names(r.iucn, r.introduced_us)]
            + await _taxonomy(r.taxon_key, r.scientific_name, taxa, common))


async def _taxonomy(key: int, latin: str, taxa: dict, common: str = "") -> list[str]:
    """GBIF's current name (when it has moved) and English name, genus, family, order, class, the class in
    plain words, and Flickr machine tags. Only the taxon key goes to GBIF."""
    if key not in taxa:
        from fernkam.species_range import _base, _client, get
        try:
            async with _client() as c:
                d = (await get(c, f"{_base()}/species/{key}")).json()
            taxa[key] = {r: d.get(r) or "" for r in ("species", "genus", "family", "order", "class", "vernacularName")}
            taxa[key]["moved"] = d.get("taxonomicStatus", "").endswith("SYNONYM")
        except Exception:  # noqa: BLE001 — offline: export without the extra ranks
            taxa[key] = {"genus": (latin or "").split()[0] if latin else ""}
    t = taxa[key]
    now = t["species"] if (t.get("species") or "").lower() not in ("", (latin or "").lower()) else ""   # moved or respelt
    english = (t.get("vernacularName") or "").split(",")[0].strip()
    binomial = now or (latin if latin and " " in latin and "." not in latin else "")
    machine = [f'taxonomy:binomial="{binomial}"' if binomial else "",
               *(f"taxonomy:{r}={t[r]}" for r in ("genus", "family", "order", "class") if t.get(r)),
               f'taxonomy:common="{common}"' if common else ""]
    plain = PLAIN_CLASS.get(t.get("class") or "", []) + (["spider"] if t.get("order") == "Araneae" else [])
    english = " ".join(w[:1].upper() + w[1:] for w in english.split())   # "Davy's Naked-backed Bat", not .title()'s "Davy'S Naked-Backed"
    return [x for x in (now, english, t.get("genus"), t.get("family"), t.get("order"), t.get("class"),
                        *plain, *machine) if x]


async def _match_key(latin: str | None, taxa: dict) -> int | None:
    """GBIF's key for a Latin name, for a tag that isn't linked yet: a species, or a genus or family
    folder he named ("Tantilla", "Lycosidae", "Taxodium sp."). An exact match, or GBIF's spelling of the
    same species when only the ending differs ("Heterodon platyrhinos" -> platirhinos), which is noted
    in taxa[("spelled", name)]. "cf." / "aff." are ignored."""
    name = re.sub(r"\s+(cf|aff)\.", "", re.sub(r"\s+sp\.?$", "", latin or "")).strip()
    if not name or "/" in name or "." in name:
        return None
    if ("match", name) not in taxa:
        from difflib import SequenceMatcher
        from fernkam.species_range import _base, _client, get
        try:
            async with _client() as c:
                d = (await get(c, f"{_base()}/species/match", params={"name": name, "strict": "true"})).json()
            canon, words = (d.get("canonicalName") or ""), name.split()
            respelt = (d.get("matchType") == "FUZZY" and len(words) > 1 and canon.split()[:1] == words[:1]
                       and len(canon.split()) == len(words) and canon.split()[-1][:3] == words[-1][:3]
                       and SequenceMatcher(None, canon.split()[-1], words[-1]).ratio() >= 0.75)
            ok = (canon.lower() == name.lower() or respelt) and d.get("confidence", 0) >= 90 and \
                (len(words) == 1 or d.get("rank") in ("SPECIES", "SUBSPECIES"))
            taxa[("match", name)] = d.get("usageKey") if ok else None
            if ok and respelt:
                taxa[("spelled", name)] = canon
        except Exception:  # noqa: BLE001 — offline: no extra ranks
            taxa[("match", name)] = None
    return taxa[("match", name)]


def places(caption: str, album_path: str, names: list[str]) -> list[str]:
    """States, countries and counties the caption or a trip folder names. Species names ("Virginia
    Opossum", "Texas Spiny Lizard") and species folders don't count; nothing is guessed from a range."""
    text = caption
    for n in names:
        for part in split_name(n):
            text = re.sub(re.escape(part), " ", text, flags=re.I)
    text += " " + " ".join(seg for seg in album_path.split("/") if "(" not in seg)
    found = [p for p in US_STATES + COUNTRIES if re.search(rf"(?<!West )\b{p}\b", text)]
    if "Florida" not in found and re.search(r"\b(FL|Fla\.)(?=\W|$)", text):
        found.append("Florida")
    return found + [f"{_county(m)} County" for m in re.findall(r"\b([A-Z][a-z]+(?: [A-Z][a-z]+)?) (?:Co\.|County)", text)]


def _county(name: str) -> str:
    """A county as the caption names it, without "Northern" etc., and a near-miss of a Florida county
    spelt right ("Swannee" -> "Suwannee"). Other states' counties are kept as written."""
    from difflib import get_close_matches
    name = re.sub(r"^(North|South|East|West|Central|Northern|Southern|Eastern|Western)\s+", "", name)
    return (get_close_matches(name, FL_COUNTIES, n=1, cutoff=0.8) or [name])[0]


async def same_species(name: str, first, taxa: dict) -> bool:
    """His caption's Latin name is an older name of the photo's species (GBIF: Bufo terrestris is
    Anaxyrus terrestris), not another species he misnamed (Calocitta formosa on a Black-throated
    Magpie-Jay), which must not become a keyword."""
    if not first or not (key := await _match_key(name, taxa)):
        return False
    await _taxonomy(key, name, taxa)
    await _taxonomy(first[0], first[1], taxa)
    a, b = (taxa.get(key) or {}).get("species"), (taxa.get(first[0]) or {}).get("species")
    return bool(a) and a == b


def old_latin(caption: str, have: list[str]) -> list[str]:
    """A Latin name in the caption that the keywords don't have yet (his older name, e.g. Bufo terrestris)."""
    known = {k.lower() for k in have}
    return [m for m in re.findall(r"\(\s*([A-Z][a-z]+ [a-z]{3,})\s*\)", caption) if m.lower() not in known]


def _resize(src: Path, dst: Path, edge: int) -> tuple[int, int]:
    from PIL import Image, ImageOps

    with Image.open(src) as im:
        icc = im.info.get("icc_profile")
        im = ImageOps.exif_transpose(im)        # upright pixels; the copy gets no Orientation tag
        if im.mode not in ("RGB", "L"):
            im = im.convert("RGB")
        im.thumbnail((edge, edge), Image.LANCZOS)
        im.save(dst, "JPEG", quality=90, optimize=True, icc_profile=icc)
        return im.size


def _write(src: Path, dst: Path, meta: dict, s, remove_gps: bool) -> None:
    from fernkam.metadata_sync import _et

    et = _et()
    if not et:
        raise RuntimeError("exiftool not found")
    # 1. Camera, lens, dates and the rest from the original; orientation is already applied.
    copy = [et, "-q", "-overwrite_original", "-TagsFromFile", str(src), "-all:all", "-unsafe",
            "--Orientation", "--ThumbnailImage", "--PreviewImage",
            # keyword paths ("Camera|Nikon|Z9") stay home; the copy gets flat keywords below
            "--HierarchicalSubject", "--XMP-digiKam:all", "--XMP-microsoft:LastKeywordXMP", "--XMP-mediapro:CatalogSets"]
    if remove_gps:
        copy += ["--gps:all", "--xmp:GPS*"]
    subprocess.run([*copy, str(dst)], check=True, capture_output=True, timeout=60)
    # 2. What this copy says about itself.
    payload = {"SourceFile": str(dst), "CodedCharacterSet": "UTF8",
               "Title": meta["title"], "ObjectName": meta["title"][:64], "Headline": meta["title"],
               "Description": meta["description"], "ImageDescription": meta["description"],
               "Caption-Abstract": meta["description"][:2000],
               "Subject": meta["keywords"], "Keywords": [k[:64] for k in meta["keywords"]],
               "XMP-plus:DataMining": "Prohibited", **{f"XMP-dwc:{k}": v for k, v in meta.get("dwc", {}).items()}}
    if s.export_copyright:
        payload.update(Copyright=s.export_copyright, CopyrightNotice=s.export_copyright, Rights=s.export_copyright)
    if s.export_creator:
        payload.update(Artist=s.export_creator, Creator=s.export_creator, **{"By-line": s.export_creator})
    fd, tmp = tempfile.mkstemp(suffix=".json")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump([payload], fh, ensure_ascii=False)
        subprocess.run([et, "-q", "-overwrite_original", f"-json={tmp}", str(dst)], check=True, capture_output=True, timeout=60)
    finally:
        os.unlink(tmp)
