"""RAW/JPG stack detection.

A "stack" groups one RAW file (living in an `<album>/RAW/` subfolder) with
every derivative (JPG/TIF/edited variant) in the parent album whose filename
stem starts with the RAW's stem — e.g. `_DSC9498.NEF` <-> `_DSC9498.jpg`, or
`_5_I5460.CR3` <-> `_5_I5460-DxO_DeepPRIME XD2s.jpg`.

Only groups containing >=1 RAW photo are considered stacks.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Optional

from sqlalchemy import select, text, update
from sqlalchemy.ext.asyncio import AsyncSession

from fernkam.db.models.photos import Photo, PhotoStack
from fernkam.media_types import is_raw

# Separator characters allowed right after the RAW stem in a derivative's
# filename, to avoid `_DSC95` false-matching `_DSC950x`.
_SEPARATORS = (".", "-", "_", " ")


def _stem(filename: str) -> str:
    if "." in filename:
        return filename.rsplit(".", 1)[0].lower()
    return filename.lower()


def _parent_album(raw_album_path: str) -> Optional[str]:
    """Return the parent album for a `.../RAW` album path, or None if it doesn't end in /RAW."""
    normalized = raw_album_path.rstrip("/")
    if normalized.endswith("/RAW"):
        parent = normalized[: -len("/RAW")]
        return parent if parent else "/"
    if normalized == "RAW":
        return "/"
    return None


def _derivative_matches(raw_stem: str, derivative_stem: str) -> bool:
    """True if derivative_stem is raw_stem itself, or raw_stem followed by a separator."""
    if derivative_stem == raw_stem:
        return True
    if derivative_stem.startswith(raw_stem):
        next_char = derivative_stem[len(raw_stem): len(raw_stem) + 1]
        return next_char in _SEPARATORS
    return False


def _pick_cover(raw_photo: Any, derivatives: list[Any]) -> Any:
    """Prefer the best derivative: highest rating, then largest file, else the RAW.

    Takes lightweight Row objects (id/filename/rating/file_size), not full
    Photo ORM entities — see the column-only select in detect_stacks().
    """
    if not derivatives:
        return raw_photo
    return max(
        derivatives,
        key=lambda p: (p.rating or 0, p.file_size or 0),
    )


async def detect_stacks(db: AsyncSession, album_path: Optional[str] = None) -> dict:
    """(Re)build stacks for the whole library, or restricted to one album subtree.

    Idempotent: safe to re-run; re-syncs membership, cover, and counts.
    Returns summary stats.
    """
    # Column-only select (not full Photo ORM entities): the matching pass below
    # only needs these 5 fields, and pulling every column for up to ~120k rows
    # into the ORM identity map was the dominant memory cost of a full rebuild.
    photo_q = select(
        Photo.id, Photo.filename, Photo.album_path, Photo.rating, Photo.file_size,
    ).where(Photo.status == 1)
    if album_path:
        clean_album_path = album_path.lstrip("/")
        photo_q = photo_q.where(Photo.album_path.like(f"{clean_album_path}%"))
    all_photos = (await db.execute(photo_q)).all()

    # Index photos by album_path for fast derivative lookup.
    by_album: dict[str, list[Any]] = {}
    for p in all_photos:
        by_album.setdefault(p.album_path, []).append(p)

    raw_photos = [p for p in all_photos if is_raw(p.filename)]

    stacks_created = 0
    stacks_updated = 0
    photos_grouped = 0
    now = datetime.now(timezone.utc)

    seen_stack_keys: set[tuple[str, str]] = set()
    # Collected and applied as one batched UPDATE at the end, instead of
    # mutating up to ~120k tracked ORM Photo objects (which is what made the
    # full-entity select above necessary in the first place).
    member_updates: list[dict] = []

    # Preload every existing stack in scope once — used both to avoid a
    # per-RAW-photo SELECT below and for the stale-stack cleanup further down
    # (previously fetched twice: once per RAW photo, once again at cleanup).
    all_stacks_q = select(PhotoStack)
    if album_path:
        all_stacks_q = all_stacks_q.where(PhotoStack.album_path.like(f"{clean_album_path}%"))
    all_stacks = (await db.execute(all_stacks_q)).scalars().all()
    stacks_by_key: dict[tuple[str, str], PhotoStack] = {
        (s.album_path, s.stem_key): s for s in all_stacks
    }

    for raw in raw_photos:
        parent_album = _parent_album(raw.album_path)
        if parent_album is None:
            continue
        siblings = by_album.get(parent_album, [])
        raw_stem = _stem(raw.filename)
        derivatives = [
            p for p in siblings
            if not is_raw(p.filename) and _derivative_matches(raw_stem, _stem(p.filename))
        ]
        if not derivatives:
            # RAW with no derivative — still forms a single-member stack so it's
            # discoverable and can later be tag-synced once a derivative appears.
            derivatives = []

        stack_key = (parent_album, raw_stem)
        seen_stack_keys.add(stack_key)

        cover = _pick_cover(raw, derivatives)
        members = [raw, *derivatives]

        existing = stacks_by_key.get((parent_album, raw_stem))

        if existing is None:
            existing = PhotoStack(
                album_path=parent_album,
                stem_key=raw_stem,
                cover_photo_id=cover.id,
                member_count=len(members),
                has_raw=True,
                created_at=now,
                updated_at=now,
            )
            db.add(existing)
            await db.flush()
            stacks_by_key[(parent_album, raw_stem)] = existing
            stacks_created += 1
        else:
            existing.cover_photo_id = cover.id
            existing.member_count = len(members)
            existing.has_raw = True
            existing.updated_at = now
            stacks_updated += 1

        for m in members:
            member_updates.append({
                "id": m.id,
                "stack_id": existing.id,
                "stack_role": "raw" if is_raw(m.filename) else "derivative",
            })
            photos_grouped += 1

    if member_updates:
        await db.execute(text(
            "UPDATE photos SET stack_id=:stack_id, stack_role=:stack_role WHERE id=:id"
        ), member_updates)

    # Clean up stacks that no longer have any matching RAW (e.g. RAW moved/deleted).
    # Reuses the `all_stacks` snapshot preloaded above — note newly-created
    # stacks from this run are also in `stacks_by_key`/seen_stack_keys, so they
    # won't be misidentified as stale even though they weren't in that snapshot.
    stale_ids = [s.id for s in all_stacks if (s.album_path, s.stem_key) not in seen_stack_keys]
    if stale_ids:
        await db.execute(update(Photo).where(Photo.stack_id.in_(stale_ids)).values(stack_id=None, stack_role=None))
        for s in all_stacks:
            if s.id in stale_ids:
                await db.delete(s)

    await db.commit()

    return {
        "raw_photos_scanned": len(raw_photos),
        "stacks_created": stacks_created,
        "stacks_updated": stacks_updated,
        "stacks_removed": len(stale_ids),
        "photos_grouped": photos_grouped,
    }
