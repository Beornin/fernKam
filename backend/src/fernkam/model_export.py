"""Build ONNX towers for an open_clip model (BioCLIP 2) from its official weights.

BioCLIP 2 is published only as PyTorch weights. fernKam runs models through
onnxruntime and does not install PyTorch, so this runs once, in a throwaway
environment that uv creates just for the command:

    uv run --with open-clip-torch fernkam export-model bioclip2

It downloads the checkpoint (about 1.7 GB), writes vision_model.onnx,
text_model.onnx, preprocessor_config.json (read off the model's own
preprocessing) and a CLIP tokenizer into data/models/<key>/, and checks that
onnxruntime reproduces PyTorch's output before keeping anything.
"""
from __future__ import annotations

import json
import logging
import shutil
from pathlib import Path
from typing import Callable, Optional

import numpy as np

logger = logging.getLogger(__name__)

_CLIP_TOKENIZER = "https://huggingface.co/Xenova/clip-vit-base-patch32/resolve/main/tokenizer.json"
_RESAMPLE = {"nearest": 0, "lanczos": 1, "bilinear": 2, "bicubic": 3, "box": 4, "hamming": 5}


def preprocess_config(preprocess) -> dict:
    """open_clip's torchvision preprocessing -> Hugging Face image-processor keys."""
    from torchvision import transforms as T

    cfg: dict = {"rescale_factor": 1 / 255, "torchvision": True}
    for t in getattr(preprocess, "transforms", [preprocess]):
        if isinstance(t, T.Resize):
            size = t.size
            if isinstance(size, int) or (isinstance(size, (list, tuple)) and len(size) == 1):
                cfg["size"] = {"shortest_edge": int(size if isinstance(size, int) else size[0])}
            else:
                cfg["size"] = {"height": int(size[0]), "width": int(size[1])}
            cfg["resample"] = _RESAMPLE.get(str(t.interpolation.value).lower(), 3)
        elif isinstance(t, T.CenterCrop):
            h, w = t.size
            cfg["do_center_crop"] = True
            cfg["crop_size"] = {"height": int(h), "width": int(w)}
        elif isinstance(t, T.Normalize):
            cfg["image_mean"] = [float(x) for x in t.mean]
            cfg["image_std"] = [float(x) for x in t.std]
    if "size" not in cfg or "image_mean" not in cfg:
        raise ValueError(f"unexpected preprocessing, cannot export: {preprocess}")
    return cfg


