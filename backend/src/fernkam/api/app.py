from __future__ import annotations

import logging
import os
import signal
import sys
from contextlib import asynccontextmanager
from pathlib import Path

# Windows consoles default to cp1252, which cannot encode the U+2713/U+2717
# marks used in the startup banner below. Two of those prints sit in `except`
# blocks, so a genuine failure would raise UnicodeEncodeError *while reporting
# itself* and take the worker down with the real cause hidden. errors="replace"
# means these streams can never raise on encoding again.
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError, OSError):
        pass  # already wrapped, detached, or not a real stream

# Add CUDA paths to PATH so onnxruntime-gpu can find CUDA/cuDNN DLLs.
# DaVinci Resolve includes cuDNN 9, which works with onnxruntime-gpu.
_CUDA_PATHS = [
    r"C:\Program Files\NVIDIA GPU Computing Toolkit\CUDA\v12.6\bin",
    r"C:\Program Files\Blackmagic Design\DaVinci Resolve",
]
for _p in _CUDA_PATHS:
    if os.path.isdir(_p) and _p not in os.environ.get("PATH", ""):
        os.environ["PATH"] = _p + os.pathsep + os.environ["PATH"]

# Install native stderr capture BEFORE importing cv2/onnxruntime/insightface so
# their cached FD-2 reference is the piped one. Keeps OpenCV/ffmpeg warnings out
# of nowhere and into the app_logs table.
from fernkam import stderr_capture as _stderr_capture
_stderr_capture.install()

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import text

from fernkam.api.routers import albums, backup, debug, dedup, faces, geocode, logs as logs_router, media, outside_changes, people, photos, saved_searches, semantic, stacks, sync, tags, workflows
from fernkam.db.session import get_async_engine
from fernkam.task_manager import TaskConflict

# Configure logging to output to terminal
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[logging.StreamHandler(sys.stdout)]
)


def _run_migrations() -> None:
    """Run alembic upgrade head synchronously.

    Uses a throw-away psycopg2 connection (same as alembic normally does).
    Safe to call every startup — alembic is idempotent when already at head.

    Bootstrap handling: if alembic_version doesn't exist but orphan tables do
    (created by the old ensure_* helpers before migrations took over), those
    tables are dropped first so alembic can own them cleanly.
    """
    from alembic.config import Config as _AlembicConfig
    from alembic import command as _alembic_cmd
    from fernkam.config import BACKEND_DIR, get_settings

    cfg = _AlembicConfig(str(BACKEND_DIR / "alembic.ini"))
    # alembic/env.py sets the URL from the same settings; this copy is for the
    # bootstrap check below.
    sync_url = get_settings().pg_url_sync.replace("+asyncpg", "+psycopg2")

    # ── Bootstrap guard ───────────────────────────────────────────────────────
    # If alembic_version doesn't exist this is either a brand-new DB or one
    # that was seeded by the old ensure_* helpers (only `tasks` present).
    # Tables that alembic migrations own must be dropped so alembic can
    # create them with the correct schema + indexes.
    ALEMBIC_OWNED_TABLES = ["tasks"]
    try:
        from sqlalchemy import create_engine as _create_engine, text as _sa_text
        _boot_engine = _create_engine(sync_url)
        with _boot_engine.connect() as _conn:
            _conn = _conn.execution_options(isolation_level="AUTOCOMMIT")
            _has_ver = _conn.execute(_sa_text(
                "SELECT 1 FROM pg_tables WHERE schemaname='public' AND tablename='alembic_version'"
            )).fetchone() is not None
            if not _has_ver:
                for _tbl in ALEMBIC_OWNED_TABLES:
                    _exists = _conn.execute(_sa_text(
                        "SELECT 1 FROM pg_tables WHERE schemaname='public' AND tablename=:t"
                    ), {"t": _tbl}).fetchone() is not None
                    if _exists:
                        print(f"[migrations] Dropping orphan table '{_tbl}' (will be recreated by alembic)", flush=True)
                        _conn.execute(_sa_text(f"DROP TABLE IF EXISTS {_tbl} CASCADE"))
        _boot_engine.dispose()
    except Exception as _be:
        print(f"[migrations] Bootstrap check warning: {_be}", flush=True)

    _alembic_cmd.upgrade(cfg, "head")


