"""CLIP image/text embeddings for semantic search and tag propagation.

Runs CLIP ViT-B/32 through onnxruntime, which is already a dependency (the face
pipeline uses it), so this adds no heavyweight ML stack. Both ONNX towers emit
the *projected* 512-d vectors, so image and text embeddings live in one space
and can be compared directly with pgvector cosine distance.

Models are downloaded on first use into `data/models/clip`, mirroring how
InsightFace caches buffalo_l — they are deliberately not bundled into the
PyInstaller build, which would add 600 MB to the exe.
"""
from __future__ import annotations

import logging
import os
import threading
from pathlib import Path
from typing import Iterable, Optional

import numpy as np

logger = logging.getLogger(__name__)

EMBED_DIM = 512
_HF_BASE = "https://huggingface.co/Xenova/clip-vit-base-patch32/resolve/main/"
_FILES = {
    "vision_model.onnx": "onnx/vision_model.onnx",
    "text_model.onnx": "onnx/text_model.onnx",
    "tokenizer.json": "tokenizer.json",
}

# CLIP ViT-B/32 preprocessing constants (preprocessor_config.json).
_SIZE = 224
_MEAN = np.array([0.48145466, 0.4578275, 0.40821073], dtype=np.float32).reshape(3, 1, 1)
_STD = np.array([0.26862954, 0.26130258, 0.27577711], dtype=np.float32).reshape(3, 1, 1)
_CONTEXT_LEN = 77  # CLIP truncates/pads text to this

_vision_sess = None
_text_sess = None
_tokenizer = None
_lock = threading.Lock()


def model_dir() -> Path:
    from fernkam.config import get_settings
    raw = os.getenv("FERNKAM_CLIP_DIR") or ""
    if raw:
        return Path(raw)
    # Sits beside the thumbnail cache so both live under the same writable dir.
    return Path(get_settings().thumb_cache_dir).parent / "models" / "clip"


def ensure_models(progress=None) -> Path:
    """Download the ONNX towers + tokenizer if absent. ~606 MB, once."""
    import urllib.request

    d = model_dir()
    d.mkdir(parents=True, exist_ok=True)
    for local, remote in _FILES.items():
        p = d / local
        if p.exists() and p.stat().st_size > 0:
            continue
        if progress:
            progress(f"Downloading {local}…")
        logger.info("[clip] downloading %s", local)
        tmp = p.with_suffix(p.suffix + ".part")
        urllib.request.urlretrieve(_HF_BASE + remote, tmp)
        tmp.replace(p)  # atomic: a half-downloaded model is never seen as ready
    return d


def _providers() -> list[str]:
    """Same policy as the face pipeline — CUDA when available, else CPU."""
    if os.getenv("FERNKAM_CLIP_GPU", "1") == "0":
        return ["CPUExecutionProvider"]
    try:
        import onnxruntime as ort
        if "CUDAExecutionProvider" in set(ort.get_available_providers()):
            return ["CUDAExecutionProvider", "CPUExecutionProvider"]
    except Exception:
        pass
    return ["CPUExecutionProvider"]


def _get_vision():
    global _vision_sess
    if _vision_sess is None:
        with _lock:
            if _vision_sess is None:
                import onnxruntime as ort
                d = ensure_models()
                prov = _providers()
                _vision_sess = ort.InferenceSession(str(d / "vision_model.onnx"), providers=prov)
                logger.info("[clip] vision tower loaded (%s)", prov[0])
    return _vision_sess


def _get_text():
    global _text_sess, _tokenizer
    if _text_sess is None:
        with _lock:
            if _text_sess is None:
                import onnxruntime as ort
                from tokenizers import Tokenizer
                d = ensure_models()
                prov = _providers()
                _text_sess = ort.InferenceSession(str(d / "text_model.onnx"), providers=prov)
                _tokenizer = Tokenizer.from_file(str(d / "tokenizer.json"))
                logger.info("[clip] text tower loaded (%s)", prov[0])
    return _text_sess, _tokenizer


