"""Reconciling a photo's catalogue metadata with its file.

Rating, colour label, title, caption and tags can change on both sides:

- in fernKam — the edit is saved to the database, flagged "needs sync", and
  written into the file on the next write-back;
- in another program (digiKam, Lightroom, ...) that edits the file directly.

`photos.synced_meta` records those fields as the file held them the last time
fernKam read or wrote it. With that common ancestor, each field merges on its
own:

    changed only in the file     -> the file's value is taken
    changed only in fernKam      -> fernKam's value is kept (written back later)
    changed on both sides        -> the file's value is taken — an edit made
                                    outside fernKam is honoured — and the
                                    replaced fernKam value is reported, so the
                                    user can put it back

Tags merge as sets: tags added or removed in the file are added to or removed
from the photo, and tags added or removed in fernKam are kept.

Without an ancestor (rows not read since this existed) a difference can't be
attributed. Pending in-app edits are then left alone, and otherwise the file
wins for the values it has — what scans always did. Tags are left alone until
there is an ancestor.

Face regions are not merged: fernKam's faces are always what gets written.
"""
from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from typing import Any, Optional

from sqlalchemy import text

logger = logging.getLogger(__name__)

SCALAR_FIELDS = ("rating", "color_label", "title", "caption")


def ltree_label(name: str) -> str:
    """Sanitize a string to a valid ltree label (letters, digits, underscores only)."""
    return re.sub(r"[^A-Za-z0-9]", "_", name.strip()) or "_"


def tag_key(parts: "list[str] | tuple[str, ...]") -> str:
    """Comparable key for a tag path: its ltree form ("People.Jane_Doe").

    The file holds human names ("People/Jane Doe"), the database holds ltree
    labels; both are reduced to the same key. ltree_label is idempotent, so a
    path written from the database reads back to the key it came from.
    """
    return ".".join(ltree_label(p) for p in parts)


def db_tag_key(ltree_path: str) -> str:
    return tag_key(str(ltree_path).split("."))


def _norm(name: str, value: Any) -> Any:
    if name in ("rating", "color_label"):
        try:
            return int(value) if value is not None else 0
        except (TypeError, ValueError):
            return 0
    if value is None:
        return None
    value = str(value)
    return value if value.strip() else None  # an empty caption is no caption


def file_tag_paths(metadata: dict) -> list[list[str]]:
    """The file's tags as lists of names.

    HierarchicalSubject when the file has one (digiKam, Lightroom and fernKam
    all write it), otherwise the flat Subject keywords as top-level tags.
    """
    hier = metadata.get("tag_paths") or []
    out: list[list[str]] = []
    seen: set[str] = set()
    if hier:
        candidates = [[p.strip() for p in str(h).split("/") if p.strip()] for h in hier]
    else:
        candidates = [[str(kw).strip()] for kw in (metadata.get("tags") or []) if str(kw).strip()]
    for parts in candidates:
        if parts and (k := tag_key(parts)) not in seen:
            seen.add(k)
            out.append(parts)
    return out


def file_state(metadata: dict) -> dict:
    """What synced_meta stores: the file's editable fields, normalised."""
    state = {f: _norm(f, metadata.get(f)) for f in SCALAR_FIELDS}
    state["tags"] = sorted({tag_key(p) for p in file_tag_paths(metadata)})
    return state


@dataclass
class Merge:
    updates: dict = field(default_factory=dict)           # scalar column -> file value to take
    add_tags: list = field(default_factory=list)          # name-paths to attach
    remove_tags: set = field(default_factory=set)         # tag keys to detach
    conflicts: dict = field(default_factory=dict)         # field -> {"fernkam": replaced, "file": taken}
    snapshot: dict = field(default_factory=dict)          # new synced_meta
    lost_all: bool = False                                # file suddenly has none of its metadata


def _has_any(state: dict) -> bool:
    return any(state.get(f) for f in SCALAR_FIELDS) or bool(state.get("tags"))


def merge(db_state: dict, metadata: dict, base: Optional[dict], dirty: bool) -> Merge:
    """Pure three-way merge. db_state holds the scalar fields plus "tags", a
    set of tag keys (see db_tag_key)."""
    fs = file_state(metadata)
    result = Merge(snapshot=fs)

    # Every editable field gone at once is far more often a damaged or
    # truncated file (exiftool happily reads a broken JPEG as "no tags") than
    # a deliberate strip. Change nothing and keep the old ancestor, so the
    # next write-back puts fernKam's values back into the file.
    if base is not None and _has_any(base) and not _has_any(fs):
        return Merge(snapshot=dict(base), lost_all=True)

    for name in SCALAR_FIELDS:
        db_v, file_v = _norm(name, db_state.get(name)), fs[name]
        if base is None:
            if not dirty and metadata.get(name) is not None and file_v != db_v:
                result.updates[name] = file_v
            continue
        base_v = _norm(name, base.get(name))
        file_changed, db_changed = file_v != base_v, db_v != base_v
        if file_changed and file_v != db_v:
            result.updates[name] = file_v
            if db_changed:
                result.conflicts[name] = {"fernkam": db_v, "file": file_v}

    if base is not None and "tags" in base:
        base_t, file_t = set(base["tags"]), set(fs["tags"])
        db_t = set(db_state.get("tags") or ())
        by_key = {tag_key(p): p for p in file_tag_paths(metadata)}
        result.add_tags = [by_key[k] for k in sorted((file_t - base_t) - db_t)]
        result.remove_tags = (base_t - file_t) & db_t
    return result


# ── Database side ────────────────────────────────────────────────────────────

