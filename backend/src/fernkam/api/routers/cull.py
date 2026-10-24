"""Cull mode and what follows it: species tags while culling, To do, filing into species
folders, opening a photo in the editor, and making a web copy to upload to Flickr.

The species tag decides the folder. A photo tagged "Wingtail Crayfish (Procambarus latipleurum)"
files into PORTFOLIO_FOLDER/<group folder>/<species folder>/ with its RAW in RAW/ and its DNG in
dng/ (the library's convention), so a shoot can be tagged while it is still in AA_RAW and filed
in one step. Its other versions (a PureRAW jpg/, an edited_ copy, sidecars) go along with it.
"""
from __future__ import annotations

import csv
import os
import re
import shutil
import subprocess
from collections import Counter
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Body, HTTPException, Query
from sqlalchemy import text

from fernkam import auto_write
from fernkam.api.deps import DB
from fernkam.config import BACKEND_DIR, get_settings
from fernkam.media_types import RAW_EXTENSIONS
from fernkam.workflows.export_web import IMAGES, _key, core_stem, folder_species

router = APIRouter()

# A species tag: Wildlife.<group>.<species ...>, not a fact tag.
_SPECIES = "t.path <@ 'Wildlife' AND nlevel(t.path) >= 3 AND NOT t.is_fact"
_SUBDIRS = ("", "jpg", "RAW", "dng")          # where a photo's versions live, beside it or below
_EXT = re.compile(r"(\.[A-Za-z0-9]{1,5})+$")  # ".NEF", ".NEF.dop", ".jpg.xmp"


def file_key(name: str) -> str:
    """The shot a file belongs to: "_DSC7763.NEF.dop", "edited_DSC7763.jpg" and
    "_DSC7763-DxO_DeepPRIME 3.dng" are all "dsc7763"."""
    return _key(core_stem(_EXT.sub("", name) + ".x"))


def subdir_for(name: str) -> str:
    """Where a version lives in a species folder: RAW/, dng/, or beside the folder's pictures."""
    ext = _EXT.search(name)
    first = ext[0].split(".")[1].lower() if ext else ""
    return "dng" if first == "dng" else "RAW" if f".{first}" in RAW_EXTENSIONS else ""


def base_dir(album_dir: Path) -> Path:
    """The folder a photo belongs to: its jpg/, RAW/ or dng/ subfolder counts as the folder itself."""
    return album_dir.parent if album_dir.name.lower() in {d.lower() for d in _SUBDIRS if d} else album_dir


def versions(album_dir: Path, filename: str) -> list[Path]:
    """Every file of this shot in its folder and the folder's jpg/, RAW/ and dng/."""
    base, key = base_dir(album_dir), file_key(filename)
    out = []
    for sub in _SUBDIRS:
        d = base / sub if sub else base
        if d.is_dir():
            out += [p for p in d.iterdir() if p.is_file() and file_key(p.name) == key]
    return out


def group_folder(group: str, existing: list[str]) -> str:
    """The species root's folder for a tag group: one already there with the same name, singular
    or plural ("Snakes" -> "Snake", "Butterflies" -> "Butterfly"), else the group's own name."""
    g = group.lower()
    for f in existing:
        x = f.lower()
        if g in (x, x + "s", x + "es", x[:-1] + "ies"):
            return f
    return group


def species_folder_of(album_path: str) -> Optional[str]:
    """The album path up to its "Common (Latin)" folder, or None."""
    parts = album_path.split("/")
    for i in range(len(parts), 0, -1):
        if folder_species(parts[i - 1]):
            return "/".join(parts[:i])
    return None


# ── Species picking ──────────────────────────────────────────────────────────

@router.get("/groups")
async def groups(db: DB) -> list[dict]:
    """Wildlife's groups (Birds, Crayfish, ...) with how many species each holds."""
    rows = (await db.execute(text("""
        SELECT g.id, g.name, (SELECT count(*) FROM tags t WHERE t.path <@ g.path AND t.id <> g.id) AS n
        FROM tags g WHERE g.path <@ 'Wildlife' AND nlevel(g.path) = 2 AND NOT g.is_fact ORDER BY g.name"""))).all()
    return [{"id": r.id, "name": r.name, "n": r.n} for r in rows]


