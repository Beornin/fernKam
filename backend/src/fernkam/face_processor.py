"""InsightFace-based face detection, embedding, and similarity search.

Workflow:
  1. detect_and_embed(path) – detect face regions + extract 512-dim embeddings
  2. store_embedding(face_id, embedding) – persist bytes to DB
  3. find_similar_pg(db, query, ...) – pgvector indexed cosine similarity (preferred)
  4. cluster_faces() – DBSCAN cluster of faces without a person
"""
from __future__ import annotations

import logging
import os
import struct
import warnings
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Optional
from uuid import UUID

import numpy as np

logger = logging.getLogger(__name__)

_app = None  # lazy InsightFace app singleton
_EMBED_DIM = 512

# Dedicated thread pool for blocking InsightFace work so the FastAPI default
# executor (which serves all request handlers) is never starved by face scans.
# Single worker by default because the InsightFace model is shared and not
# thread-safe under concurrent .get() calls on CPU; with GPU we still serialize
# to keep VRAM predictable.
FACE_EXECUTOR = ThreadPoolExecutor(
    max_workers=int(os.getenv("FERNKAM_FACE_WORKERS", "1")),
    thread_name_prefix="face",
)


def _detect_providers() -> list[str]:
    """Return ONNX providers list, preferring CUDA when env+runtime allow it."""
    if os.getenv("FERNKAM_FACE_GPU", "1") == "0":
        return ["CPUExecutionProvider"]
    try:
        import onnxruntime as ort  # type: ignore
        avail = set(ort.get_available_providers())
        if "CUDAExecutionProvider" in avail:
            return ["CUDAExecutionProvider", "CPUExecutionProvider"]
    except Exception:
        pass
    return ["CPUExecutionProvider"]


def _get_app():
    """Lazy-load InsightFace (downloads models on first call ~100 MB)."""
    global _app
    if _app is None:
        warnings.filterwarnings(
            "ignore",
            message="`estimate` is deprecated",
            category=FutureWarning,
            module="skimage",
        )
        from insightface.app import FaceAnalysis
        providers = _detect_providers()
        det_size = int(os.getenv("FERNKAM_FACE_DET_SIZE", "416"))
        print(
            f"[face] Loading InsightFace buffalo_l ({providers[0]}, det_size={det_size})...",
            flush=True,
        )
        _app = FaceAnalysis(name="buffalo_l", providers=providers)
        _app.prepare(ctx_id=0, det_size=(det_size, det_size))
        print(f"[face] InsightFace buffalo_l loaded ({providers[0]})", flush=True)
        logger.info("InsightFace buffalo_l model loaded providers=%s det=%d", providers, det_size)
    return _app


# ─────────────────────────── detection ──────────────────────────────────────

def decode_image(image_path: Path | str):
    """Decode an image file to a BGR ndarray.

    Tries cv2.imread first (fast). Falls back to PIL for 16-bit / compressed
    TIFFs that cv2 cannot handle. Returns None if all decoders fail.
    Runs in a regular thread pool — safe to parallelise across photos.
    """
    import cv2

    path = Path(image_path)
    try:
        img = cv2.imread(str(path))
    except Exception:
        img = None
    if img is not None:
        return img

    try:
        from PIL import Image, ImageOps
        Image.MAX_IMAGE_PIXELS = None
        pil_img = Image.open(path)
        pil_img = ImageOps.exif_transpose(pil_img)
        if pil_img.mode != "RGB":
            pil_img = pil_img.convert("RGB")
        img = cv2.cvtColor(np.array(pil_img), cv2.COLOR_RGB2BGR)
        return img
    except Exception:
        logger.warning("Could not read image: %s", path)
        return None


def run_face_inference(img_bgr) -> list[dict]:
    """Run InsightFace detection + embedding on an already-decoded BGR array.

    Returns list of {x, y, w, h, score, embedding} dicts.
    Must run in FACE_EXECUTOR to serialise GPU access.
    """
    app = _get_app()
    faces = app.get(img_bgr)
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


def detect_and_embed(image_path: Path | str):
    """Detect all faces in an image and return (detections, img_bgr).

    Combined wrapper around decode_image + run_face_inference kept for
    backward compatibility. Prefer calling them separately when you want
    to parallelise decode across scan slots.
    """
    img = decode_image(image_path)
    if img is None:
        return [], None
    return run_face_inference(img), img


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


