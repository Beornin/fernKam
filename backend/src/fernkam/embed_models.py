"""Extra image models for Tag Review, beside CLIP (clip_embed.py).

Each model turns a photo into a vector. Tag Review trains one small classifier
per tag and per model, then learns per tag how much to trust each model (see
tag_learning). A wildlife model ends up weighted for species tags, a general
model for scenes, without anyone deciding that by hand.

  siglip2_512   SigLIP 2 so400m at 512 px: the most detail, for small or distant subjects
  siglip2       SigLIP 2 so400m at 384 px: general scenes and objects, much stronger than CLIP
  siglip2_base  SigLIP 2 base: the same family, small enough for a CPU
  bioclip2      BioCLIP 2: organisms, trained to tell similar species apart

Everything runs through onnxruntime, like CLIP and the face pipeline, so no
new ML stack is needed at runtime. SigLIP 2 downloads ready-made ONNX files
from onnx-community (Hugging Face's Transformers.js organisation). BioCLIP 2 is
published only as PyTorch weights, so it is exported once from the official
checkpoint (model_export.py, run through `uv run --with open-clip-torch`).

A model directory holds vision_model.onnx and preprocessor_config.json, plus,
for finding photos by a tag's name, text_model.onnx and tokenizer.json. How an
image is resized and normalised is read from preprocessor_config.json, not
hard-coded, so a model is used exactly the way it was trained.
"""
from __future__ import annotations

import json
import logging
import os
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable, Optional

import numpy as np

logger = logging.getLogger(__name__)

_HF = "https://huggingface.co/{repo}/resolve/main/{path}"


@dataclass(frozen=True)
class ModelInfo:
    key: str
    label: str
    purpose: str
    dim: int
    source: str                  # "download" (ready-made ONNX) or "export" (from PyTorch weights)
    repo: str = ""               # download: Hugging Face repo with onnx/vision_model.onnx
    weights: str = ""            # export: open_clip id of the official checkpoint
    size_mb: int = 0             # vision tower, roughly
    text_size_mb: int = 0
    gpu_recommended: bool = False
    thumb: str = "md"            # thumbnail it reads: md = 480 px, lg = 960 px (for 512 px models)
    recommended_24gb: bool = False
    # Images per inference call. Attention memory grows with batch x tokens^2:
    # batch 32 at 512 px (1024 tokens) needs ~2 GB per layer, fills a 24 GB
    # card, and Windows then spills to system RAM (measured: 1 photo/s).
    # Keep batch x tokens^2 about constant. FERNKAM_EMBED_BATCH overrides.
    batch: int = 32


MODELS: dict[str, ModelInfo] = {
    m.key: m for m in (
        ModelInfo(
            "siglip2_512", "SigLIP 2 (so400m, 512 px)",
            "The most detailed: general scenes and objects, and small or distant subjects "
            "(a bird in a big frame) that lower resolutions blur. Reads 960 px thumbnails. Needs a GPU.",
            1152, "download", repo="onnx-community/siglip2-so400m-patch16-512-ONNX",
            size_mb=1700, text_size_mb=1800, gpu_recommended=True, thumb="lg", recommended_24gb=True,
            batch=8),
        ModelInfo(
            "siglip2", "SigLIP 2 (so400m, 384 px)",
            "General scenes, objects and activities. Much stronger than CLIP, faster than 512 px.",
            1152, "download", repo="onnx-community/siglip2-so400m-patch14-384-ONNX",
            size_mb=1700, text_size_mb=1800, gpu_recommended=True, batch=16),
        ModelInfo(
            "siglip2_base", "SigLIP 2 (base)",
            "The same family at a quarter of the size: stronger than CLIP and fine on a CPU.",
            768, "download", repo="onnx-community/siglip2-base-patch16-224-ONNX",
            size_mb=370, text_size_mb=1100),
        ModelInfo(
            "bioclip2", "BioCLIP 2",
            "Wildlife. Trained on over 200 million images of organisms to tell similar "
            "species apart. Built once from the official weights.",
            768, "export", weights="hf-hub:imageomics/bioclip-2",
            size_mb=1200, text_size_mb=500, gpu_recommended=True, recommended_24gb=True),
    )
}

VISION_FILES = ("vision_model.onnx", "preprocessor_config.json")
TEXT_FILES = ("text_model.onnx", "tokenizer.json")


def models_root() -> Path:
    from fernkam.config import get_settings
    raw = os.getenv("FERNKAM_MODELS_DIR") or ""
    if raw:
        return Path(raw)
    return Path(get_settings().thumb_cache_dir).parent / "models"


def model_dir(key: str) -> Path:
    return models_root() / key


def _present(d: Path, names: Iterable[str]) -> bool:
    return all((d / n).is_file() and (d / n).stat().st_size > 0 for n in names)


def installed(key: str) -> bool:
    return _present(model_dir(key), VISION_FILES)


def text_installed(key: str) -> bool:
    return _present(model_dir(key), TEXT_FILES)


def export_command(key: str) -> str:
    return f"uv run --with open-clip-torch fernkam export-model {key}"


# ── downloads ────────────────────────────────────────────────────────────────