@router.get("/species")
async def species(db: DB, group_id: Optional[int] = Query(None), q: str = Query("")) -> list[dict]:
    """A group's species (and sub-groups), or every species whose name holds q."""
    if q.strip():
        rows = (await db.execute(text(f"""
            SELECT t.id, t.name, g.name AS grp, 0 AS kids FROM tags t
            JOIN tags g ON g.path = subpath(t.path, 0, 2)
            WHERE {_SPECIES} AND t.name ILIKE :q ORDER BY t.name LIMIT 40"""), {"q": f"%{q.strip()}%"})).all()
    else:
        rows = (await db.execute(text("""
            SELECT t.id, t.name, p.name AS grp, (SELECT count(*) FROM tags c WHERE c.parent_id = t.id) AS kids
            FROM tags t JOIN tags p ON p.id = t.parent_id
            WHERE t.parent_id = :g AND NOT t.is_fact ORDER BY t.name"""), {"g": group_id})).all()
    return [{"id": r.id, "name": r.name, "group": r.grp, "kids": r.kids} for r in rows]


async def _todo_tag(db) -> int:
    """The To do fact tag: photos to come back to (an edit in the photo editor, say)."""
    tid = (await db.execute(text("SELECT id FROM tags WHERE path = 'To_do'"))).scalar()
    if tid is None:
        tid = (await db.execute(text(
            "INSERT INTO tags (name, path, is_person, is_fact) VALUES ('To do', 'To_do', false, true) RETURNING id"
        ))).scalar()
        await db.commit()
    return tid


@router.get("/photo/{photo_id}")
async def photo_info(photo_id: int, db: DB) -> dict:
    """What Cull mode shows beside a photo: its species, To do, and the species it looks like."""
    from fernkam.embed_index import space
    from fernkam.tag_learning import _hnsw_settings

    todo = await _todo_tag(db)
    tags = (await db.execute(text(f"""
        SELECT t.id, t.name, x.verified_at IS NOT NULL AS ok, t.id = :todo AS is_todo
        FROM photo_tags x JOIN tags t ON t.id = x.tag_id
        WHERE x.photo_id = :p AND (({_SPECIES}) OR t.id = :todo)"""), {"p": photo_id, "todo": todo})).all()
    # Nearest photos that already have an approved species, by BioCLIP (it tells species apart),
    # else CLIP; their species, weighted by closeness.
    suggestions: list[dict] = []
    for key in ("bioclip2", "clip"):
        sp = space(key)
        v = (await db.execute(text(sp.vector_sql()), {"ids": [photo_id]})).first()
        if not v:
            continue
        await _hnsw_settings(db, 40)
        where = (f"p.id <> {int(photo_id)} AND EXISTS (SELECT 1 FROM photo_tags x JOIN tags t ON t.id = x.tag_id "
                 f"WHERE x.photo_id = p.id AND x.verified_at IS NOT NULL AND {_SPECIES})")
        nb = (await db.execute(text(sp.nearest_sql(where)), {"w": v[1], "k": 40})).all()
        if not nb:
            continue
        score = {r[0]: max(float(r[1]), 0.0) for r in nb}
        rows = (await db.execute(text(f"""
            SELECT x.photo_id, t.id, t.name, g.name AS grp FROM photo_tags x JOIN tags t ON t.id = x.tag_id
            JOIN tags g ON g.path = subpath(t.path, 0, 2)
            WHERE x.photo_id = ANY(:ids) AND x.verified_at IS NOT NULL AND {_SPECIES}"""), {"ids": list(score)})).all()
        weight: Counter = Counter()
        names = {}
        for r in rows:
            weight[r.id] += score[r.photo_id]
            names[r.id] = (r.name, r.grp)
        total = sum(weight.values()) or 1
        suggestions = [{"id": t, "name": names[t][0], "group": names[t][1], "share": round(w / total, 2)}
                       for t, w in weight.most_common(6)]
        break
    return {"todo_tag_id": todo, "todo": any(t.is_todo for t in tags),
            "species": [{"id": t.id, "name": t.name, "approved": t.ok} for t in tags if not t.is_todo],
            "suggestions": suggestions}


