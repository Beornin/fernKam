"""File-system workflow runner.

POST /api/workflows/run/sorting          — video sort workflow (background)
POST /api/workflows/run/remove-nonkeep-raw — remove NEF-without-JPG (background)
POST /api/workflows/run/develop-pureraw  — RAW -> JPG with DxO PureRAW (background)
GET  /api/workflows/task/{task_id}       — poll output / status
"""
from __future__ import annotations

import asyncio
import io
import contextlib
import logging
import sys
import threading
from typing import Optional

from fastapi import APIRouter, Body
from pydantic import BaseModel, ConfigDict
from sqlalchemy import text as _sql

from fernkam.api.deps import DB

logger = logging.getLogger(__name__)
router = APIRouter()


# ---------------------------------------------------------------------------
# Request models
# ---------------------------------------------------------------------------

class SortingRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    dry_run: bool = True
    raw_dir: Optional[str] = None       # default: <library>/<RAW_INTAKE_FOLDER>
    sort_me_dir: Optional[str] = None   # default: <library>/AB_TO_SORT/SORT ME
    export_root: Optional[str] = None   # default: <library>/<DEDUP_ARCHIVE_FOLDER> (Ordered by Dates)


class FinishShootRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    dry_run: bool = True
    shoot: str
    destination: str = "dates"          # dates | portfolio | client
    portfolio_folder: str = ""          # e.g. Portfolio/Frogs


class RemoveNonKeepRawRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    dry_run: bool = True
    starting_folder: str = r"D:\Pictures and Videos\AA_RAW"


class MoveRawsToFoldersRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    dry_run: bool = True
    starting_folder: Optional[str] = None


class DevelopPureRawRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    dry_run: bool = True
    folder: Optional[str] = None     # default: the whole intake folder (fresh shoots only)
    preview: bool = False            # open PureRAW's settings window first


class SyncStackTagsRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    dry_run: bool = True
    album_path: Optional[str] = None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

class _ThreadStdout:
    """sys.stdout replacement that sends a thread's writes to its own buffer.

    contextlib.redirect_stdout swaps the *process-wide* sys.stdout, so while a
    workflow ran in its worker thread every other print in the server (scan
    and face-scan progress, other requests) landed in that workflow's output,
    and two overlapping workflows restored each other's buffers — leaving
    sys.stdout pointing at a dead StringIO and the server silent from then on.
    Anything but write/flush (encoding, isatty, fileno, ...) is the real stream's.
    """

    def __init__(self, real):
        self._real = real
        self._local = threading.local()

    def _target(self):
        return getattr(self._local, "buf", None) or self._real

    def write(self, s: str) -> int:
        return self._target().write(s)

    def flush(self) -> None:
        self._target().flush()

    def __getattr__(self, name):
        return getattr(self._real, name)

    @contextlib.contextmanager
    def capture(self):
        buf = io.StringIO()
        self._local.buf = buf
        try:
            yield buf
        finally:
            self._local.buf = None


_stdout_router_lock = threading.Lock()


def _thread_stdout() -> _ThreadStdout:
    with _stdout_router_lock:
        if not isinstance(sys.stdout, _ThreadStdout):
            sys.stdout = _ThreadStdout(sys.stdout)
        return sys.stdout


def _capture_run(fn, **kwargs) -> str:
    """Run *fn* with **kwargs, capturing this thread's print() output."""
    with _thread_stdout().capture() as buf:
        try:
            fn(**kwargs)
        except Exception as exc:
            print(f"\nERROR: {exc}")
    return buf.getvalue()