def _fetch(url: str, dest: Path, progress: Optional[Callable[[str], None]], label: str,
           optional: bool = False) -> bool:
    """Stream url to dest atomically. Returns False for a missing optional file."""
    import urllib.error
    import urllib.request

    tmp = dest.with_name(dest.name + ".part")
    try:
        with urllib.request.urlopen(url, timeout=60) as r, open(tmp, "wb") as f:
            total = int(r.headers.get("Content-Length") or 0)
            done = 0
            while chunk := r.read(1 << 20):
                f.write(chunk)
                done += len(chunk)
                if progress and total:
                    progress(f"Downloading {label}… {done >> 20} / {total >> 20} MB")
    except urllib.error.HTTPError as e:
        tmp.unlink(missing_ok=True)
        if optional and e.code == 404:
            return False
        raise
    tmp.replace(dest)
    return True


def download(key: str, text: bool = False, progress: Optional[Callable[[str], None]] = None) -> Path:
    """Fetch a downloadable model's vision tower (or, with text=True, its text
    tower and tokenizer). Files already present are kept."""
    info = MODELS[key]
    if info.source != "download":
        raise ValueError(f"{info.label} is built from its weights: {export_command(key)}")
    d = model_dir(key)
    d.mkdir(parents=True, exist_ok=True)
    wanted = ([("onnx/text_model.onnx", "text_model.onnx"), ("tokenizer.json", "tokenizer.json")]
              if text else
              [("onnx/vision_model.onnx", "vision_model.onnx"),
               ("preprocessor_config.json", "preprocessor_config.json")])
    for remote, local in wanted:
        if not (d / local).is_file():
            _fetch(_HF.format(repo=info.repo, path=remote), d / local, progress, f"{info.label} {local}")
        if local.endswith(".onnx"):
            # Towers over 2 GB keep their weights in a side file.
            data = local + "_data"
            if not (d / data).is_file():
                _fetch(_HF.format(repo=info.repo, path=remote + "_data"), d / data, progress,
                       f"{info.label} {data}", optional=True)
    if text and not (d / "text_config.json").is_file():
        # SigLIP was trained on lower-case text padded to 64 tokens, with the
        # padding attended to (no mask).
        (d / "text_config.json").write_text(json.dumps(
            {"context_length": 64, "lowercase": True, "mask_padding": False,
             "prompt": "a photo of {}"}))
    return d


def remove(key: str) -> None:
    import shutil
    release(key)
    shutil.rmtree(model_dir(key), ignore_errors=True)


# ── runtime ──────────────────────────────────────────────────────────────────

_RESAMPLE = {0: "NEAREST", 1: "LANCZOS", 2: "BILINEAR", 3: "BICUBIC", 4: "BOX", 5: "HAMMING"}


class Preprocessor:
    """preprocessor_config.json (Hugging Face image-processor keys) -> tensor."""

    def __init__(self, cfg: dict):
        size = cfg.get("size") or {"height": 224, "width": 224}
        if isinstance(size, int):
            size = {"shortest_edge": size}
        self.exact = (size["height"], size["width"]) if "height" in size else None
        self.shortest = size.get("shortest_edge")
        crop = cfg.get("crop_size") if cfg.get("do_center_crop") else None
        if isinstance(crop, int):
            crop = {"height": crop, "width": crop}
        self.crop = (crop["height"], crop["width"]) if crop else None
        self.resample = _RESAMPLE.get(int(cfg.get("resample", 3)), "BICUBIC")
        # Exported open_clip models were trained with torchvision, which
        # rounds the centre-crop offset; Hugging Face floors it.
        self.torchvision = bool(cfg.get("torchvision", False))
        self.scale = float(cfg.get("rescale_factor", 1 / 255))
        self.mean = np.array(cfg.get("image_mean", [0.5, 0.5, 0.5]), np.float32).reshape(3, 1, 1)
        self.std = np.array(cfg.get("image_std", [0.5, 0.5, 0.5]), np.float32).reshape(3, 1, 1)

    def __call__(self, img) -> np.ndarray:
        from PIL import Image

        if img.mode != "RGB":
            img = img.convert("RGB")
        method = getattr(Image, self.resample)
        if self.exact:
            h, w = self.exact
            img = img.resize((w, h), method)
        elif self.shortest:
            # Both torchvision and Hugging Face floor the long side.
            w, h = img.size
            short, long = (w, h) if w <= h else (h, w)
            new_long = int(self.shortest * long / short)
            img = img.resize((self.shortest, new_long) if w <= h else (new_long, self.shortest), method)
        if self.crop:
            ch, cw = self.crop
            w, h = img.size
            if self.torchvision:
                left, top = int(round((w - cw) / 2.0)), int(round((h - ch) / 2.0))
            else:
                left, top = (w - cw) // 2, (h - ch) // 2
            img = img.crop((left, top, left + cw, top + ch))
        a = np.asarray(img, dtype=np.float32).transpose(2, 0, 1) * self.scale
        return (a - self.mean) / self.std


def _l2(a: np.ndarray) -> np.ndarray:
    return a / np.maximum(np.linalg.norm(a, axis=-1, keepdims=True), 1e-12)