async def shot_ids(db, photo_ids: list[int]) -> list[int]:
    """The photos plus their other catalogued versions: the NEF, its PureRAW jpg/ and dng/, an edit."""
    rows = (await db.execute(text("SELECT id, album_path, filename FROM photos WHERE id = ANY(:ids)"),
                             {"ids": photo_ids})).all()
    keys: dict[str, set[str]] = {}
    for r in rows:
        keys.setdefault(base_dir(Path(r.album_path)).as_posix(), set()).add(file_key(r.filename))
    out = set(photo_ids)
    for base, ks in keys.items():
        albums = [base] + [f"{base}/{d}" for d in _SUBDIRS if d]
        sib = (await db.execute(text("SELECT id, filename FROM photos WHERE status = 1 AND album_path = ANY(:a)"),
                                {"a": albums})).all()
        out |= {s.id for s in sib if file_key(s.filename) in ks}
    return sorted(out)


@router.post("/tag")
async def set_tag(db: DB, photo_ids: list[int] = Body(...), tag_id: Optional[int] = Body(None),
                  on: bool = Body(True)) -> dict:
    """Add (approved) or remove a tag on photos and every version of their shot, so the RAW, the
    PureRAW JPG and the DNG agree and the tag files and exports with whichever is used.
    No tag_id: the To do tag."""
    tag_id = tag_id or await _todo_tag(db)
    ids = await shot_ids(db, photo_ids)
    if on:
        await db.execute(text("""
            INSERT INTO photo_tags (photo_id, tag_id, verified_at) SELECT unnest(CAST(:ids AS bigint[])), :t, now()
            ON CONFLICT (photo_id, tag_id) DO UPDATE SET verified_at = COALESCE(photo_tags.verified_at, now())"""),
            {"ids": ids, "t": tag_id})
        for table in ("tag_suggestions", "tag_rejections"):
            await db.execute(text(f"DELETE FROM {table} WHERE tag_id = :t AND photo_id = ANY(:ids)"),
                             {"ids": ids, "t": tag_id})
    else:
        await db.execute(text("DELETE FROM photo_tags WHERE tag_id = :t AND photo_id = ANY(:ids)"),
                         {"ids": ids, "t": tag_id})
    await db.execute(text("UPDATE photos SET file_sync_dirty = true WHERE id = ANY(:ids)"), {"ids": ids})
    await db.commit()
    auto_write.queue(ids)
    return {"photos": ids}


# ── Filing ───────────────────────────────────────────────────────────────────

async def _destination(db, tag_id: int, name: str, group: str, lib: Path, root: str) -> str:
    """Where a species files: the species folder already holding most of it, else a folder with
    its Latin name, else a new one named after the tag in its group's folder."""
    rows = (await db.execute(text("""
        SELECT p.album_path, count(*) AS n FROM photos p JOIN photo_tags x ON x.photo_id = p.id
        WHERE x.tag_id = :t AND x.verified_at IS NOT NULL AND p.status = 1 AND starts_with(p.album_path, :r)
        GROUP BY 1 ORDER BY 2 DESC"""), {"t": tag_id, "r": root + "/"})).all()
    for r in rows:
        if folder := species_folder_of(r.album_path):
            return folder
    latin = (folder_species(name) or {}).get("latin")
    top = sorted(d.name for d in (lib / root).iterdir() if d.is_dir()) if (lib / root).is_dir() else []
    for g in top:
        for d in (lib / root / g).iterdir():
            if d.is_dir() and latin and (folder_species(d.name) or {}).get("latin") == latin:
                return f"{root}/{g}/{d.name}"
    return f"{root}/{group_folder(group, top)}/{name}"


