from __future__ import annotations

import logging
import os
import signal
import sys
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import text

from fernkam.api.routers import albums, faces, media, people, photos, sync, tags
from fernkam.db.session import get_async_engine

# Configure logging to output to terminal
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[logging.StreamHandler(sys.stdout)]
)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Check database connectivity on startup
    print("Checking database connectivity...")
    try:
        engine = get_async_engine()
        async with engine.begin() as conn:
            result = await conn.execute(text("SELECT 1"))
            print("✓ Database connection successful")
    except Exception as e:
        print(f"✗ Database connection failed: {e}")
        print(f"  Check your PostgreSQL configuration in .env file")
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

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Allow all origins for development
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


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
app.include_router(media.router, prefix="/media", tags=["media"])


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
    import asyncio
    import threading
    
    def force_shutdown():
        import time
        time.sleep(0.1)
        # Try multiple methods
        try:
            os.kill(os.getpid(), signal.SIGINT)
        except:
            pass
        try:
            time.sleep(0.5)
            os._exit(0)
        except:
            pass
    
    thread = threading.Thread(target=force_shutdown, daemon=True)
    thread.start()
    return {"status": "shutting down"}
