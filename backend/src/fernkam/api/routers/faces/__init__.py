"""faces router package — aggregates suggestions, clusters, and crud sub-routers.

Public surface: ``router`` (FastAPI APIRouter), matching the old single-file layout.
The helper ``_auto_confirm_sweep`` is re-exported so sync.py's inline imports keep working.
"""
from fastapi import APIRouter

from .suggestions import router as _suggestions_router
from .clusters import router as _clusters_router
from .crud import router as _crud_router
from ._helpers import _auto_confirm_sweep  # re-export for sync.py compatibility

router = APIRouter()
router.include_router(_suggestions_router)
router.include_router(_clusters_router)
router.include_router(_crud_router)

__all__ = ["router", "_auto_confirm_sweep"]