async def _run_in_thread(task_id: str, fn, **kwargs) -> None:
    """Execute workflow in a thread-pool executor and update task when done."""
    from fernkam.task_manager import task_manager

    loop = asyncio.get_event_loop()
    try:
        output = await loop.run_in_executor(None, lambda: _capture_run(fn, **kwargs))
        lines = [l for l in output.splitlines() if l]
        failed = any("ERROR:" in l for l in lines)
        await task_manager.update_task(
            task_id,
            status="failed" if failed else "completed",
            message=lines[-1] if lines else "Done",
            progress={"lines": lines},
        )
    except Exception as exc:
        logger.exception("Workflow task %s failed", task_id)
        await task_manager.update_task(
            task_id,
            status="failed",
            message=str(exc),
            progress={"lines": [f"ERROR: {exc}"]},
        )


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@router.post("/run/sorting")
async def run_sorting(req: SortingRequest) -> dict:
    from pathlib import Path
    from fernkam.config import get_settings
    from fernkam.task_manager import task_manager
    from fernkam.workflows import sorting_video

    s = get_settings()
    lib = Path(s.library_root)
    export_root = req.export_root or str(lib / s.dedup_archive_folder)
    task_id = await task_manager.create_task(
        "workflow_sorting", f"{'Preview: ' if req.dry_run else ''}Sorting into {export_root}")
    _start(task_id, sorting_video.run, req.dry_run, scan=None,
           raw_dir=req.raw_dir or str(lib / s.raw_intake_folder),
           sort_me_dir=req.sort_me_dir or str(lib / "AB_TO_SORT" / "SORT ME"),
           export_root=export_root)
    return {"task_id": task_id, "status": "started"}


@router.post("/run/finish-shoot")
async def run_finish_shoot(req: FinishShootRequest) -> dict:
    from fernkam.task_manager import task_manager
    from fernkam.workflows import finish_shoot

    task_id = await task_manager.create_task(
        "workflow_finish_shoot", f"{'Preview: ' if req.dry_run else ''}Finish shoot: {req.shoot}")
    _start(task_id, finish_shoot.run, req.dry_run, scan=None, shoot=req.shoot,
           destination=req.destination, portfolio_folder=req.portfolio_folder)
    return {"task_id": task_id, "status": "started"}


@router.get("/shoot-suggestion")
async def shoot_suggestion(db: DB, shoot: str) -> dict:
    """Where a shoot's keepers probably belong, from the photos they look like.

    For up to 40 of the shoot's pictures, the 10 nearest photos already filed
    outside the intake (CLIP). Portfolio is suggested when most neighbours are
    there, with their most common Portfolio folder.
    """
    from collections import Counter
    from pathlib import Path
    from fernkam.config import get_settings

    s = get_settings()
    root, intake = Path(s.library_root), s.raw_intake_folder
    try:
        album = Path(shoot).resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        album = shoot.strip("/\\").replace("\\", "/")
    vecs = (await db.execute(_sql("""
        SELECT embedding_v::text AS v FROM photos
        WHERE album_path IN (:a, :j) AND embedding_v IS NOT NULL AND media_type = 'image'
        ORDER BY random() LIMIT 40"""), {"a": album, "j": f"{album}/jpg"})).all()
    await db.execute(_sql("SET LOCAL hnsw.ef_search = 200"))   # the shoot's own frames crowd the top
    where, folders = Counter(), Counter()
    for r in vecs:
        # Literal LIMIT: a bound one makes the planner skip the HNSW index.
        near = (await db.execute(_sql(
            "SELECT album_path FROM photos WHERE embedding_v IS NOT NULL "
            "ORDER BY embedding_v <=> CAST(:v AS vector) LIMIT 100"), {"v": r.v})).scalars().all()
        near = [a for a in near if a.split("/")[0] != intake][:10]
        for a in near:
            if a.split("/")[0] == s.portfolio_folder:
                where["portfolio"] += 1
                folders[a[:-4] if a.upper().endswith("/RAW") else a] += 1
            else:
                where["dates"] += 1
    total = sum(where.values())
    share = where["portfolio"] / total if total else 0.0
    return {
        "destination": "portfolio" if share >= 0.5 else "dates",
        "portfolio_folder": folders.most_common(1)[0][0] if folders else "",
        "portfolio_share": round(share, 2),
        "sampled": len(vecs),
        "client_folder": bool(s.client_folder),
    }


@router.post("/run/remove-nonkeep-raw")
async def run_remove_nonkeep_raw(req: RemoveNonKeepRawRequest) -> dict:
    from fernkam.task_manager import task_manager
    from fernkam.workflows import remove_nonkeep_raw

    task_id = await task_manager.create_task(
        "workflow_remove_nonkeep_raw",
        f"{'Preview: ' if req.dry_run else ''}Remove non-keep RAW: {req.starting_folder}",
    )
    asyncio.create_task(
        _run_in_thread(task_id, remove_nonkeep_raw.run,
                       starting_folder=req.starting_folder,
                       dry_run=req.dry_run),
        name=f"fernkam-workflow-{task_id}",
    )
    return {"task_id": task_id, "status": "started"}


