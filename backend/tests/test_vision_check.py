"""Check the local vision model client behind Tag Review's double-check.

Runs a fake Ollama server (and a fake OpenAI-compatible one) in a thread: it
lists one vision and one text-only model, decodes the JPEG it is sent, and
answers "yes" for reddish pictures. No database, no real model.

Run directly: python backend/tests/test_vision_check.py
"""
import asyncio
import base64
import io
import json
import math
import sys
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import httpx
import numpy as np
from PIL import Image

from fernkam import vision_check as vc

seen: list[dict] = []


def answer(b64: str) -> tuple[str, float]:
    img = Image.open(io.BytesIO(base64.b64decode(b64))).convert("RGB")
    r, g, b = np.asarray(img, dtype=float).reshape(-1, 3).mean(axis=0)
    return ("Yes." if r > b else "no", 0.9 if r > b else 0.2)


class Fake(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _json(self, obj, code=200):
        body = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path == "/api/tags":
            self._json({"models": [{"name": "llama3:8b", "size": 4.7e9},
                                   {"name": "qwen2.5vl:7b", "size": 6e9}]})
        elif self.path == "/v1/models":
            self._json({"data": [{"id": "local-vlm"}]})
        else:
            self._json({}, 404)

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        seen.append({"path": self.path, **body})
        if self.path == "/api/show":
            caps = ["completion", "vision"] if "vl" in body["model"] else ["completion"]
            self._json({"capabilities": caps})
        elif self.path == "/api/chat":
            word, p = answer(body["messages"][-1]["images"][0])
            self._json({"message": {"role": "assistant", "content": word},
                        "logprobs": [{"token": word, "top_logprobs": [
                            {"token": "yes", "logprob": math.log(p)},
                            {"token": "no", "logprob": math.log(1 - p)}]}]})
        elif self.path == "/v1/chat/completions":
            url = body["messages"][-1]["content"][1]["image_url"]["url"]
            assert url.startswith("data:image/jpeg;base64,")
            word, _ = answer(url.split(",", 1)[1])
            self._json({"choices": [{"message": {"content": word}}]})
        else:
            self._json({}, 404)


server = HTTPServer(("127.0.0.1", 0), Fake)
threading.Thread(target=server.serve_forever, daemon=True).start()
base = f"http://127.0.0.1:{server.server_port}"
red, blue = Image.new("RGB", (300, 200), (200, 40, 30)), Image.new("RGB", (300, 200), (30, 40, 200))


async def main():
    # 1. Only vision models are listed; the preferred family is picked.
    models = await vc.list_models(base)
    assert [m["name"] for m in models] == ["qwen2.5vl:7b"], models
    assert vc.pick_model(models, "") == "qwen2.5vl:7b"
    assert vc.pick_model(models, "qwen2.5vl:7b") == "qwen2.5vl:7b"
    assert vc.pick_model(models, "not-pulled:1b") == "qwen2.5vl:7b"
    assert vc.pick_model([], "") is None

    # 2. Ollama API: the question names the tag with its category, the photo
    #    goes as a JPEG, temperature 0; the answer and P(yes) come back.
    async with httpx.AsyncClient() as c:
        v = await vc.ask(c, base, "qwen2.5vl:7b", red, "Wildlife.Birds.Heron")
        assert v.verdict == 1 and abs(v.p_yes - 0.9) < 1e-6, v
        v = await vc.ask(c, base, "qwen2.5vl:7b", blue, "Wildlife.Birds.Heron")
        assert v.verdict == 0 and abs(v.p_yes - 0.2) < 1e-6, v
        req = seen[-1]
        assert '"Heron" (category: Wildlife > Birds)' in req["messages"][-1]["content"]
        assert req["options"]["temperature"] == 0 and req["stream"] is False
        assert "think" not in req
        await vc.ask(c, base, "qwen3-vl:8b", red, "Places.Beach", thinking=True)
        assert seen[-1]["think"] is False

        # 3. OpenAI-compatible server (URL ending in /v1), no token probabilities.
        v = await vc.ask(c, base + "/v1", "local-vlm", red, "Places.Beach")
        assert v.verdict == 1 and v.p_yes is None
    assert [m["name"] for m in await vc.list_models(base + "/v1")] == ["local-vlm"]

    # 4. Unreachable server raises (Tag Review shows "is Ollama running?").
    try:
        await vc.list_models("http://127.0.0.1:9")
        raise AssertionError("expected a connection error")
    except httpx.HTTPError:
        pass


asyncio.run(main())

# 5. Answer parsing.
assert vc._parse("Yes.", None).verdict == 1
assert vc._parse("  NO", None).verdict == 0
assert vc._parse("<think>hmm</think> yes", None).verdict == 1
assert vc._parse("maybe", None).verdict is None
assert vc.question("Beach") == 'Does this photo show "Beach"? Answer yes or no.'

print("ok - vision check client (Ollama and OpenAI-compatible, vision models only, P(yes))")
