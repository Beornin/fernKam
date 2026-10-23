"""File-system workflow runner.

POST /api/workflows/run/remove-nonkeep-raw — remove NEF-without-JPG (background)
POST /api/workflows/run/sorting          — AA_RAW + SORT ME -> AC_SORTED/YYYY/MM (background)
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

from fastapi import APIRouter
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
    export_root: Optional[str] = None   # default: <library>/AC_SORTED


class RemoveNonKeepRawRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    dry_run: bool = True
    starting_folder: str = r"D:\Pictures and Videos\AA_RAW"


class MoveRawsToFoldersRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    dry_run: bool = True
    starting_folder: Optional[str] = None


class SyncStackTagsRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    dry_run: bool = True
    album_path: Optional[str] = None


class ExportWebRequest(BaseModel):
    """Workflows card fields arrive as text; blank = the setting's default."""
    model_config = ConfigDict(extra='forbid')
    dry_run: bool = True
    path: str
    out_dir: Optional[str] = None
    max_edge: Optional[str] = None
    remove_gps: Optional[str] = None     # anything non-blank = strip GPS


class PortfolioKeywordsRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    dry_run: bool = True
    paths: str = ""                      # library folders, separated by ";"; blank = PORTFOLIO_FOLDERS


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
    export_root = req.export_root or str(lib / "AC_SORTED")
    task_id = await task_manager.create_task(
        "workflow_sorting", f"{'Preview: ' if req.dry_run else ''}Sorting into {export_root}")
    _start(task_id, sorting_video.run, req.dry_run, scan=None,
           raw_dir=req.raw_dir or str(lib / s.raw_intake_folder),
           sort_me_dir=req.sort_me_dir or str(lib / "AB_TO_SORT" / "SORT ME"),
           export_root=export_root)
    return {"task_id": task_id, "status": "started"}


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


@router.post("/run/export-web")
async def run_export_web(req: ExportWebRequest) -> dict:
    """Web-sized copies with research keywords and rights (workflows/export_web.py). Originals untouched."""
    from pathlib import Path
    from fastapi import HTTPException
    from fernkam.task_manager import task_manager
    from fernkam.workflows import export_web

    src = Path(req.path.strip().strip('"'))
    if not src.exists():
        raise HTTPException(400, f"Not found: {src}")
    edge = (req.max_edge or "").strip()
    if edge and not edge.isdigit():
        raise HTTPException(400, "Longest side must be a number of pixels")
    task_id = await task_manager.create_task(
        "workflow_export_web", f"{'Preview: ' if req.dry_run else ''}Export for web: {src}")
    asyncio.create_task(
        _run_in_thread(task_id, export_web.run, path=str(src), out_dir=(req.out_dir or "").strip() or None,
                       max_edge=int(edge) if edge else None, remove_gps=bool((req.remove_gps or "").strip()),
                       dry_run=req.dry_run),
        name=f"fernkam-workflow-{task_id}",
    )
    return {"task_id": task_id, "status": "started"}


@router.post("/run/portfolio-keywords")
async def run_portfolio_keywords(req: PortfolioKeywordsRequest) -> dict:
    """Species folders, GBIF / status / camera tags and research words in step (workflows/portfolio_keywords.py)."""
    from fernkam.task_manager import task_manager
    from fernkam.workflows import portfolio_keywords

    from fastapi import HTTPException
    from fernkam.config import get_settings
    paths = [p.strip().strip('"') for p in (req.paths.strip() or get_settings().portfolio_folders).split(";") if p.strip()]
    if not paths:
        raise HTTPException(400, "Pick one or more folders, or set PORTFOLIO_FOLDERS for the usual ones")
    task_id = await task_manager.create_task(
        "workflow_portfolio_keywords", f"{'Preview: ' if req.dry_run else ''}Portfolio keywords and tags: {'; '.join(paths)}")
    asyncio.create_task(
        _run_in_thread(task_id, portfolio_keywords.run, paths=paths, dry_run=req.dry_run),
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
