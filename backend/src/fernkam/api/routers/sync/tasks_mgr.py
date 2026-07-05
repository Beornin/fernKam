"""Background task management endpoints."""
from __future__ import annotations

from fastapi import APIRouter, Query

router = APIRouter()


@router.get("/tasks")
async def get_tasks(running_only: bool = Query(False)) -> dict:
    """Get background tasks. Pass running_only=1 for the lightweight status-bar poll."""
    from fernkam.task_manager import task_manager
    tasks = (
        await task_manager.get_running_tasks()
        if running_only
        else await task_manager.get_all_tasks()
    )
    return {
        "tasks": [
            {
                "id": t.id,
                "task_type": t.task_type,
                "status": t.status,
                "message": t.message,
                "started_at": t.started_at.isoformat(),
                "completed_at": t.completed_at.isoformat() if t.completed_at else None,
                "progress": t.progress
            }
            for t in tasks
        ]
    }


@router.post("/tasks/{task_id}/cancel")
async def cancel_task(task_id: str) -> dict:
    """Cancel a running task."""
    from fernkam.task_manager import task_manager
    cancelled = await task_manager.cancel_task(task_id)
    return {"cancelled": cancelled}
