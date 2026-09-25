"""Check the ONNX runtime behind Tag Review's extra image models.

Builds tiny ONNX towers shaped like the real exports (a decoy 3-D output ahead
of the embedding, a float16 variant, a text tower that declares an attention
mask) and checks preprocessing, output selection, batching, padding and
lower-casing against a numpy reference. No downloads, no database.

Run directly: python backend/tests/test_embed_models.py
"""
import json
import os
import sys
import tempfile
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import onnx
from onnx import TensorProto, helper, numpy_helper
from PIL import Image

tmp = Path(tempfile.mkdtemp())
os.environ["FERNKAM_MODELS_DIR"] = str(tmp)
os.environ["FERNKAM_CLIP_GPU"] = "0"

from fernkam import embed_models as em  # noqa: E402

SIDE, DIM, VOCAB, CTX = 16, 24, 50, 8
rng = np.random.default_rng(0)
W_img = rng.normal(size=(3 * SIDE * SIDE, DIM)).astype(np.float32)
E_txt = rng.normal(size=(VOCAB, DIM)).astype(np.float32)


def vision_model(path: Path, fp16: bool = False):
    inp = helper.make_tensor_value_info("pixel_values", TensorProto.FLOAT16 if fp16 else TensorProto.FLOAT,
                                        ["b", 3, SIDE, SIDE])
    nodes = [helper.make_node("Cast", ["pixel_values"], ["x"], to=TensorProto.FLOAT)] if fp16 else \
        [helper.make_node("Identity", ["pixel_values"], ["x"])]
    nodes += [
        helper.make_node("Reshape", ["x", "shape"], ["flat"]),
        helper.make_node("MatMul", ["flat", "W"], ["pooler_output"]),
        helper.make_node("Unsqueeze", ["pooler_output", "axes"], ["last_hidden_state"]),
    ]
    g = helper.make_graph(
        nodes, "v", [inp],
        # The decoy comes first, as last_hidden_state does in real exports.
        [helper.make_tensor_value_info("last_hidden_state", TensorProto.FLOAT, ["b", 1, DIM]),
         helper.make_tensor_value_info("pooler_output", TensorProto.FLOAT, ["b", DIM])],
        [numpy_helper.from_array(np.array([-1, 3 * SIDE * SIDE], np.int64), "shape"),
         numpy_helper.from_array(W_img, "W"),
         numpy_helper.from_array(np.array([1], np.int64), "axes")])
    onnx.save(helper.make_model(g, opset_imports=[helper.make_opsetid("", 17)], ir_version=8), path)


def text_model(path: Path):
    ids = helper.make_tensor_value_info("input_ids", TensorProto.INT64, ["b", CTX])
    mask = helper.make_tensor_value_info("attention_mask", TensorProto.INT64, ["b", CTX])
    g = helper.make_graph(
        [helper.make_node("Gather", ["E", "input_ids"], ["emb"]),
         helper.make_node("Cast", ["attention_mask"], ["m"], to=TensorProto.FLOAT),
         helper.make_node("Unsqueeze", ["m", "axes"], ["m3"]),
         helper.make_node("Mul", ["emb", "m3"], ["masked"]),
         helper.make_node("ReduceSum", ["masked", "axes1"], ["text_embeds"], keepdims=0)],
        "t", [ids, mask],
        [helper.make_tensor_value_info("text_embeds", TensorProto.FLOAT, ["b", DIM])],
        [numpy_helper.from_array(E_txt, "E"),
         numpy_helper.from_array(np.array([2], np.int64), "axes"),
         numpy_helper.from_array(np.array([1], np.int64), "axes1")])
    onnx.save(helper.make_model(g, opset_imports=[helper.make_opsetid("", 17)], ir_version=8), path)


def tokenizer(path: Path):
    from tokenizers import Tokenizer, models, pre_tokenizers
    vocab = {"<pad>": 0, "[UNK]": 1, "a": 2, "photo": 3, "of": 4, "heron": 5, "Heron": 6}
    tok = Tokenizer(models.WordLevel(vocab, unk_token="[UNK]"))
    tok.pre_tokenizer = pre_tokenizers.Whitespace()
    tok.save(str(path))


def install(key: str, cfg: dict, fp16: bool = False, text_cfg: dict | None = None):
    em.MODELS[key] = em.ModelInfo(key, key, "test", DIM, "download", repo="none/none")
    d = tmp / key
    d.mkdir()
    vision_model(d / "vision_model.onnx", fp16)
    (d / "preprocessor_config.json").write_text(json.dumps(cfg))
    if text_cfg is not None:
        text_model(d / "text_model.onnx")
        tokenizer(d / "tokenizer.json")
        (d / "text_config.json").write_text(json.dumps(text_cfg))