# ─────────────────────────── pgvector helpers ────────────────────────────────

def embedding_to_pgvector(emb: np.ndarray) -> list[float]:
    """Convert a numpy embedding to a Python list[float] suitable for assigning
    directly to a `pgvector.sqlalchemy.Vector` column."""
    return [float(x) for x in emb.astype(np.float32).tolist()]


def _pgvector_literal(emb: np.ndarray) -> str:
    """Render an embedding as a pgvector text literal '[v1,v2,...]' for raw SQL."""
    arr = emb.astype(np.float32)
    return "[" + ",".join(f"{x:.6f}" for x in arr.tolist()) + "]"


async def find_similar_pg_batch(
    db,
    queries: list,
    *,
    confirmed_only: bool = False,
    ignored_only: bool = False,
    k: int = 1,
    min_score: float = 0.0,
) -> list[list[dict]]:
    """Batch cosine-similarity lookup: one SQL round-trip for multiple embeddings.

    Returns a list of length len(queries); each element is a list of
    {person_tag_id, score} dicts sorted descending by score.
    """
    if not queries:
        return []

    from sqlalchemy import text

    where_parts = ["embedding_v IS NOT NULL"]
    if confirmed_only:
        where_parts += ["person_tag_id IS NOT NULL", "status = 'confirmed'"]
    if ignored_only:
        where_parts.append("status = 'ignored'")
    where_clause = " AND ".join(where_parts)

    val_rows = ", ".join(
        f"({i}::int, '{_pgvector_literal(emb)}'::vector(512))"
        for i, emb in enumerate(queries)
    )

    sql = text(
        f"WITH queries(idx, emb) AS (VALUES {val_rows}) "
        f"SELECT q.idx, nb.person_tag_id, 1 - (nb.embedding_v <=> q.emb) AS score "
        f"FROM queries q "
        f"CROSS JOIN LATERAL ("
        f"  SELECT person_tag_id, embedding_v FROM faces "
        f"  WHERE {where_clause} "
        f"  ORDER BY embedding_v <=> q.emb LIMIT {k}"
        f") nb "
        f"WHERE 1 - (nb.embedding_v <=> q.emb) >= {float(min_score):.8f}"
    )
    rows = (await db.execute(sql)).fetchall()

    results: list[list[dict]] = [[] for _ in queries]
    for idx, person_tag_id, score in rows:
        results[int(idx)].append({"person_tag_id": person_tag_id, "score": float(score)})
    for r in results:
        r.sort(key=lambda x: x["score"], reverse=True)
    return results


async def find_similar_pg(
    db,
    query: np.ndarray,
    *,
    confirmed_only: bool = False,
    ignored_only: bool = False,
    exclude_face_id=None,
    k: int = 10,
    min_score: float = 0.0,
) -> list[dict]:
    """Indexed cosine-similarity lookup against `faces.embedding_v` (HNSW).

    Returns list of {face_id, person_tag_id, score} sorted desc by score.
    Score = 1 - cosine_distance, in [0, 1] for unit-norm vectors.

    `db` is an AsyncSession.
    """
    from sqlalchemy import text  # local import keeps top of file lean

    # Pre-filter via WHERE so the HNSW index is actually used.
    where = ["embedding_v IS NOT NULL"]
    params: dict = {"q": _pgvector_literal(query), "k": k}
    if confirmed_only:
        where.append("person_tag_id IS NOT NULL")
        where.append("status = 'confirmed'")
    if ignored_only:
        where.append("status = 'ignored'")
    if exclude_face_id is not None:
        where.append("id <> :exclude_id")
        params["exclude_id"] = str(exclude_face_id)

    sql = text(
        "SELECT id, person_tag_id, 1 - (embedding_v <=> CAST(:q AS vector)) AS score "
        "FROM faces WHERE " + " AND ".join(where) + " "
        "ORDER BY embedding_v <=> CAST(:q AS vector) "
        "LIMIT :k"
    )
    rows = (await db.execute(sql, params)).fetchall()

    out = []
    for face_id, person_tag_id, score in rows:
        s = float(score)
        if s < min_score:
            continue
        out.append({"face_id": face_id, "person_tag_id": person_tag_id, "score": s})
    return out


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