@asynccontextmanager
async def lifespan(app: FastAPI):
    # ── Auto-migrate: run alembic upgrade head (idempotent, safe every start) ─
    try:
        import asyncio as _asyncio
        loop = _asyncio.get_event_loop()
        await loop.run_in_executor(None, _run_migrations)
        print("✓ Database migrations up to date", flush=True)
    except Exception as _e:
        print(f"✗ Migration failed: {_e}", flush=True)
        print("  Run `alembic upgrade head` manually if this persists", flush=True)

    # Check database connectivity on startup
    print("Checking database connectivity...")
    try:
        engine = get_async_engine()
        async with engine.begin() as conn:
            await conn.execute(text("SELECT 1"))
            print("✓ Database connection successful")
    except Exception as e:
        print(f"✗ Database connection failed: {e}")
        print("  Check your PostgreSQL configuration in .env file")
    # Ensure pgvector extension/column/index/backfill are in place
    try:
        from fernkam.db.pgvector_setup import ensure_pgvector
        await ensure_pgvector(get_async_engine())
    except Exception as _e:
        print(f"[pgvector] setup failed: {_e}", flush=True)

    # Ensure app_logs table exists, then start the async sink + retention loop.
    try:
        from fernkam.db.app_logs_setup import ensure_app_logs
        await ensure_app_logs(get_async_engine())
        from fernkam import logs_sink as _ls
        await _ls.start_sink()
        _ls.install_python_handler(
            min_level=getattr(logging, os.getenv("FERNKAM_LOG_DB_LEVEL", "WARNING").upper(), logging.WARNING)
        )
        import asyncio as __asyncio
        __asyncio.create_task(_ls.retention_loop(), name="fernkam-logs-retention")
    except Exception as _e:
        print(f"[app_logs] setup failed: {_e}", flush=True)

    # Ensure tasks table exists
    try:
        from fernkam.db.tasks_setup import ensure_tasks
        await ensure_tasks(get_async_engine())
    except Exception as _e:
        print(f"[tasks] setup failed: {_e}", flush=True)

    # Ensure person_centroids table + det_score column exist; drop TWINS-blocking index
    try:
        from fernkam.db.person_centroids_setup import (
            ensure_person_centroids,
            ensure_det_score_column,
            drop_single_confirmed_per_photo_constraint,
        )
        await ensure_person_centroids(get_async_engine())
        await ensure_det_score_column(get_async_engine())
        await drop_single_confirmed_per_photo_constraint(get_async_engine())
    except Exception as _e:
        print(f"[person_centroids] setup failed: {_e}", flush=True)

    # Ensure face_clusters table exists (cluster-review feature)
    try:
        from fernkam.db.face_clusters_setup import ensure_face_clusters
        await ensure_face_clusters(get_async_engine())
    except Exception as _e:
        print(f"[face_clusters] setup failed: {_e}", flush=True)

    # Ensure optimal indexes exist and dead ones are dropped
    try:
        from fernkam.db.index_setup import ensure_database_tuning, ensure_indexes
        await ensure_indexes(get_async_engine())
        await ensure_database_tuning(get_async_engine())
        print("[index_setup] index maintenance complete", flush=True)
    except Exception as _e:
        print(f"[index_setup] failed: {_e}", flush=True)

    # Cancel tasks that were left running by a previous process (would spin forever)
    try:
        from fernkam.task_manager import task_manager as _tm
        stale = await _tm.cancel_stale_tasks()
        if stale:
            print(f"[task_manager] Cancelled {stale} stale running task(s) from previous session", flush=True)
    except Exception as _e:
        print(f"[task_manager] stale-cancel failed: {_e}", flush=True)

    # Refresh from disk first: files edited outside fernKam while it was
    # closed are picked up before anything else happens (background task).
    try:
        from fernkam.config import get_settings as _gs
        if _gs().scan_on_startup:
            from fernkam.api.routers.sync.library import start_library_scan
            await start_library_scan(label="Refreshing from disk (startup)…")
            print("[startup] Refreshing the catalogue from disk in the background", flush=True)
    except TaskConflict as _tc:
        print(f"[startup] refresh skipped: {_tc}", flush=True)
    except Exception as _e:
        print(f"[startup] refresh failed to start: {_e}", flush=True)

    # Then keep watching for changes made while fernKam runs.
    try:
        from fernkam.config import get_settings as _gs2
        if _gs2().watch_library:
            from fernkam import library_watch
            import asyncio as _aio
            _aio.create_task(library_watch.watch_library(), name="fernkam-library-watch")
    except Exception as _e:
        print(f"[watch] failed to start: {_e}", flush=True)

    # Pre-warm InsightFace model in background (avoids long hang on first face scan)
    import asyncio as _asyncio
    async def _warm_face_model():
        loop = _asyncio.get_event_loop()
        try:
            print("[face] Pre-warming InsightFace model in background...", flush=True)
            from fernkam.face_processor import _get_app
            await loop.run_in_executor(None, _get_app)
            print("[face] Pre-warm complete", flush=True)
        except Exception as e:
            print(f"[face] Model pre-warm failed: {e}", flush=True)
    # Schedule task but don't wait for it
    try:
        _asyncio.create_task(_warm_face_model(), name="fernkam-warmup-face")
    except Exception as e:
        print(f"[face] Failed to schedule pre-warm: {e}", flush=True)

    yield
    # Cleanup on shutdown - only cancel our named background tasks
    print("Shutting down...")
    try:
        from fernkam import library_watch
        library_watch.stop()  # the watcher's thread checks this every step
    except Exception:
        pass
    import asyncio
    try:
        tasks = [
            t for t in asyncio.all_tasks()
            if t is not asyncio.current_task()
            and t.get_name().startswith("fernkam-")
        ]
        for task in tasks:
            task.cancel()
        print(f"✓ Cancelled {len(tasks)} fernkam background tasks")
    except Exception as e:
        print(f"✗ Error cancelling tasks: {e}")