def _pick_output(sess, preferred: tuple[str, ...]) -> str:
    outs = sess.get_outputs()
    names = [o.name for o in outs]
    for p in preferred:
        if p in names:
            return p
    for o in outs:
        if len(o.shape or []) == 2:
            return o.name
    return names[0]


class _Runtime:
    def __init__(self, key: str):
        self.key = key
        self.batch = int(os.getenv("FERNKAM_EMBED_BATCH") or MODELS[key].batch)
        self.lock = threading.Lock()
        self.vision = self.text = self.tok = self.pre = None
        self.text_cfg: dict = {}

    def _session(self, path: Path):
        import onnxruntime as ort
        from fernkam.clip_embed import _providers
        return ort.InferenceSession(str(path), providers=_providers())

    def load_vision(self):
        with self.lock:
            if self.vision is None:
                d = model_dir(self.key)
                if not installed(self.key):
                    raise FileNotFoundError(f"{MODELS[self.key].label} is not installed")
                self.pre = Preprocessor(json.loads((d / "preprocessor_config.json").read_text()))
                self.vision = self._session(d / "vision_model.onnx")
                self.v_in = self.vision.get_inputs()[0]
                self.v_out = _pick_output(self.vision, ("image_embeds", "pooler_output", "embeddings"))
                logger.info("[%s] vision tower loaded", self.key)
        return self.vision

    def load_text(self):
        with self.lock:
            if self.text is None:
                from tokenizers import Tokenizer
                d = model_dir(self.key)
                if not text_installed(self.key):
                    raise FileNotFoundError(f"{MODELS[self.key].label} has no text tower yet")
                cfg_file = d / "text_config.json"
                self.text_cfg = json.loads(cfg_file.read_text()) if cfg_file.is_file() else {}
                self.tok = Tokenizer.from_file(str(d / "tokenizer.json"))
                self.text = self._session(d / "text_model.onnx")
                self.t_inputs = {i.name for i in self.text.get_inputs()}
                self.t_out = _pick_output(self.text, ("text_embeds", "pooler_output", "embeddings"))
        return self.text

    def embed_images(self, sources: Iterable) -> list[Optional[np.ndarray]]:
        from PIL import Image

        sess = self.load_vision()
        dtype = np.float16 if "float16" in self.v_in.type else np.float32
        srcs = list(sources)
        out: list[Optional[np.ndarray]] = [None] * len(srcs)
        for start in range(0, len(srcs), self.batch):
            batch, keep = [], []
            for i in range(start, min(start + self.batch, len(srcs))):
                s = srcs[i]
                try:
                    img = s if hasattr(s, "mode") else Image.open(s)
                    batch.append(self.pre(img))
                    keep.append(i)
                except Exception as exc:
                    logger.debug("[%s] skip %s: %s", self.key, s, exc)
            if not batch:
                continue
            vecs = sess.run([self.v_out], {self.v_in.name: np.stack(batch).astype(dtype)})[0]
            vecs = _l2(np.asarray(vecs, dtype=np.float32).reshape(len(batch), -1))
            for slot, v in zip(keep, vecs):
                out[slot] = v
        return out

    def embed_text(self, texts: list[str]) -> np.ndarray:
        sess = self.load_text()
        ctx = int(self.text_cfg.get("context_length", 77))
        pad = self.tok.token_to_id("<pad>")
        pad = 0 if pad is None else pad
        ids = np.full((len(texts), ctx), pad, dtype=np.int64)
        mask = np.zeros_like(ids)
        for i, t in enumerate(texts):
            enc = self.tok.encode(t.lower() if self.text_cfg.get("lowercase") else t).ids[:ctx]
            ids[i, : len(enc)] = enc
            mask[i, : len(enc)] = 1
        feed = {"input_ids": ids}
        if "attention_mask" in self.t_inputs:
            feed["attention_mask"] = mask if self.text_cfg.get("mask_padding", True) else np.ones_like(ids)
        return _l2(np.asarray(sess.run([self.t_out], feed)[0], dtype=np.float32))

    def release(self):
        with self.lock:
            self.vision = self.text = self.tok = None


_runtimes: dict[str, _Runtime] = {}
_runtimes_lock = threading.Lock()


def runtime(key: str) -> _Runtime:
    if key not in MODELS:
        raise KeyError(key)
    with _runtimes_lock:
        return _runtimes.setdefault(key, _Runtime(key))


def embed_images(key: str, sources: Iterable) -> list[Optional[np.ndarray]]:
    """Unit vectors for PIL images or paths; None for any that cannot be read."""
    return runtime(key).embed_images(sources)


def embed_text(key: str, texts: list[str]) -> np.ndarray:
    return runtime(key).embed_text(texts)


def text_prompt(key: str) -> str:
    cfg = model_dir(key) / "text_config.json"
    if cfg.is_file():
        return json.loads(cfg.read_text()).get("prompt", "a photo of {}")
    return "a photo of {}"


def release(key: str) -> None:
    """Free a model's GPU memory, e.g. after indexing, so a local vision
    model has room."""
    if key in _runtimes:
        _runtimes[key].release()
