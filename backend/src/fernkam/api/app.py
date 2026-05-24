from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from fernkam.api.routers import albums, faces, media, photos, tags


@asynccontextmanager
async def lifespan(app: FastAPI):
    yield


app = FastAPI(
    title="fernKam API",
    version="0.2.0",
    description="Self-hosted photo & video organizer API",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:5173",
        "http://localhost:4173",
        "http://localhost:3000",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(albums.router, prefix="/api/albums", tags=["albums"])
app.include_router(photos.router, prefix="/api/photos", tags=["photos"])
app.include_router(tags.router, prefix="/api/tags", tags=["tags"])
app.include_router(faces.router, prefix="/api/faces", tags=["faces"])
app.include_router(media.router, prefix="/media", tags=["media"])


@app.get("/api/health")
async def health() -> dict:
    return {"status": "ok", "version": "0.2.0"}
