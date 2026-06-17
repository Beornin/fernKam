"""File-system workflow runner.

POST /api/workflows/run/sorting          — video sort workflow (background)
POST /api/workflows/run/remove-nonkeep-raw — remove NEF-without-JPG (background)
GET  /api/workflows/task/{task_id}       — poll output / status
"""
from __future__ import annotations

import asyncio
import io
import contextlib
import logging
from typing import Optional

from fastapi import APIRouter
from pydantic import BaseModel

logger = logging.getLogger(__name__)
router = APIRouter()


# ---------------------------------------------------------------------------
# Request models
# ---------------------------------------------------------------------------

class SortingRequest(BaseModel):
    raw_dir: str = r"D:\Pictures and Videos\AA_RAW"
    sort_me_dir: str = r"D:\Pictures and Videos\AB_TO_SORT\SORT ME"
    export_root: str = r"D:\Pictures and Videos\AC_SORTED"


class RemoveNonKeepRawRequest(BaseModel):
    starting_folder: str = r"D:\Pictures and Videos\AA_RAW"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _capture_run(fn, **kwargs) -> str:
    """Run *fn* with **kwargs, capturing all print() output, return as string."""
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
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
    from fernkam.task_manager import task_manager
    from fernkam.workflows import sorting_video

    task_id = await task_manager.create_task(
        "workflow_sorting",
        f"Sorting videos: {req.raw_dir}",
    )
    asyncio.create_task(
        _run_in_thread(task_id, sorting_video.run,
                       raw_dir=req.raw_dir,
                       sort_me_dir=req.sort_me_dir,
                       export_root=req.export_root),
        name=f"fernkam-workflow-{task_id}",
    )
    return {"task_id": task_id, "status": "started"}


@router.post("/run/remove-nonkeep-raw")
async def run_remove_nonkeep_raw(req: RemoveNonKeepRawRequest) -> dict:
    from fernkam.task_manager import task_manager
    from fernkam.workflows import remove_nonkeep_raw

    task_id = await task_manager.create_task(
        "workflow_remove_nonkeep_raw",
        f"Remove non-keep RAW: {req.starting_folder}",
    )
    asyncio.create_task(
        _run_in_thread(task_id, remove_nonkeep_raw.run,
                       starting_folder=req.starting_folder),
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