@router.post("/file")
async def file_to_species(db: DB, photo_ids: list[int] = Body(...), dry_run: bool = Body(True)) -> dict:
    """Move each photo, with its other versions, into its species folder. Photos without exactly
    one approved species tag stay put and are listed. Nothing is overwritten."""
    from fernkam.task_manager import task_manager

    s = get_settings()
    lib, root = Path(s.library_root), s.portfolio_folder.strip("/")
    rows = (await db.execute(text(f"""
        SELECT p.id, p.album_path, p.filename,
               array_agg(t.id) FILTER (WHERE t.id IS NOT NULL) AS tids,
               array_agg(t.name) FILTER (WHERE t.id IS NOT NULL) AS names,
               array_agg(g.name) FILTER (WHERE t.id IS NOT NULL) AS grps
        FROM photos p
        LEFT JOIN photo_tags x ON x.photo_id = p.id AND x.verified_at IS NOT NULL
        LEFT JOIN tags t ON t.id = x.tag_id AND {_SPECIES}
        LEFT JOIN tags g ON g.path = subpath(t.path, 0, 2)
        WHERE p.id = ANY(:ids) AND p.status = 1 GROUP BY p.id"""), {"ids": photo_ids})).all()
    plan, skipped, dest_of, planned = [], [], {}, set()
    for r in rows:
        tids = [t for t in (r.tids or []) if t]
        if len(set(tids)) != 1:
            skipped.append({"id": r.id, "file": r.filename,
                            "why": "no species tag yet" if not tids else "more than one species"})
            continue
        if tids[0] not in dest_of:
            dest_of[tids[0]] = await _destination(db, tids[0], r.names[0], r.grps[0], lib, root)
        dest = dest_of[tids[0]]
        if species_folder_of(r.album_path) == dest:
            continue   # already filed
        found = versions(lib / r.album_path, r.filename)
        if not found:
            skipped.append({"id": r.id, "file": r.filename, "why": "not on disk"})
            continue
        files = [f for f in found if f not in planned]
        if not files:
            continue   # it moves with another version of the same shot (the NEF and its JPG both picked)
        planned.update(files)
        moves = [(f, lib / dest / subdir_for(f.name) / f.name) for f in files]
        if clash := next((d for _, d in moves if d.exists()), None):
            skipped.append({"id": r.id, "file": r.filename, "why": f"{clash.name} is already in {dest}"})
            continue
        plan.append({"id": r.id, "file": r.filename, "species": r.names[0], "dest": dest,
                     "moves": [(str(a), str(b)) for a, b in moves]})
    summary = {"to_file": len(plan), "skipped": skipped,
               "folders": dict(Counter(p["dest"] for p in plan)),
               "new_folders": sorted({p["dest"] for p in plan if not (lib / p["dest"]).is_dir()})}
    if dry_run:
        return summary

    task_manager.ensure_no_file_task()   # a scan must not see a file half moved
    filed, moved_ids, errors, emptied = [], [], [], set()
    for p in plan:
        try:
            for a, b in p["moves"]:
                src, dst = Path(a), Path(b)
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.move(src, dst)
                old = src.parent.relative_to(lib).as_posix()
                new = dst.parent.relative_to(lib).as_posix()
                moved_ids += (await db.execute(text(
                    "UPDATE photos SET album_path = :n WHERE album_path = :o AND filename = :f RETURNING id"),
                    {"n": new, "o": old, "f": src.name})).scalars().all()
                emptied.add(src.parent)
            await db.execute(text("""
                UPDATE photo_stacks st SET album_path = :d FROM photos p
                WHERE p.id = :id AND st.id = p.stack_id
                  AND NOT EXISTS (SELECT 1 FROM photo_stacks o WHERE o.album_path = :d AND o.stem_key = st.stem_key)"""),
                {"d": p["dest"], "id": p["id"]})
            await db.commit()
            filed.append(p["id"])
        except OSError as exc:
            await db.commit()   # what did move stays recorded
            errors.append(f"{p['file']}: {exc}")
    # Shoot folders left empty (and their empty jpg/ RAW/ dng/) go; top-level ones like AA_RAW stay.
    for d in sorted(emptied, key=lambda x: len(x.parts), reverse=True):
        for x in (d, base_dir(d)):
            try:
                if x.is_relative_to(lib) and len(x.relative_to(lib).parts) > 1 and x.is_dir() and not any(x.iterdir()):
                    x.rmdir()
            except OSError:
                pass
    return {**summary, "filed": filed, "moved_ids": moved_ids, "errors": errors}


# ── Edit and post ────────────────────────────────────────────────────────────

async def _row(db, photo_id: int):
    r = (await db.execute(text("SELECT id, album_path, filename FROM photos WHERE id = :p"), {"p": photo_id})).first()
    if not r:
        raise HTTPException(404, "Photo not found")
    return r