def export_open_clip(model, preprocess, out_dir: Path, context_length: int = 77,
                     tokenizer_url: Optional[str] = _CLIP_TOKENIZER,
                     progress: Optional[Callable[[str], None]] = None) -> Path:
    """Export model.encode_image / encode_text to ONNX in out_dir, verified
    against PyTorch. Writes into a temporary folder and swaps it in at the end."""
    import onnxruntime as ort
    import torch

    say = progress or (lambda m: logger.info(m))
    model = model.eval().float()
    tmp = out_dir.with_name(out_dir.name + ".export")
    shutil.rmtree(tmp, ignore_errors=True)
    tmp.mkdir(parents=True)

    cfg = preprocess_config(preprocess)
    side = (cfg.get("crop_size") or cfg["size"]).get("height") or cfg["size"]["shortest_edge"]

    class Vision(torch.nn.Module):
        def __init__(self, m):
            super().__init__()
            self.m = m

        def forward(self, pixel_values):
            return self.m.encode_image(pixel_values)

    class Text(torch.nn.Module):
        def __init__(self, m):
            super().__init__()
            self.m = m

        def forward(self, input_ids):
            return self.m.encode_text(input_ids)

    image = torch.randn(2, 3, side, side)
    ids = torch.zeros(2, context_length, dtype=torch.long)
    ids[:, 0], ids[:, 1:4], ids[:, 4] = 49406, torch.tensor([320, 1125, 539]), 49407

    say("Exporting the image tower…")
    torch.onnx.export(Vision(model), (image,), str(tmp / "vision_model.onnx"),
                      input_names=["pixel_values"], output_names=["image_embeds"],
                      dynamic_axes={"pixel_values": {0: "batch"}, "image_embeds": {0: "batch"}},
                      opset_version=17, dynamo=False)
    say("Exporting the text tower…")
    torch.onnx.export(Text(model), (ids,), str(tmp / "text_model.onnx"),
                      input_names=["input_ids"], output_names=["text_embeds"],
                      dynamic_axes={"input_ids": {0: "batch"}, "text_embeds": {0: "batch"}},
                      opset_version=17, dynamo=False)

    say("Checking the export against PyTorch…")
    with torch.no_grad():
        want_img = model.encode_image(image).numpy()
        want_txt = model.encode_text(ids).numpy()
    got_img = ort.InferenceSession(str(tmp / "vision_model.onnx"), providers=["CPUExecutionProvider"]) \
        .run(None, {"pixel_values": image.numpy()})[0]
    got_txt = ort.InferenceSession(str(tmp / "text_model.onnx"), providers=["CPUExecutionProvider"]) \
        .run(None, {"input_ids": ids.numpy()})[0]

    def cos(a, b):
        a = a / np.linalg.norm(a, axis=-1, keepdims=True)
        b = b / np.linalg.norm(b, axis=-1, keepdims=True)
        return float((a * b).sum(-1).min())

    if cos(want_img, got_img) < 0.999 or cos(want_txt, got_txt) < 0.999:
        shutil.rmtree(tmp, ignore_errors=True)
        raise RuntimeError(f"export does not match PyTorch (image {cos(want_img, got_img):.4f}, "
                           f"text {cos(want_txt, got_txt):.4f})")

    (tmp / "preprocessor_config.json").write_text(json.dumps(cfg, indent=2))
    (tmp / "text_config.json").write_text(json.dumps(
        {"context_length": context_length, "lowercase": False, "prompt": "a photo of {}."}))
    (tmp / "export_info.json").write_text(json.dumps(
        {"dim": int(got_img.shape[-1]), "image_check": cos(want_img, got_img),
         "text_check": cos(want_txt, got_txt)}, indent=2))
    if tokenizer_url:
        # BioCLIP keeps CLIP's tokenizer. Reuse the copy CLIP downloaded, if any.
        from fernkam.clip_embed import model_dir as clip_dir
        local = clip_dir() / "tokenizer.json"
        if local.is_file():
            shutil.copy(local, tmp / "tokenizer.json")
        else:
            import urllib.request
            urllib.request.urlretrieve(tokenizer_url, tmp / "tokenizer.json")

    shutil.rmtree(out_dir, ignore_errors=True)
    tmp.replace(out_dir)
    return out_dir


def export(key: str, progress: Optional[Callable[[str], None]] = None) -> Path:
    """Export a registered open_clip model from its official weights."""
    from fernkam.embed_models import MODELS, model_dir

    info = MODELS[key]
    if info.source != "export":
        raise ValueError(f"{info.label} downloads ready-made; no export needed")
    try:
        import open_clip
    except ImportError as exc:
        raise SystemExit(
            "This needs PyTorch and open_clip, which fernKam does not install. Run:\n"
            f"  uv run --with open-clip-torch fernkam export-model {key}") from exc
    say = progress or print
    say(f"Downloading {info.label} ({info.weights})…")
    model, preprocess = open_clip.create_model_from_pretrained(info.weights)
    out = export_open_clip(model, preprocess, model_dir(key), progress=say)
    dim = json.loads((out / "export_info.json").read_text())["dim"]
    if dim != info.dim:
        raise RuntimeError(f"{info.label} produced {dim}-d vectors, expected {info.dim}")
    say(f"{info.label} is ready in {out}")
    return out