def reference(img: Image.Image, mean=0.5, std=0.5) -> np.ndarray:
    a = np.asarray(img.convert("RGB").resize((SIDE, SIDE), Image.BILINEAR), np.float32).transpose(2, 0, 1) / 255
    v = ((a - mean) / std).reshape(-1) @ W_img
    return v / np.linalg.norm(v)


squash = {"size": {"height": SIDE, "width": SIDE}, "resample": 2, "image_mean": [0.5] * 3, "image_std": [0.5] * 3}
assert not em.installed("fake")
install("fake", squash, text_cfg={"context_length": CTX, "lowercase": True, "prompt": "a photo of {}"})
assert em.installed("fake") and em.text_installed("fake")

# 1. Image embeddings: right output picked, unit length, equal to the reference,
#    across more than one batch, unreadable input -> None.
imgs = [Image.fromarray(rng.integers(0, 255, (30 + i, 20 + 2 * i, 3), dtype=np.uint8)) for i in range(40)]
vecs = em.embed_images("fake", imgs + ["/no/such/file.jpg"])
assert len(vecs) == 41 and vecs[-1] is None
for img, v in zip(imgs, vecs):
    assert v.shape == (DIM,) and abs(np.linalg.norm(v) - 1) < 1e-5
    assert np.allclose(v, reference(img), atol=1e-4), "differs from reference"

# 1b. The session only ever sees one batch shape. Each new one makes
#     onnxruntime keep another set of GPU buffers for the rest of the run.
rt = em.runtime("fake")


class _Shapes:
    def __init__(self, s):
        self.s, self.seen = s, set()

    def __getattr__(self, name):
        return getattr(self.s, name)

    def run(self, outs, feed):
        self.seen.update(v.shape for v in feed.values())
        return self.s.run(outs, feed)


rt.vision = spy = _Shapes(rt.vision)
for n in (rt.batch + 3, 1, 5):  # a full batch plus a tail, then short calls
    got = em.embed_images("fake", imgs[:n])
    assert all(np.allclose(g, reference(i), atol=1e-4) for g, i in zip(got, imgs[:n]))
assert len(spy.seen) == 1, f"batch shapes seen: {spy.seen}"
em.release("fake")

# 2. A float16 tower gets float16 input.
install("fake16", squash, fp16=True)
v16 = em.embed_images("fake16", imgs[:3])
assert all(np.dot(a, reference(i)) > 0.999 for a, i in zip(v16, imgs[:3]))

# 3. Shortest-edge resize + centre crop (CLIP/BioCLIP style).
pre = em.Preprocessor({"size": {"shortest_edge": SIDE}, "do_center_crop": True,
                       "crop_size": {"height": SIDE, "width": SIDE}, "resample": 3,
                       "image_mean": [0.48145466, 0.4578275, 0.40821073],
                       "image_std": [0.26862954, 0.26130258, 0.27577711]})
wide = Image.new("RGB", (64, 32), (200, 10, 10))
assert pre(wide).shape == (3, SIDE, SIDE)
assert em.Preprocessor(squash)(wide).shape == (3, SIDE, SIDE)

# 4. Text: lower-cased when the model says so, padded to its context length,
#    attention mask supplied because the tower declares one.
t = em.embed_text("fake", ["A photo of Heron", "a photo of heron"])
assert t.shape == (2, DIM) and np.allclose(t[0], t[1], atol=1e-6), "lower-casing not applied"
want = E_txt[[2, 3, 4, 5]].sum(0)
assert np.allclose(t[1], want / np.linalg.norm(want), atol=1e-5)
assert em.text_prompt("fake") == "a photo of {}"
# SigLIP style: padding attended to (mask all ones), so the pad embedding counts.
install("fakesig", squash, text_cfg={"context_length": CTX, "lowercase": True, "mask_padding": False})
ts = em.embed_text("fakesig", ["a photo of heron"])[0]
want_sig = want + 4 * E_txt[0]
assert np.allclose(ts, want_sig / np.linalg.norm(want_sig), atol=1e-5)

# 5. release() drops the sessions; the next call loads them again.
em.release("fake")
assert em.runtime("fake").vision is None
assert em.embed_images("fake", imgs[:1])[0] is not None

print("ok - embedding model runtime (config-driven preprocessing, output pick, fp16, text)")
