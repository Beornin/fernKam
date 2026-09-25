"""Double-check tag suggestions with a local vision-language model.

The image models behind Tag Review compare vectors; a vision-language model
actually looks at the photo and answers a question: "Does this photo show a
heron?" It is slower (about a second per photo on a GPU) and makes different
mistakes, which is what makes it a useful second opinion.

It talks to Ollama's API (default http://127.0.0.1:11434, any vision model you
have pulled: qwen3-vl:32b is the most accurate that fits a 24 GB GPU,
qwen3-vl:8b the quick one), or to any OpenAI-compatible
server when VISION_URL ends in /v1 (LM Studio, llama.cpp). The photo goes to
that URL as a 960 px JPEG, so keep it local.

Answers are stored in tag_checks, shown on each photo in Tag Review, and
compared with the user's own decisions ("agreed with you 45 of 50 times").
They are never used as labels: only the user's decisions train the models.
"""
from __future__ import annotations

import asyncio
import base64
import io
import logging
import math
from dataclasses import dataclass
from typing import Optional

import httpx
from sqlalchemy import text

logger = logging.getLogger(__name__)

_SYSTEM = ("You check whether photos match a tag. Look carefully, then answer with "
           "exactly one word: yes or no.")
# Names that are vision models when a server cannot say (older Ollama).
_VISION_HINTS = ("vl", "vision", "llava", "gemma3", "minicpm-v", "moondream", "pixtral",
                 "granite3.2-vision", "mistral-small3", "internvl", "smolvlm")
# Preferred when picking automatically, best first.
_PREFERRED = ("qwen3-vl", "qwen2.5vl", "gemma3", "mistral-small3", "minicpm-v", "llama3.2-vision", "llava")


@dataclass
class Verdict:
    verdict: Optional[int]      # 1 yes, 0 no, None unsure
    p_yes: Optional[float]      # from token probabilities when the server gives them


def _openai(url: str) -> bool:
    return url.rstrip("/").endswith("/v1")


async def _base_settings(db) -> tuple[str, str]:
    from fernkam.config import get_settings
    from fernkam.db.app_settings import get_setting
    s = get_settings()
    url = (await get_setting(db, "vision_url")) or s.vision_url
    model = (await get_setting(db, "vision_model")) or s.vision_model
    return url.rstrip("/"), model


async def list_models(url: str) -> list[dict]:
    """Vision-capable models on the server: [{name, size_gb}]. Raises
    httpx errors when the server is not reachable."""
    async with httpx.AsyncClient(timeout=3) as c:
        if _openai(url):
            r = await c.get(f"{url}/models")
            r.raise_for_status()
            return [{"name": m["id"], "size_gb": None} for m in r.json().get("data", [])]
        r = await c.get(f"{url}/api/tags")
        r.raise_for_status()
        out = []
        for m in r.json().get("models", []):
            name = m.get("name") or m.get("model")
            caps = None
            try:
                show = await c.post(f"{url}/api/show", json={"model": name})
                caps = show.json().get("capabilities") if show.status_code == 200 else None
            except httpx.HTTPError:
                pass
            vision = ("vision" in caps) if caps is not None else any(h in name.lower() for h in _VISION_HINTS)
            if vision:
                out.append({"name": name, "size_gb": round((m.get("size") or 0) / 1e9, 1),
                            "thinking": bool(caps and "thinking" in caps)})
        return out


def pick_model(models: list[dict], wanted: str) -> Optional[str]:
    """The user's choice if pulled; else the best family pulled, and within
    it the largest size (on a 24 GB GPU, qwen3-vl:32b over qwen3-vl:8b)."""
    names = [m["name"] for m in models]
    if wanted and wanted in names:
        return wanted
    for pref in _PREFERRED:
        family = [m for m in models if m["name"].lower().startswith(pref)]
        if family:
            return max(family, key=lambda m: m.get("size_gb") or 0)["name"]
    return names[0] if names else None