@router.post("/run/move-raws-to-folders")
async def run_move_raws_to_folders(req: MoveRawsToFoldersRequest) -> dict:
    from fernkam.task_manager import task_manager
    from fernkam.workflows import move_raws_to_folders

    desc = f"{'Preview: ' if req.dry_run else ''}Move stray RAWs: {req.starting_folder or 'full library'}"
    task_id = await task_manager.create_task("workflow_move_raws", desc)
    asyncio.create_task(
        _run_in_thread(task_id, move_raws_to_folders.run,
                       starting_folder=req.starting_folder,
                       dry_run=req.dry_run),
        name=f"fernkam-workflow-{task_id}",
    )
    return {"task_id": task_id, "status": "started"}


def _start(task_id: str, fn, dry_run: bool, scan: Optional[str] = "", **kwargs) -> None:
    """Run a workflow in the background; after a real run, scan `scan` (None =
    the whole library) so the catalogue follows. The scan matches moved files
    to their rows by content, so tags and ratings follow them."""
    from fernkam.api.routers.sync.library import start_library_scan
    from fernkam.task_manager import TaskConflict

    async def go() -> None:
        await _run_in_thread(task_id, fn, dry_run=dry_run, **kwargs)
        if not dry_run and scan != "":
            try:
                await start_library_scan(custom_path=scan, label="Updating the catalogue…")
            except TaskConflict:
                pass  # the library watcher picks the changes up

    asyncio.create_task(go(), name=f"fernkam-workflow-{task_id}")


async def start_develop(folder: Optional[str] = None, dry_run: bool = True, preview: bool = False) -> str:
    """Start the PureRAW develop workflow as a task, then scan what it made.

    Used by the Workflows page and by the automatic run after a scan finds a
    fresh shoot in the intake folder. Raises TaskConflict like any file task.
    """
    from pathlib import Path
    from fernkam.config import get_settings
    from fernkam.task_manager import task_manager
    from fernkam.workflows import develop_pureraw

    s = get_settings()
    folder = folder or str(Path(s.library_root) / s.raw_intake_folder)
    task_id = await task_manager.create_task(
        "workflow_pureraw", f"{'Preview: ' if dry_run else ''}Develop with PureRAW: {folder}")
    loop = asyncio.get_running_loop()

    def progress(msg: str) -> None:
        asyncio.run_coroutine_threadsafe(task_manager.update_task(task_id, message=msg), loop)

    _start(task_id, develop_pureraw.run, dry_run, scan=folder, folder=folder, preview=preview, progress=progress)
    return task_id


@router.post("/run/develop-pureraw")
async def run_develop_pureraw(req: DevelopPureRawRequest) -> dict:
    return {"task_id": await start_develop(req.folder, req.dry_run, req.preview), "status": "started"}


@router.get("/pureraw-auto")
async def get_pureraw_auto(db: DB) -> dict:
    """Develop fresh intake shoots with PureRAW automatically after a scan."""
    from pathlib import Path
    from fernkam.config import get_settings
    from fernkam.db.app_settings import get_setting
    return {"enabled": (await get_setting(db, "pureraw_auto", "1")) == "1",
            "installed": Path(get_settings().pureraw_exe).is_file()}


@router.post("/pureraw-auto")
async def set_pureraw_auto(db: DB, enabled: bool = Body(..., embed=True)) -> dict:
    from fernkam.db.app_settings import set_setting
    await set_setting(db, "pureraw_auto", "1" if enabled else "0")
    return await get_pureraw_auto(db)


@router.post("/run/sync-stack-tags")
async def run_sync_stack_tags(req: SyncStackTagsRequest) -> dict:
    from fernkam.task_manager import task_manager
    from fernkam.workflows import sync_stack_tags

    desc = f"{'Preview: ' if req.dry_run else ''}Sync stack tags: {req.album_path or 'full library'}"
    task_id = await task_manager.create_task("workflow_sync_stack_tags", desc)
    asyncio.create_task(
        _run_in_thread(task_id, sync_stack_tags.run,
                       album_path=req.album_path,
                       dry_run=req.dry_run),
        name=f"fernkam-workflow-{task_id}",
    )
    return {"task_id": task_id, "status": "started"}


