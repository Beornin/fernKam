"""sync router package — aggregates metadata, library, admin, and tasks sub-routers."""
from fastapi import APIRouter

from .metadata import router as _metadata_router
from .library import router as _library_router
from .admin import router as _admin_router
from .tasks_mgr import router as _tasks_router

router = APIRouter()
router.include_router(_metadata_router)
router.include_router(_library_router)
router.include_router(_admin_router)
router.include_router(_tasks_router)

__all__ = ["router"]