async def status(db) -> dict:
    url, wanted = await _base_settings(db)
    try:
        models = await list_models(url)
    except (httpx.HTTPError, ValueError) as exc:
        return {"url": url, "reachable": False, "error": str(exc)[:200], "models": [], "model": None}
    return {"url": url, "reachable": True, "models": models, "model": pick_model(models, wanted),
            "api": "openai" if _openai(url) else "ollama"}


def question(path: str) -> str:
    parts = path.replace("_", " ").split(".")
    leaf = parts[-1]
    if len(parts) > 1:
        return f'Does this photo show "{leaf}" (category: {" > ".join(parts[:-1])})? Answer yes or no.'
    return f'Does this photo show "{leaf}"? Answer yes or no.'


def _jpeg_b64(img) -> str:
    buf = io.BytesIO()
    img.convert("RGB").save(buf, "JPEG", quality=90)
    return base64.b64encode(buf.getvalue()).decode()


def _parse(content: str, top: Optional[list]) -> Verdict:
    """The answer word, and P(yes) from the first token's alternatives if given."""
    word = content.strip().strip('."\'*').lower()
    if "</think>" in word:
        word = word.split("</think>")[-1].strip()
    verdict = 1 if word.startswith("yes") else 0 if word.startswith("no") else None
    p_yes = None
    if top:
        yes = sum(math.exp(t["logprob"]) for t in top if t.get("token", "").strip().lower() == "yes")
        no = sum(math.exp(t["logprob"]) for t in top if t.get("token", "").strip().lower() == "no")
        if yes + no > 0:
            p_yes = yes / (yes + no)
    return Verdict(verdict, p_yes)