@router.get("/task/{task_id}")
async def get_workflow_task(task_id: str) -> dict:
    from fernkam.task_manager import task_manager

    task = await task_manager.get_task(task_id)
    if task is None:
        from fastapi import HTTPException
        raise HTTPException(status_code=404, detail="Task not found")
    return {
        "task_id": task.id,
        "status": task.status,
        "message": task.message,
        "lines": (task.progress or {}).get("lines", []),
    }


# ---------------------------------------------------------------------------
# Pipeline view (Roadmap 3.1) and RAW/JPEG health (3.4) — both read-only.
# ---------------------------------------------------------------------------

@router.get("/pipeline")
async def pipeline_stages(db: DB) -> dict:
    """Live counts for each stage of the sorting pipeline.

    Counts come from the catalogue, not a disk walk, so this stays instant on a
    116k-photo library. `on_disk` is reported separately for the staging folders
    only — those are small, and a drift between disk and catalogue there is the
    thing actually worth noticing.
    """
    from pathlib import Path
    from fernkam.config import get_settings

    s = get_settings()
    staging = [f.strip() for f in s.dedup_staging_folders.split(",") if f.strip()]
    stages = (
        [(s.raw_intake_folder, "intake", "RAW files straight off the card")]
        + [(f, "staging", "Waiting to be sorted") for f in staging]
        + [(s.dedup_archive_folder, "archive", "Sorted by date"),
           (s.portfolio_folder, "portfolio", "Curated keepers")]
    )

    rows = (await db.execute(_sql("""
        SELECT split_part(trim(both '/' from album_path), '/', 1) AS top,
               COUNT(*) AS n,
               COUNT(*) FILTER (WHERE media_type = 'video') AS videos,
               COUNT(*) FILTER (WHERE rating > 0) AS rated,
               COALESCE(SUM(file_size), 0) AS bytes
        FROM photos WHERE status = 1 GROUP BY 1
    """))).fetchall()
    by_top = {r[0]: r for r in rows}

    library_root = Path(s.library_root)
    out = []
    for folder, kind, blurb in stages:
        r = by_top.get(folder)
        entry = {
            "folder": folder, "kind": kind, "blurb": blurb,
            "catalogued": int(r[1]) if r else 0,
            "videos": int(r[2]) if r else 0,
            "rated": int(r[3]) if r else 0,
            "bytes": int(r[4]) if r else 0,
            "on_disk": None,
        }
        if kind in ("intake", "staging"):
            d = library_root / folder
            try:
                entry["on_disk"] = sum(1 for p in d.rglob("*") if p.is_file()) if d.exists() else 0
            except OSError:
                entry["on_disk"] = None
        out.append(entry)
    return {"stages": out}