@router.post("/edit/{photo_id}")
async def open_in_editor(photo_id: int, db: DB) -> dict:
    """Open exactly this file in the editor (Affinity Photo 2 by default)."""
    r = await _row(db, photo_id)
    target = Path(get_settings().library_root) / r.album_path / r.filename
    if not target.exists():
        raise HTTPException(404, f"Not on disk: {target}")
    exe = get_settings().editor_exe.strip().strip('"') or shutil.which("AffinityPhoto2.exe")
    try:
        if exe:
            subprocess.Popen([exe, str(target)])
        else:
            os.startfile(str(target))   # type: ignore[attr-defined]  # Windows: the file's own app
    except OSError as exc:
        raise HTTPException(500, f"Could not open the editor: {exc}")
    return {"opened": str(target)}


@router.post("/export")
async def export_for_flickr(db: DB, photo_ids: list[int] = Body(..., embed=True)) -> dict:
    """Export for Web on exactly these pictures: a copy of each, at most EXPORT_MAX_EDGE px, with
    title, tags, rights and Ref (workflows/export_web.py), in the export folder, ready to upload by
    hand. Nothing is sent anywhere, but each exported picture is marked as on Flickr (the user
    uploads what they export). A NEF or DNG is skipped; so is a picture not on disk."""
    from fernkam.workflows import export_web

    s = get_settings()
    lib = Path(s.library_root)
    out = _export_dir(s, lib)
    rows = (await db.execute(text("SELECT id, album_path, filename FROM photos WHERE id = ANY(:ids)"),
                             {"ids": photo_ids})).all()
    log = BACKEND_DIR / "data" / "flickr_map.csv"   # what is already on Flickr, if you keep that list
    on_flickr_ids = {row["photo_id"] for row in csv.DictReader(log.open(encoding="utf-8"))} if log.exists() else set()
    saved, skipped, on_flickr, taxa, exported = [], [], [], {}, []
    for r in rows:
        src = lib / r.album_path / r.filename
        if src.suffix.lower() not in IMAGES:
            skipped.append(f"{r.filename}: not a JPEG, TIFF or PNG")
            continue
        if not src.exists():
            skipped.append(f"{r.filename}: not on disk")
            continue
        dst, _, _ = await export_web.export_one(db, src, lib, out, s, taxa, s.export_max_edge)
        saved.append(dst.name)
        exported.append(r)
        if {str(i) for i in await shot_ids(db, [r.id])} & on_flickr_ids:
            on_flickr.append(r.filename)
    tags = await _flickr_mark(db, exported, True)
    await db.commit()
    auto_write.queue([r.id for r in exported])
    return {"folder": str(out), "saved": saved, "skipped": skipped, "on_flickr": on_flickr, "tags": sorted(tags)}


def _export_dir(s, lib: Path) -> Path:
    """EXPORT_DIR (blank = <home>/Pictures/fernKam export); never inside the library, which would
    catalogue every copy as a new photo."""
    out = Path(s.export_dir or Path.home() / "Pictures" / "fernKam export")
    if out.resolve().is_relative_to(lib.resolve()):
        raise HTTPException(400, f"The export folder {out} is inside the library; pick one outside it (EXPORT_DIR)")
    return out


async def _flickr_mark(db, rows, on: bool) -> set[str]:
    """The FLICKR_TAG fact tag (ARCHIVE_FLICKR_TAG for the archive's photos) on these photo rows
    (id, album_path), or off; flagged for DB → Files. Returns the tag names used. Doesn't commit."""
    from fernkam.reference_tags import in_archive
    from fernkam.sync_merge import ensure_tag_path

    s, cache, used = get_settings(), {}, set()
    names = sorted({s.flickr_tag, s.archive_flickr_tag or s.flickr_tag})
    ids = [r.id for r in rows]
    if on:
        for r in rows:
            tag = await ensure_tag_path(db, [(in_archive(r.album_path) and s.archive_flickr_tag) or s.flickr_tag], cache)
            await db.execute(text("""
                INSERT INTO photo_tags (photo_id, tag_id, verified_at) VALUES (:p, :t, now())
                ON CONFLICT (photo_id, tag_id) DO UPDATE SET verified_at = COALESCE(photo_tags.verified_at, now())"""),
                {"p": r.id, "t": tag.id})
            used.add(tag.name)
        await db.execute(text("UPDATE tags SET is_fact = true WHERE parent_id IS NULL AND name = ANY(:n) AND NOT is_fact"),
                         {"n": names})
    else:
        await db.execute(text("""DELETE FROM photo_tags x USING tags t WHERE t.id = x.tag_id AND t.parent_id IS NULL
                                 AND t.name = ANY(:n) AND x.photo_id = ANY(:ids)"""), {"n": names, "ids": ids})
        used = set(names)
    await db.execute(text("UPDATE photos SET file_sync_dirty = true WHERE id = ANY(:ids)"), {"ids": ids})
    return used


