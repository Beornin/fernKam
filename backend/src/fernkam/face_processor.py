"""InsightFace-based face detection, embedding, and similarity search.

Workflow:
  1. detect_and_embed(path) – detect face regions + extract 512-dim embeddings
  2. store_embedding(face_id, embedding) – persist bytes to DB
  3. find_similar(embedding, confirmed_only=True, k=10) – cosine similarity
  4. cluster_unassigned() – DBSCAN cluster of faces without a person

When pgvector is available (CREATE EXTENSION vector), similarity queries use
SQL (<=> cosine distance). Otherwise we load embeddings into numpy and compute
similarity in-process — perfectly fast for a personal library.
"""
from __future__ import annotations

import logging
import struct
from pathlib import Path
from typing import Optional
from uuid import UUID

import numpy as np

logger = logging.getLogger(__name__)

_app = None  # lazy InsightFace app singleton
_EMBED_DIM = 512


def _get_app():
    """Lazy-load InsightFace (downloads models on first call ~100 MB)."""
    global _app
    if _app is None:
        import insightface
        from insightface.app import FaceAnalysis
        print("[face] Loading InsightFace buffalo_l (may download ~100MB on first run)...", flush=True)
        _app = FaceAnalysis(name="buffalo_l", providers=["CPUExecutionProvider"])
        _app.prepare(ctx_id=0, det_size=(640, 640))
        print("[face] InsightFace buffalo_l loaded", flush=True)
        logger.info("InsightFace buffalo_l model loaded")
    return _app


# ─────────────────────────── detection ──────────────────────────────────────

def detect_and_embed(image_path: Path | str) -> list[dict]:
    """Detect all faces in an image and return bbox + embedding for each.

    Returns list of dicts:
      { x, y, w, h, embedding: np.ndarray shape (512,), score: float }
    Coordinates are absolute pixels. Returns [] if no faces found.
    """
    import cv2

    path = Path(image_path)
    img = cv2.imread(str(path))
    if img is None:
        logger.warning("Could not read image: %s", path)
        return []

    app = _get_app()
    faces = app.get(img)

    results = []
    for f in faces:
        x1, y1, x2, y2 = [int(v) for v in f.bbox]
        results.append({
            "x": x1,
            "y": y1,
            "w": x2 - x1,
            "h": y2 - y1,
            "score": float(f.det_score),
            "embedding": f.normed_embedding.astype(np.float32),
        })
    return results


# ─────────────────────────── serialisation ───────────────────────────────────

def embedding_to_bytes(emb: np.ndarray) -> bytes:
    """Serialize a float32 numpy array to raw bytes for storage."""
    return emb.astype(np.float32).tobytes()


def bytes_to_embedding(data: bytes) -> np.ndarray:
    """Deserialize raw bytes back to float32 numpy array."""
    arr = np.frombuffer(data, dtype=np.float32)
    # Some older entries might be wrong length; handle gracefully
    if arr.shape[0] != _EMBED_DIM:
        raise ValueError(f"Expected {_EMBED_DIM}-dim embedding, got {arr.shape[0]}")
    return arr


# ─────────────────────────── similarity ──────────────────────────────────────

def cosine_similarity(a: np.ndarray, b: np.ndarray) -> float:
    """Cosine similarity between two unit-normed embeddings."""
    return float(np.dot(a, b))


def find_similar_numpy(
    query: np.ndarray,
    face_rows: list[tuple[UUID, bytes, Optional[int]]],
    k: int = 10,
    min_score: float = 0.30,
) -> list[dict]:
    """Compute cosine similarity against a list of (face_id, embedding_bytes, person_tag_id).

    Returns top-k matches as [{face_id, person_tag_id, score}] sorted desc.
    """
    results = []
    for face_id, emb_bytes, ptid in face_rows:
        if not emb_bytes:
            continue
        try:
            emb = bytes_to_embedding(emb_bytes)
        except ValueError:
            continue
        score = cosine_similarity(query, emb)
        if score >= min_score:
            results.append({"face_id": face_id, "person_tag_id": ptid, "score": score})

    results.sort(key=lambda r: r["score"], reverse=True)
    return results[:k]


# ─────────────────────────── clustering ──────────────────────────────────────

def cluster_faces(
    face_rows: list[tuple[UUID, bytes]],
    eps: float = 0.35,
    min_samples: int = 2,
) -> dict[int, list[UUID]]:
    """DBSCAN cluster unassigned faces by embedding similarity.

    Returns {cluster_id: [face_ids]}.  Cluster -1 = noise (no cluster).
    """
    from sklearn.cluster import DBSCAN

    valid = []
    valid_ids = []
    for fid, emb_bytes in face_rows:
        if not emb_bytes:
            continue
        try:
            emb = bytes_to_embedding(emb_bytes)
            valid.append(emb)
            valid_ids.append(fid)
        except ValueError:
            continue

    if len(valid) < 2:
        return {}

    X = np.stack(valid)
    # Use cosine distance (1 - cosine_similarity)
    labels = DBSCAN(eps=eps, min_samples=min_samples, metric="cosine").fit_predict(X)

    clusters: dict[int, list[UUID]] = {}
    for label, fid in zip(labels, valid_ids):
        clusters.setdefault(int(label), []).append(fid)
    return clusters