async def ask(client: httpx.AsyncClient, url: str, model: str, img, path: str,
              thinking: bool = False) -> Verdict:
    q, b64 = question(path), _jpeg_b64(img)
    if _openai(url):
        r = await client.post(f"{url}/chat/completions", json={
            "model": model, "temperature": 0, "max_tokens": 4, "logprobs": True, "top_logprobs": 5,
            "messages": [{"role": "system", "content": _SYSTEM},
                         {"role": "user", "content": [
                             {"type": "text", "text": q},
                             {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64}"}}]}]})
        r.raise_for_status()
        choice = r.json()["choices"][0]
        lp = (choice.get("logprobs") or {}).get("content") or []
        return _parse(choice["message"]["content"] or "", lp[0].get("top_logprobs") if lp else None)
    body = {"model": model, "stream": False, "logprobs": True, "top_logprobs": 5,
            "options": {"temperature": 0, "num_predict": 4},
            "messages": [{"role": "system", "content": _SYSTEM},
                         {"role": "user", "content": q, "images": [b64]}]}
    if thinking:
        body["think"] = False
    r = await client.post(f"{url}/api/chat", json=body)
    r.raise_for_status()
    data = r.json()
    lp = data.get("logprobs") or []
    return _parse(data.get("message", {}).get("content") or "", lp[0].get("top_logprobs") if lp else None)


def _load(pid: int, album: str, fname: str):
    from fernkam.thumbnails import generate_thumbnail_bytes, photo_disk_path, read_thumbnail_from_disk
    from PIL import Image
    data = read_thumbnail_from_disk(pid, "lg") or generate_thumbnail_bytes(photo_disk_path(album, fname), "lg")
    if not data:
        return None
    try:
        img = Image.open(io.BytesIO(data))
        img.load()
        return img
    except Exception:
        return None


async def check_photos(db, tag_id: int, photo_ids: list[int], on_progress=None, is_cancelled=None) -> dict:
    """Ask the vision model about each photo for one tag; store the answers."""
    st = await status(db)
    if not st["reachable"]:
        raise RuntimeError(f"No vision model server at {st['url']}: {st.get('error', '')}")
    if not st["model"]:
        raise RuntimeError("The server has no vision model. With Ollama: ollama pull qwen3-vl:8b")
    model = st["model"]
    thinking = any(m["name"] == model and m.get("thinking") for m in st["models"])
    tag = (await db.execute(text("SELECT path::text FROM tags WHERE id = :t"), {"t": tag_id})).scalar_one()
    rows = (await db.execute(text(
        "SELECT id, album_path, filename FROM photos WHERE id = ANY(:ids)"), {"ids": photo_ids})).all()
    loop = asyncio.get_running_loop()
    done = yes = no = failed = 0
    async with httpx.AsyncClient(timeout=120) as client:
        for r in rows:
            if is_cancelled and await is_cancelled():
                break
            img = await loop.run_in_executor(None, _load, r.id, r.album_path, r.filename)
            if img is None:
                failed += 1
                continue
            try:
                v = await ask(client, st["url"], model, img, tag, thinking)
            except (httpx.HTTPError, KeyError, ValueError) as exc:
                logger.warning("vision check of photo %s failed: %s", r.id, exc)
                failed += 1
                if failed >= 5 and done == 0:
                    raise RuntimeError(f"The vision model keeps failing: {exc}") from exc
                continue
            await db.execute(text("""
                INSERT INTO tag_checks (photo_id, tag_id, model, verdict, p_yes)
                VALUES (:p, :t, :m, :v, :py)
                ON CONFLICT (photo_id, tag_id) DO UPDATE SET model = EXCLUDED.model,
                    verdict = EXCLUDED.verdict, p_yes = EXCLUDED.p_yes, checked_at = now()
            """), {"p": r.id, "t": tag_id, "m": model, "v": v.verdict, "py": v.p_yes})
            await db.commit()
            done += 1
            yes += v.verdict == 1
            no += v.verdict == 0
            if on_progress:
                await on_progress(done, len(rows), yes, no)
    return {"checked": done, "yes": yes, "no": no, "failed": failed, "model": model}


async def agreement(db, tag_id: Optional[int] = None) -> dict:
    """How often the vision model's answer matched the user's decision, on
    photos the user has since approved or rejected."""
    where = "WHERE c.tag_id = :t" if tag_id is not None else ""
    row = (await db.execute(text(f"""
        SELECT count(*) FILTER (WHERE (c.verdict = 1 AND pt.verified_at IS NOT NULL)
                                   OR (c.verdict = 0 AND r.photo_id IS NOT NULL)) AS agreed,
               count(*) FILTER (WHERE c.verdict IS NOT NULL
                                  AND (pt.verified_at IS NOT NULL OR r.photo_id IS NOT NULL)) AS judged,
               count(*) AS checked
        FROM tag_checks c
        LEFT JOIN photo_tags pt ON pt.photo_id = c.photo_id AND pt.tag_id = c.tag_id
        LEFT JOIN tag_rejections r ON r.photo_id = c.photo_id AND r.tag_id = c.tag_id
        {where}
    """), {"t": tag_id})).one()
    return {"agreed": row.agreed, "judged": row.judged, "checked": row.checked,
            "rate": row.agreed / row.judged if row.judged else None}


async def photos_to_check(db, tag_id: int, state: str, limit: int, model: str) -> list[int]:
    """Photos of one tab not yet checked by this model: suggestions best first,
    unverified tags most doubtful first."""
    if state == "suggested":
        src = "tag_suggestions x", "x.tag_id = :t", "x.score DESC"
    else:
        src = "photo_tags x", "x.tag_id = :t AND x.verified_at IS NULL", "x.model_score ASC NULLS LAST"
    table, where, order = src
    return [r[0] for r in (await db.execute(text(f"""
        SELECT x.photo_id FROM {table} JOIN photos p ON p.id = x.photo_id
        WHERE {where} AND p.status = 1
          AND NOT EXISTS (SELECT 1 FROM tag_checks c WHERE c.photo_id = x.photo_id
                          AND c.tag_id = :t AND c.model = :m)
        ORDER BY {order}, x.photo_id LIMIT :lim
    """), {"t": tag_id, "m": model, "lim": limit})).all()]