@router.post("/flickr-mark")
async def mark_on_flickr(db: DB, photo_ids: list[int] = Body(...), on: bool = Body(True)) -> dict:
    """Mark exactly these pictures as on Flickr, or clear it (_flickr_mark). Export for Flickr marks
    what it exports; this is for uploads made another way, and for taking the mark off."""
    rows = (await db.execute(text("SELECT id, album_path FROM photos WHERE id = ANY(:ids)"), {"ids": photo_ids})).all()
    tags = await _flickr_mark(db, rows, on)
    await db.commit()
    auto_write.queue([r.id for r in rows])
    return {"photos": [r.id for r in rows], "tags": sorted(tags)}


def share_copy(src: Path, dst: Path) -> None:
    """A smaller copy of a picture at full resolution, for sharing: JPEG quality 85, 4:2:0,
    progressive, with its camera info, colour profile and keywords. A 45 MP PureRAW JPEG
    (quality ~97, 4:4:4) comes out at about 40% of its size."""
    from PIL import Image, ImageOps

    Image.MAX_IMAGE_PIXELS = None   # big slide scans
    with Image.open(src) as f:      # closed after, so Windows doesn't keep the original locked
        info, im = f.info, f
        if f.format != "JPEG":      # a TIFF's rotation lives in tags a JPEG copy wouldn't carry
            im = ImageOps.exif_transpose(f)
        if im.mode not in ("RGB", "L"):
            im = im.convert("RGB")
        im.save(dst, "JPEG", quality=85, subsampling=2, optimize=True, progressive=True,
                exif=info.get("exif", b""), icc_profile=info.get("icc_profile"), xmp=info.get("xmp", b""))


@router.post("/share")
async def share_copies(db: DB, photo_ids: list[int] = Body(..., embed=True)) -> dict:
    """Smaller full-resolution copies of exactly these pictures (share_copy), for Google Drive and
    the like, in the export folder's Share/. A NEF or DNG is skipped; nothing is uploaded."""
    import asyncio
    from concurrent.futures import ThreadPoolExecutor
    from fernkam.workflows.export_web import IMAGES

    s = get_settings()
    lib = Path(s.library_root)
    out = _export_dir(s, lib) / "Share"
    out.mkdir(parents=True, exist_ok=True)
    rows = (await db.execute(text("SELECT album_path, filename FROM photos WHERE id = ANY(:ids) ORDER BY filename"),
                             {"ids": photo_ids})).all()
    jobs, skipped, taken = [], [], set()
    for r in rows:
        src = lib / r.album_path / r.filename
        if src.suffix.lower() not in IMAGES or not src.exists():
            skipped.append(f"{r.filename}: {'not on disk' if src.suffix.lower() in IMAGES else 'not a JPEG, TIFF or PNG'}")
            continue
        dst, n = out / f"{src.stem}.jpg", 2
        while dst.exists() or dst in taken:   # never overwrite an earlier copy
            dst, n = out / f"{src.stem} ({n}).jpg", n + 1
        taken.add(dst)
        jobs.append((src, dst))
    loop = asyncio.get_running_loop()
    with ThreadPoolExecutor(4) as pool:       # Pillow encodes outside the GIL
        await asyncio.gather(*(loop.run_in_executor(pool, share_copy, a, b) for a, b in jobs))
    before, after = sum(a.stat().st_size for a, _ in jobs), sum(b.stat().st_size for _, b in jobs)
    return {"folder": str(out), "saved": [b.name for _, b in jobs], "skipped": skipped, "before": before, "after": after}