async def load_states(db, photo_ids: "list[int]") -> "dict[int, dict]":
    """Current editable fields, dirty flag, synced_meta and tags ({key: tag_id})
    for each photo."""
    if not photo_ids:
        return {}
    states: dict[int, dict] = {}
    rows = await db.execute(text(
        "SELECT id, rating, color_label, title, caption, file_sync_dirty, synced_meta "
        "FROM photos WHERE id = ANY(:ids)"
    ), {"ids": list(photo_ids)})
    for r in rows:
        states[r.id] = {
            "rating": r.rating, "color_label": r.color_label, "title": r.title, "caption": r.caption,
            "dirty": bool(r.file_sync_dirty), "base": r.synced_meta, "tags": {},
        }
    tag_rows = await db.execute(text(
        "SELECT pt.photo_id, pt.tag_id, t.path::text AS path FROM photo_tags pt "
        "JOIN tags t ON t.id = pt.tag_id WHERE pt.photo_id = ANY(:ids)"
    ), {"ids": list(photo_ids)})
    for r in tag_rows:
        if r.photo_id in states:
            states[r.photo_id]["tags"][db_tag_key(r.path)] = r.tag_id
    return states


async def ensure_tag_path(db, parts: "list[str]", cache: "dict[str, Any]"):
    """Find or create the tag for a name-path like ["People", "Jane Doe"],
    creating missing parents. `cache` maps tag keys to Tag rows."""
    from sqlalchemy import select
    from fernkam.db.models.photos import Tag

    parent = None
    for i, part in enumerate(parts):
        key = tag_key(parts[: i + 1])
        tag = cache.get(key)
        if tag is None:
            tag = (await db.execute(select(Tag).where(Tag.path == key))).scalar_one_or_none()
            if tag is None:
                tag = Tag(
                    name=part, path=key, parent_id=parent.id if parent else None,
                    is_person=(parts[0].lower() == "people" and i == len(parts) - 1),
                )
                db.add(tag)
                await db.flush()
            cache[key] = tag
        parent = tag
    return parent


async def record_outside_change(db, photo_id: int, kind: str, details: dict) -> None:
    """Add an entry to the "Changed outside fernKam" list. Does not commit."""
    await db.execute(text(
        "INSERT INTO outside_changes (photo_id, kind, details) VALUES (:p, :k, CAST(:d AS jsonb))"
    ), {"p": photo_id, "k": kind, "d": json.dumps(details, default=str)})


def describe(m: Merge, state: dict) -> Optional[dict]:
    """What an outside edit changed, for the user — None if nothing did."""
    if not (m.updates or m.add_tags or m.remove_tags or m.lost_all):
        return None
    return {
        "fields": {f: {"from": _norm(f, state.get(f)), "to": v} for f, v in m.updates.items()},
        "tags_added": ["/".join(p) for p in m.add_tags],
        "tags_removed": sorted(k.replace(".", "/") for k in m.remove_tags),
        "conflicts": m.conflicts,
    }


async def apply(db, photo_id: int, state: dict, metadata: dict,
                tag_cache: "dict[str, Any]") -> Optional[Merge]:
    """Merge one file's metadata into its photo and record the new ancestor.

    Returns None — and changes nothing — when the metadata could not be read:
    an empty read is not a file with everything deleted. Does not commit.
    """
    if not metadata:
        return None
    m = merge({**state, "tags": set(state["tags"])}, metadata, state.get("base"), state.get("dirty", False))
    if m.lost_all:
        logger.warning("photo %d: its file no longer has any rating, label, caption or tags — "
                       "treated as damaged; fernKam's values kept and will be written back", photo_id)
    elif m.conflicts:
        logger.warning("photo %d: edited both outside fernKam and in fernKam — kept the file's %s",
                       photo_id, ", ".join(f"{k}={v['file']!r} (fernKam had {v['fernkam']!r})"
                                           for k, v in m.conflicts.items()))
    sets = ", ".join(f"{col} = :{col}" for col in m.updates)
    await db.execute(
        text(f"UPDATE photos SET {sets + ', ' if sets else ''}synced_meta = CAST(:snap AS jsonb) WHERE id = :id"),
        {**m.updates, "snap": json.dumps(m.snapshot), "id": photo_id},
    )
    for parts in m.add_tags:
        # Added in another program: unverified until approved on Tag Review.
        tag = await ensure_tag_path(db, parts, tag_cache)
        await db.execute(text(
            "INSERT INTO photo_tags (photo_id, tag_id) VALUES (:p, :t) ON CONFLICT DO NOTHING"
        ), {"p": photo_id, "t": tag.id})
        await db.execute(text(
            "DELETE FROM tag_suggestions WHERE photo_id = :p AND tag_id = :t"
        ), {"p": photo_id, "t": tag.id})
    if m.remove_tags:
        await db.execute(text(
            "DELETE FROM photo_tags WHERE photo_id = :p AND tag_id = ANY(:ids)"
        ), {"p": photo_id, "ids": [state["tags"][k] for k in m.remove_tags]})

    # Tell the user. Only with an ancestor: without one a difference can't be
    # attributed to another program (the first read after upgrading would
    # otherwise report every photo whose file disagrees with the catalogue).
    if state.get("base") is not None:
        if m.lost_all:
            await record_outside_change(db, photo_id, "damaged", {
                "note": "The file no longer has any rating, label, caption or tags. Treated as "
                        "damaged: fernKam kept its values and will write them back."})
        elif (details := describe(m, state)) is not None:
            await record_outside_change(db, photo_id, "metadata", details)
    return m