app = FastAPI(
    title="fernKam API",
    version="0.2.0",
    description="Self-hosted photo & video organizer API",
    lifespan=lifespan,
)

# ── Cross-site protection ──────────────────────────────────────────────────
# There is no login: anything that can send this server a request can trash
# originals, move folders or truncate the catalogue. The UI is served by this
# server (or proxied by the Vite dev server), so it is always same-origin and
# needs no CORS. The old `allow_origins=["*"]` let any web page open in the
# user's browser read the library's API — and a cross-site POST needs no CORS
# at all, so a page could also fire /api/sync/reset-db blind.
_SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})


def _extra_origins() -> set[str]:
    from fernkam.config import get_settings
    return {o.strip().rstrip("/") for o in get_settings().cors_origins.split(",") if o.strip()}


if _extra_origins():
    app.add_middleware(
        CORSMiddleware,
        allow_origins=sorted(_extra_origins()),
        allow_methods=["*"],
        allow_headers=["*"],
    )


def _ip(hostname: str):
    import ipaddress
    try:
        return ipaddress.ip_address(hostname.strip("[]"))
    except ValueError:
        return None


def _is_loopback(hostname: str) -> bool:
    h = hostname.lower()
    ip = _ip(h)
    return h == "localhost" or h.endswith(".localhost") or bool(ip and ip.is_loopback)


def _is_local_host(hostname: str) -> bool:
    """A name a DNS-rebinding page cannot hide behind: an IP literal, localhost,
    or this machine's own hostname."""
    import socket
    h = hostname.lower()
    me = socket.gethostname().lower()
    return _ip(h) is not None or _is_loopback(h) or h == me or h.startswith(me + ".")


def _origin_allowed(origin: str, host_header: str) -> bool:
    from urllib.parse import urlsplit
    if origin.rstrip("/") in _extra_origins():
        return True
    parts = urlsplit(origin)
    if not parts.hostname:
        return False  # "null": sandboxed iframes, file:// pages
    # Any page served from this machine's loopback — including the Vite dev
    # server on :5173, whose proxy rewrites Host to :8000.
    if _is_loopback(parts.hostname):
        return True
    # Same origin as the address we were reached on (LAN access with
    # --host 0.0.0.0), unless that address is a foreign domain rebound to us.
    return parts.netloc == host_header and _is_local_host(parts.hostname)


@app.middleware("http")
async def reject_cross_site_writes(request, call_next):
    """403 any state-changing request a browser sent on behalf of another site.

    Browsers always attach Origin to cross-origin POST/PUT/PATCH/DELETE, so a
    missing Origin means a non-browser client (curl, scripts) and is allowed.
    """
    if request.method not in _SAFE_METHODS:
        origin = request.headers.get("origin")
        if origin is not None and not _origin_allowed(origin, request.headers.get("host", "")):
            from fastapi.responses import JSONResponse
            logging.getLogger("fernkam.request").warning(
                "Blocked cross-site %s %s from Origin %s", request.method, request.url.path, origin)
            return JSONResponse(status_code=403, content={
                "detail": f"Cross-site request from {origin} blocked. Add it to CORS_ORIGINS if it is yours."})
    return await call_next(request)


@app.middleware("http")
async def log_requests(request, call_next):
    """Log all requests and errors to terminal."""
    import time

    logger = logging.getLogger("fernkam.request")
    start_time = time.time()
    logger.info(f"{request.method} {request.url.path}")
    
    try:
        response = await call_next(request)
        duration = time.time() - start_time
        logger.info(f"{request.method} {request.url.path} - {response.status_code} ({duration:.2f}s)")
        return response
    except Exception as e:
        duration = time.time() - start_time
        logger.error(f"{request.method} {request.url.path} - ERROR: {e} ({duration:.2f}s)", exc_info=True)
        raise