def _preprocess(img) -> np.ndarray:
    """PIL image -> (3,224,224) float32, CLIP-normalised.

    Resize shortest edge to 224 (bicubic), centre-crop, scale to 0-1, normalise.
    """
    from PIL import Image

    if img.mode != "RGB":
        img = img.convert("RGB")
    w, h = img.size
    scale = _SIZE / min(w, h)
    img = img.resize((max(_SIZE, round(w * scale)), max(_SIZE, round(h * scale))), Image.BICUBIC)
    w, h = img.size
    left, top = (w - _SIZE) // 2, (h - _SIZE) // 2
    img = img.crop((left, top, left + _SIZE, top + _SIZE))
    a = np.asarray(img, dtype=np.float32).transpose(2, 0, 1) / 255.0
    return (a - _MEAN) / _STD


def _l2(a: np.ndarray) -> np.ndarray:
    n = np.linalg.norm(a, axis=-1, keepdims=True)
    return a / np.maximum(n, 1e-12)


def embed_images(sources: Iterable) -> "list[Optional[np.ndarray]]":
    """Embed PIL images or file paths. Returns one 512-d unit vector each.

    Entries that cannot be decoded come back as None rather than raising, so one
    corrupt file never aborts a batch of thousands.
    """
    from PIL import Image

    srcs = list(sources)
    if not srcs:
        return []
    batch, keep = [], []
    for i, s in enumerate(srcs):
        try:
            img = s if hasattr(s, "mode") else Image.open(s)
            batch.append(_preprocess(img))
            keep.append(i)
        except Exception as exc:
            logger.debug("[clip] skip %s: %s", s, exc)
    out: list[Optional[np.ndarray]] = [None] * len(srcs)
    if not batch:
        return out
    vecs = _get_vision().run(None, {"pixel_values": np.stack(batch)})[0]
    vecs = _l2(np.asarray(vecs, dtype=np.float32))
    for slot, v in zip(keep, vecs):
        out[slot] = v
    return out


def embed_text(texts: "str | Iterable[str]") -> np.ndarray:
    """Embed one or more strings into the same 512-d space as images."""
    single = isinstance(texts, str)
    items = [texts] if single else list(texts)
    sess, tok = _get_text()
    ids = np.zeros((len(items), _CONTEXT_LEN), dtype=np.int64)
    for i, t in enumerate(items):
        enc = tok.encode(t).ids[:_CONTEXT_LEN]
        ids[i, : len(enc)] = enc
    vecs = _l2(np.asarray(sess.run(None, {"input_ids": ids})[0], dtype=np.float32))
    return vecs[0] if single else vecs


def to_pgvector(v: np.ndarray) -> str:
    """Format a vector as a pgvector literal."""
    return "[" + ",".join(f"{x:.6f}" for x in np.asarray(v, dtype=np.float32)) + "]"


if __name__ == "__main__":
    # Self-check: image and text must land in one comparable space, and a
    # matching caption must score higher than an unrelated one.
    from PIL import Image

    red = Image.new("RGB", (400, 300), (200, 30, 30))
    blue = Image.new("RGB", (400, 300), (30, 30, 200))
    iv = embed_images([red, blue])
    assert all(v is not None for v in iv), "image embedding failed"
    assert iv[0].shape == (EMBED_DIM,), iv[0].shape
    assert abs(float(np.linalg.norm(iv[0])) - 1.0) < 1e-4, "not unit length"

    tv = embed_text(["a solid red image", "a solid blue image"])
    assert tv.shape == (2, EMBED_DIM), tv.shape
    red_red, red_blue = float(iv[0] @ tv[0]), float(iv[0] @ tv[1])
    assert red_red > red_blue, f"red image matched 'blue' better: {red_red} vs {red_blue}"

    bad = embed_images(["/definitely/not/a/file.jpg"])
    assert bad == [None], "missing file should yield None, not raise"
    print(f"ok - clip embeddings (red->red {red_red:.3f} > red->blue {red_blue:.3f})")