@router.get("/raw-health")
async def raw_health(db: DB, album_path: Optional[str] = None) -> dict:
    """RAW/JPEG pairing problems, catalogue-side.

    Three things worth knowing before a cull, all of which were previously only
    discoverable by hand:
      * orphan RAWs   — a RAW whose derivative was deleted (the cull already happened)
      * unstacked RAW — a RAW/JPEG pair that exists but was never stacked
      * lone JPEGs    — a picture in a RAW folder with no RAW beside it
    """
    where = "AND p.album_path LIKE :ap" if album_path else ""
    params = {"ap": f"{album_path}%"} if album_path else {}

    rows = (await db.execute(_sql(f"""
        WITH f AS (
            SELECT p.id, p.album_path, p.filename, p.stack_id,
                   lower(regexp_replace(p.filename, '\.[^.]+$', '')) AS stem,
                   lower(regexp_replace(p.filename, '^.*\.', '')) AS ext,
                   regexp_replace(trim(both '/' from p.album_path), '/RAW$', '', 'i') AS base_album
            FROM photos p WHERE p.status = 1 {where}
        ),
        raws AS (SELECT * FROM f WHERE ext IN ('nef','cr2','cr3','arw','orf','raf','rw2','pef','srw','dng')),
        pics AS (SELECT * FROM f WHERE ext IN ('jpg','jpeg','tif','tiff','png','heic','webp'))
        SELECT
          (SELECT COUNT(*) FROM raws) AS raw_total,
          (SELECT COUNT(*) FROM raws r WHERE NOT EXISTS (
              SELECT 1 FROM pics q WHERE q.base_album = r.base_album AND q.stem = r.stem)) AS orphan_raw,
          (SELECT COUNT(*) FROM raws r WHERE r.stack_id IS NULL AND EXISTS (
              SELECT 1 FROM pics q WHERE q.base_album = r.base_album AND q.stem = r.stem)) AS unstacked_pairs,
          (SELECT COUNT(*) FROM pics q WHERE q.album_path ILIKE '%%/RAW' AND NOT EXISTS (
              SELECT 1 FROM raws r WHERE r.base_album = q.base_album AND r.stem = q.stem)) AS lone_pics
    """), params)).first()

    samples = (await db.execute(_sql(f"""
        WITH f AS (
            SELECT p.id, p.album_path, p.filename,
                   lower(regexp_replace(p.filename, '\.[^.]+$', '')) AS stem,
                   lower(regexp_replace(p.filename, '^.*\.', '')) AS ext,
                   regexp_replace(trim(both '/' from p.album_path), '/RAW$', '', 'i') AS base_album
            FROM photos p WHERE p.status = 1 {where}
        ),
        raws AS (SELECT * FROM f WHERE ext IN ('nef','cr2','cr3','arw','orf','raf','rw2','pef','srw','dng')),
        pics AS (SELECT * FROM f WHERE ext IN ('jpg','jpeg','tif','tiff','png','heic','webp'))
        SELECT r.id, r.album_path, r.filename FROM raws r
        WHERE NOT EXISTS (SELECT 1 FROM pics q WHERE q.base_album = r.base_album AND q.stem = r.stem)
        ORDER BY r.album_path, r.filename LIMIT 25
    """), params)).fetchall()

    return {
        "album_path": album_path,
        "raw_total": int(row_or(rows, 0)),
        "orphan_raw": int(row_or(rows, 1)),
        "unstacked_pairs": int(row_or(rows, 2)),
        "lone_pics": int(row_or(rows, 3)),
        "orphan_samples": [
            {"id": s[0], "album_path": s[1], "filename": s[2]} for s in samples
        ],
    }


def row_or(row, idx: int, default: int = 0) -> int:
    return default if row is None or row[idx] is None else row[idx]



def promote_destination(src_album: str, dest_album: str, filename: str) -> tuple[str, str]:
    """Where one file lands when promoted. Returns (album_path, relative_path).

    RAWs live in <album>/RAW/ by convention (see move_raws_to_folders), so a
    file coming from a RAW/ subfolder must land in one — flattening it into the
    destination root would break the pairing the stack depends on.
    """
    in_raw = src_album.rstrip("/").upper().endswith("/RAW")
    album = f"{dest_album}/RAW" if in_raw else dest_album
    return album, f"{album}/{filename}"


class PromoteRequest(BaseModel):
    """Roadmap 3.5 — rate, tag, move and re-stack in one action."""
    photo_ids: list[int]
    subfolder: Optional[str] = None      # under the portfolio root, e.g. "Snakes"
    rating: Optional[int] = None         # 1-5, or None to leave alone
    tag_ids: list[int] = []
    dry_run: bool = True