app.include_router(albums.router, prefix="/api/albums", tags=["albums"])
app.include_router(photos.router, prefix="/api/photos", tags=["photos"])
app.include_router(tags.router, prefix="/api/tags", tags=["tags"])
app.include_router(faces.router, prefix="/api/faces", tags=["faces"])
app.include_router(people.router, prefix="/api/people", tags=["people"])
app.include_router(sync.router, prefix="/api/sync", tags=["sync"])
app.include_router(backup.router, prefix="/api/backup", tags=["backup"])
app.include_router(logs_router.router, prefix="/api/logs", tags=["logs"])
app.include_router(media.router, prefix="/media", tags=["media"])
app.include_router(dedup.router, prefix="/api/dedup", tags=["dedup"])
app.include_router(geocode.router, prefix="/api/geocode", tags=["geocode"])
app.include_router(saved_searches.router, prefix="/api/saved-searches", tags=["saved-searches"])
app.include_router(stacks.router, prefix="/api/stacks", tags=["stacks"])
app.include_router(workflows.router, prefix="/api/workflows", tags=["workflows"])
app.include_router(semantic.router, prefix="/api/semantic", tags=["semantic"])
app.include_router(debug.router, prefix="/api/debug", tags=["debug"])
app.include_router(outside_changes.router, prefix="/api/outside-changes", tags=["outside-changes"])


@app.exception_handler(TaskConflict)
async def task_conflict_handler(request, exc: TaskConflict):
    """409, not 500: starting a second file-mutating job is a normal thing to
    try, and the caller just needs to be told what is already running."""
    from fastapi.responses import JSONResponse
    return JSONResponse(
        status_code=409,
        content={
            "detail": str(exc),
            "running_task": {"id": exc.running.id, "task_type": exc.running.task_type,
                             "message": exc.running.message},
        },
    )


@app.exception_handler(Exception)
async def global_exception_handler(request, exc):
    """Log all unhandled exceptions."""
    logger = logging.getLogger("fernkam.error")
    logger.error(f"Unhandled exception: {exc}", exc_info=True)
    from fastapi.responses import JSONResponse
    return JSONResponse(
        status_code=500,
        content={"detail": str(exc)}
    )


@app.get("/api/health")
async def health() -> dict:
    return {"status": "ok", "version": "0.2.0"}


@app.post("/api/shutdown")
async def shutdown() -> dict:
    """Shutdown the server."""
    # Use signal to shutdown gracefully
    import threading
    
    def force_shutdown():
        import time
        time.sleep(0.1)
        # Try multiple methods
        try:
            os.kill(os.getpid(), signal.SIGINT)
        except Exception:
            pass
        try:
            time.sleep(0.5)
            os._exit(0)
        except Exception:
            pass
    
    thread = threading.Thread(target=force_shutdown, daemon=True)
    thread.start()
    return {"status": "shutting down"}


def _frontend_dist_dir() -> Path:
    """Resolve the built SvelteKit static bundle.

    The backend always runs unfrozen from backend/.venv (only launcher.py is
    packaged with PyInstaller — see launcher.spec), so this is always a plain
    on-disk path relative to the repo root, never a PyInstaller _MEIPASS path.
    """
    repo_root = Path(__file__).resolve().parents[4]  # …/api/app.py -> repo root
    return repo_root / "frontend" / "build"


_FRONTEND_DIR = _frontend_dist_dir()
if _FRONTEND_DIR.is_dir():
    from fastapi.responses import FileResponse

    @app.get("/{full_path:path}", include_in_schema=False)
    async def _spa(full_path: str):
        """Serve the built frontend, falling back to index.html for client-side routes.

        Must not swallow unmatched /api or /media requests into a 200 HTML
        response — that masks real 404s (e.g. a removed/misspelled endpoint)
        behind a confusing "JSON.parse failed on '<!doctype html>...'" error.
        """
        if full_path.startswith("api/") or full_path.startswith("media/"):
            raise HTTPException(status_code=404, detail="Not Found")
        candidate = _FRONTEND_DIR / full_path
        if full_path and candidate.is_file():
            return FileResponse(candidate)
        return FileResponse(_FRONTEND_DIR / "index.html")
else:
    logging.getLogger("fernkam").warning(
        "Frontend build not found at %s — run `npm run build` in frontend/ to serve the UI from this server.",
        _FRONTEND_DIR,
    )