@router.post("/promote-to-portfolio")
async def promote_to_portfolio(req: PromoteRequest, db: DB) -> dict:
    """Move keepers into the portfolio, carrying their whole stack.

    Moving a JPEG without its RAW would silently break the pair, so every
    member of a selected photo's stack moves with it. `photo_stacks.album_path`
    is part of a unique key with stem_key, so it is rewritten too — otherwise
    the next stack rebuild would create a duplicate stack at the old location.

    dry_run defaults to True: this relocates originals on disk.
    """
    from pathlib import Path
    from fernkam.config import get_settings

    if not req.photo_ids:
        return {"moved": 0, "plan": [], "dry_run": req.dry_run}
    if req.rating is not None and not (0 <= req.rating <= 5):
        from fastapi import HTTPException
        raise HTTPException(400, "rating must be 0-5")

    s = get_settings()
    library_root = Path(s.library_root)
    dest_album = s.portfolio_folder + (f"/{req.subfolder.strip('/')}" if req.subfolder else "")
    dest_dir = library_root / dest_album
    portfolio_dir = (library_root / s.portfolio_folder).resolve()
    if not dest_dir.resolve().is_relative_to(portfolio_dir):
        from fastapi import HTTPException
        raise HTTPException(400, "subfolder must stay inside the portfolio folder")

    # Expand the selection to whole stacks.
    rows = (await db.execute(_sql("""
        SELECT p.id, p.album_path, p.filename, p.stack_id
        FROM photos p
        WHERE p.status = 1 AND (
            p.id = ANY(CAST(:ids AS int[]))
            OR p.stack_id IN (SELECT stack_id FROM photos
                              WHERE id = ANY(CAST(:ids AS int[])) AND stack_id IS NOT NULL)
        )
        ORDER BY p.album_path, p.filename
    """), {"ids": req.photo_ids})).fetchall()

    plan, conflicts = [], []
    for pid, album, fname, stack_id in rows:
        src = library_root / album.strip("/") / fname if album.strip("/") else library_root / fname
        # RAWs live in <album>/RAW/ by convention (see move_raws_to_folders).
        # Flattening them into the destination root would break that pairing,
        # so a file that came from a RAW/ subfolder lands in one.
        item_album, rel = promote_destination(album, dest_album, fname)
        dst = library_root / rel
        item = {"photo_id": pid, "from": str(src), "to": str(dst),
                "album": item_album,
                "stacked": stack_id is not None,
                "carried": pid not in req.photo_ids}
        if album.strip("/") == item_album.strip("/"):
            item["skip"] = "already in destination"
        elif dst.exists():
            item["skip"] = "destination file exists"
            conflicts.append(item)
        plan.append(item)

    movable = [p for p in plan if "skip" not in p]
    if req.dry_run:
        return {"dry_run": True, "dest_album": dest_album, "would_move": len(movable),
                "conflicts": len(conflicts), "carried_with_stack": sum(1 for p in movable if p["carried"]),
                "plan": plan[:200]}

    # Moving files while a scan walks the library is the race the task guard
    # exists for: the scan can see the old path empty and delete the row.
    from fernkam.task_manager import task_manager
    task_manager.ensure_no_file_task()

    import shutil
    moved, errors = 0, []
    for item in movable:
        try:
            Path(item["to"]).parent.mkdir(parents=True, exist_ok=True)
            shutil.move(item["from"], item["to"])
        except OSError as exc:
            errors.append({"photo_id": item["photo_id"], "error": str(exc)})
            continue
        await db.execute(_sql("UPDATE photos SET album_path = :a, file_sync_dirty = true WHERE id = :pid"),
                         {"a": item["album"], "pid": item["photo_id"]})
        moved += 1

    if moved:
        # Keep stacks pointing at where their members now live.
        await db.execute(_sql("""
            UPDATE photo_stacks SET album_path = :a, updated_at = now()
            WHERE id IN (SELECT DISTINCT stack_id FROM photos
                         WHERE id = ANY(CAST(:ids AS int[])) AND stack_id IS NOT NULL)
        """), {"a": dest_album, "ids": [i["photo_id"] for i in movable]})
        if req.rating is not None:
            await db.execute(_sql("UPDATE photos SET rating = :r WHERE id = ANY(CAST(:ids AS int[]))"),
                             {"r": req.rating, "ids": [i["photo_id"] for i in movable]})
        if req.tag_ids:
            # Tags picked in the promote dialog are the user's own: approved.
            await db.execute(_sql("""
                INSERT INTO photo_tags (photo_id, tag_id, verified_at)
                SELECT p, t, now() FROM unnest(CAST(:pids AS int[])) p
                CROSS JOIN unnest(CAST(:tids AS int[])) t
                ON CONFLICT (photo_id, tag_id)
                    DO UPDATE SET verified_at = COALESCE(photo_tags.verified_at, now())
            """), {"pids": [i["photo_id"] for i in movable], "tids": req.tag_ids})
        await db.commit()

    return {"dry_run": False, "dest_album": dest_album, "moved": moved,
            "skipped": len(plan) - len(movable), "errors": errors}
