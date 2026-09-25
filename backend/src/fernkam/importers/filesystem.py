"""Filesystem scanner to import photos/videos from library root."""
from __future__ import annotations

import hashlib
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import asyncio
import logging
import time

from sqlalchemy import select, tuple_, update
from sqlalchemy.ext.asyncio import AsyncSession

from fernkam.config import get_settings
from fernkam.db.models.photos import Face, Photo, PhotoTag, Tag
from fernkam.media_types import ALL_EXTENSIONS, IMAGE_EXTENSIONS, VIDEO_EXTENSIONS, media_type_for  # noqa: F401 (re-exported for existing importers)
from fernkam.metadata_sync import read_file_metadata_async, read_many_metadata_async
from fernkam.sync_merge import Merge, ensure_tag_path, file_state, file_tag_paths, load_states, ltree_label
from fernkam.sync_merge import apply as apply_merge
from fernkam.sync_merge import record_outside_change

log = logging.getLogger(__name__)

# Parallelism tuning — auto-scaled to CPU count, overridable by env vars.
_CPUS = os.cpu_count() or 4
METADATA_CONCURRENCY = int(os.getenv("FERNKAM_META_CONCURRENCY", str(min(32, _CPUS * 2))))
THUMB_CONCURRENCY = int(os.getenv("FERNKAM_THUMB_CONCURRENCY", str(max(4, _CPUS))))
BATCH_COMMIT_SIZE = 50      # photos committed per transaction
# Tolerance when comparing a file's on-disk mtime against the mtime recorded at
# its last sync. Filesystem timestamp granularity varies (NTFS 100ns, FAT 2s,
# and network shares round differently), so anything inside this window counts
# as unchanged rather than triggering a needless metadata re-read.
MTIME_SLACK_SECONDS = 2.0


class ScanRootError(ValueError):
    """A folder to scan that is not inside LIBRARY_ROOT."""


def resolve_scan_root(custom_path: Optional[str]) -> Path:
    """The folder a scan should walk: LIBRARY_ROOT, or a folder inside it.

    fernKam catalogues photos by their path relative to LIBRARY_ROOT. A folder
    outside it used to be walked anyway, with album paths computed relative
    to *that* folder — rows that pointed at files which do not exist, and
    that the next full scan deleted again (with any tags added meanwhile).

    Accepts an absolute path or one relative to the library, with either
    slash. Returns it expressed under LIBRARY_ROOT as configured, so album
    paths computed from it match the rest of the catalogue.
    """
    main = Path(get_settings().library_root)
    if not custom_path or not custom_path.strip():
        return main
    candidate = Path(custom_path.strip().replace("\\", "/"))
    if not candidate.is_absolute():
        candidate = main / candidate
    try:
        # resolve() normalises "..", symlinks and (on Windows) drive/folder case.
        rel = candidate.resolve().relative_to(main.resolve())
    except ValueError:
        raise ScanRootError(
            f"{custom_path} is outside the library ({main}). fernKam only catalogues "
            f"photos under LIBRARY_ROOT — move or copy them into it, then scan that folder."
        ) from None
    return main / rel


class ScanCancelled(Exception):
    """Raised from a progress callback to stop a scan between batches.

    Everything committed so far stays; the removed-files phase never runs, so
    a cancelled scan cannot delete rows based on a partial walk."""


def _sha256_path(path: Path) -> Optional[str]:
    """Compute SHA-256 of a file; return None on error."""
    try:
        h = hashlib.sha256()
        with path.open("rb") as fh:
            for chunk in iter(lambda: fh.read(65536), b""):
                h.update(chunk)
        return h.hexdigest()
    except Exception:
        return None


async def _read_meta_safe(sem: asyncio.Semaphore, file_path: Path) -> dict:
    """Read file metadata with bounded concurrency; never raises."""
    async with sem:
        try:
            return await read_file_metadata_async(file_path)
        except Exception as exc:
            log.warning("metadata read error %s: %s", file_path, exc)
            return {}


async def _gen_thumbs_for_photo(
    sem: asyncio.Semaphore,
    file_path: Path,
    photo_id: int,
    media_type: str,
) -> tuple[int, dict[str, bytes]]:
    """Generate all thumbnail sizes for one photo using a thread executor."""
    from fernkam.thumbnails import generate_thumbnail_bytes, probe_video_duration
    if media_type == "video":
        loop = asyncio.get_event_loop()
        async with sem:
            # Only generate the "md" poster for the grid; larger sizes on demand
            data = await loop.run_in_executor(None, generate_thumbnail_bytes, file_path, "md")
        return photo_id, {"md": data} if data else {}
    if media_type != "image":
        return photo_id, {}
    loop = asyncio.get_event_loop()
    async with sem:
        results = await asyncio.gather(*[
            loop.run_in_executor(None, generate_thumbnail_bytes, file_path, size)
            for size in ("sm", "md", "lg", "xl")
        ], return_exceptions=True)
    size_bytes = {
        sz: data
        for sz, data in zip(("sm", "md", "lg", "xl"), results)
        if isinstance(data, bytes) and data
    }
    return photo_id, size_bytes


def _stat_walk_batch(
    root: str,
    files: list[str],
    main_library: Path,
    library_root: Path,
    existing_photos: dict[tuple[str, str], tuple[int, Optional[datetime]]],
) -> tuple[list, list, set, int]:
    """Blocking: stat() every candidate file in one directory. Call via run_in_executor.

    Returns (new_files, existing_to_update, disk_keys, unchanged) for just this
    directory, in the same tuple shapes scan_library() accumulates across the
    whole walk.

    Files whose on-disk mtime still matches the mtime recorded at their last
    sync are reported as `unchanged` and dropped here, so the caller never pays
    for an exiftool read on them. Previously every known file was queued for a
    refresh unconditionally, which made a no-op rescan of this library re-read
    all ~122k files and take over two hours. Forcing a full re-read is still
    available separately via /sync/metadata (refresh_metadata_from_files).
    """
    new_files: list[tuple[Path, str, str, datetime]] = []
    existing_to_update: list[tuple[Path, str, str, datetime, int]] = []
    disk_keys: set[tuple[str, str]] = set()
    unchanged = 0

    for filename in files:
        ext = Path(filename).suffix.lower()
        if ext not in ALL_EXTENSIONS:
            continue
        full_path = Path(root) / filename
        try:
            rel_path = full_path.relative_to(main_library)
        except ValueError:
            # resolve_scan_root() keeps every walk inside the library, so this
            # would be a bug — and a row with a path relative to anything else
            # points at a file that does not exist. Skip it rather than import.
            log.warning("[SCAN] skipping %s: not under the library root %s", full_path, main_library)
            continue
        album_path = str(rel_path.parent).replace("\\", "/") if rel_path.parent != Path(".") else "/"
        key = (album_path, filename)
        disk_keys.add(key)
        try:
            mtime = datetime.fromtimestamp(full_path.stat().st_mtime, tz=timezone.utc)
        except OSError:
            mtime = datetime.now(timezone.utc)

        existing = existing_photos.get(key)
        if existing is None:
            new_files.append((full_path, album_path, filename, mtime))
            continue

        photo_id, synced_at = existing
        # Compared with slack in both directions: filesystem timestamp
        # granularity (and restores that rewind an mtime) must both count as
        # "changed" only when the difference is real, not sub-second noise.
        if synced_at is not None and abs((mtime - synced_at).total_seconds()) <= MTIME_SLACK_SECONDS:
            unchanged += 1
            continue
        existing_to_update.append((full_path, album_path, filename, mtime, photo_id))

    return new_files, existing_to_update, disk_keys, unchanged


def _select_removed(
    existing_photos: dict[tuple[str, str], tuple[int, Optional[datetime]]],
    disk_keys: set[tuple[str, str]],
    main_library: Path,
    scope_root: Path,
    unreadable_dirs: list[Path],
) -> tuple[list[tuple[int, str, str]], Optional[str]]:
    """Decide which catalogue rows the scan should delete as "file removed".

    Returns (rows as (id, album_path, filename), reason_if_deletion_skipped).
    Deleting a row cascades to its tags, faces and rating, so this errs hard
    on the side of keeping rows:

    - Folders os.walk could not list (permissions, a flaky network share or USB
      drive) are skipped silently by os.walk. Their photos were not *seen*,
      which is not the same as gone, so rows under them are kept.
    - A walk that found no media at all while the catalogue has rows in scope
      means the library is unmounted or LIBRARY_ROOT points at an empty
      folder; nothing is deleted.
    """
    def album_dir(album_path: str) -> Path:
        rel = album_path.strip("/")
        return main_library / rel if rel else main_library

    in_scope = []
    for key, (photo_id, _synced) in existing_photos.items():
        d = album_dir(key[0])
        if scope_root != main_library and not d.is_relative_to(scope_root):
            continue
        in_scope.append((key, photo_id, d))

    if in_scope and not disk_keys:
        return [], (f"found no media files under {scope_root}, but the catalogue has "
                    f"{len(in_scope):,} photos there — is the drive connected?")

    removed = []
    for key, photo_id, d in in_scope:
        if key in disk_keys:
            continue
        if any(d == bad or d.is_relative_to(bad) for bad in unreadable_dirs):
            continue
        removed.append((photo_id, key[0], key[1]))
    return removed, None


async def scan_library(
    db: AsyncSession,
    custom_path: Optional[str] = None,
    progress_callback: Optional[callable] = None,
    photo_added_callback: Optional[callable] = None,
) -> dict:
    """Scan library_root (or custom_path) for new/updated photos and import them.

    - New file on disk  → import with full metadata (parallel metadata reads, batch commits)
    - Existing file     → skip (do nothing)
    - File removed      → delete from DB
    - photo_added_callback(photo_id) called per batch, immediately after batch commit

    Returns dict with stats: added, skipped, deleted, errors, total, added_ids
    """
    from sqlalchemy import delete as sa_delete
    from fernkam.thumbnails import write_thumbnail_to_disk
    settings = get_settings()
    main_library = Path(settings.library_root)
    try:
        library_root = resolve_scan_root(custom_path)
    except ScanRootError as exc:
        return {"error": str(exc)}

    if not library_root.exists():
        return {"error": f"Library root does not exist: {library_root}"}

    stats = {"added": 0, "skipped": 0, "updated": 0, "deleted": 0, "errors": 0, "total": 0}

    # ── Phase 0: load existing photos ──────────────────────────────────────
    existing_photos: dict[tuple[str, str], tuple[int, Optional[datetime]]] = {}
    result = await db.execute(
        select(Photo.id, Photo.album_path, Photo.filename, Photo.file_modified_at_sync)
    )
    for row in result:
        existing_photos[(row.album_path, row.filename)] = (row.id, row.file_modified_at_sync)

    # Always walk from the root so new directories are discovered.
    # Already-imported files are skipped in O(1) via the existing_photos dict,
    # so this is cheap even for large libraries.
    scan_dirs = [library_root]

    # ── Phase 1: walk filesystem, collect new + existing files ───────────────
    new_files: list[tuple[Path, str, str, datetime]] = []  # (path, album_path, filename, mtime)
    existing_to_update: list[tuple[Path, str, str, datetime, int]] = []  # + photo_id
    disk_keys: set[tuple[str, str]] = set()

    walk_scanned = 0
    walk_last_report = time.monotonic()
    loop = asyncio.get_event_loop()

    unreadable_dirs: list[Path] = []

    def _walk_error(err: OSError) -> None:
        log.warning("[SCAN] cannot read %s: %s — its photos will not be removed", err.filename, err)
        if err.filename:
            unreadable_dirs.append(Path(err.filename))

    for scan_dir in scan_dirs:
        for root, dirs, files in os.walk(scan_dir, onerror=_walk_error):
            dirs[:] = [d for d in dirs if not d.startswith(".")]

            # The per-file stat() calls (up to 120k on a full library) are the
            # slow part on large/HDD libraries — run each directory's batch in
            # a thread so a cold-cache walk doesn't stall every other request
            # for its whole duration. os.walk() itself (just listing directory
            # entries) stays on the main thread; that part is comparatively cheap.
            new_batch, existing_batch, keys_batch, unchanged_batch = await loop.run_in_executor(
                None, _stat_walk_batch, root, files, main_library, library_root, existing_photos
            )
            new_files.extend(new_batch)
            existing_to_update.extend(existing_batch)
            disk_keys.update(keys_batch)
            stats["skipped"] += unchanged_batch
            walk_scanned += len(new_batch) + len(existing_batch) + unchanged_batch

            # Throttled progress report — the disk walk (esp. the per-file stat()
            # calls on existing photos) is the slow part on large/HDD libraries,
            # and previously reported nothing at all for its whole duration.
            if progress_callback and (time.monotonic() - walk_last_report) > 0.75:
                walk_last_report = time.monotonic()
                try:
                    current_dir = str(Path(root).relative_to(main_library))
                except ValueError:
                    current_dir = root
                await progress_callback({**stats, "phase": "scanning", "scanned": walk_scanned, "current_dir": current_dir})

    # Which catalogued files are gone — decided now, before importing, so a
    # file that merely moved can be matched to its old row (below).
    removed, skip_reason = _select_removed(
        existing_photos, disk_keys, main_library, library_root, unreadable_dirs)
    if skip_reason:
        log.warning("[SCAN] not removing any photos: %s", skip_reason)
        stats["deletion_skipped"] = skip_reason
    if unreadable_dirs:
        stats["unreadable_dirs"] = len(unreadable_dirs)

    # ── Phase 1b: moved / renamed files keep their row ─────────────────────
    thumb_sem = asyncio.Semaphore(THUMB_CONCURRENCY)
    moves, removed, new_files, known_hashes = await _match_moves(db, removed, new_files, loop, thumb_sem)
    for photo_id, (_rid, old_album, old_name), (_path, album, fname, mtime) in moves:
        res = await db.execute(
            update(Photo)
            .where(Photo.id == photo_id, Photo.album_path == old_album, Photo.filename == old_name)
            .values(album_path=album, filename=fname, modified_at=mtime, file_modified_at_sync=mtime)
        )
        if res.rowcount:
            stats["moved"] = stats.get("moved", 0) + 1
            await record_outside_change(db, photo_id, "moved", {
                "from": _display_path(old_album, old_name), "to": _display_path(album, fname)})
    if moves:
        await db.commit()

    stats["total"] = len(existing_to_update) + len(new_files)
    log.info("[SCAN] %d new files to import, %d existing to refresh", len(new_files), len(existing_to_update))

    # ── Phase 2: import in batches with concurrent metadata reads ──────────
    meta_sem = asyncio.Semaphore(METADATA_CONCURRENCY)
    added_ids: list[int] = []

    for batch_start in range(0, len(new_files), BATCH_COMMIT_SIZE):
        batch = new_files[batch_start: batch_start + BATCH_COMMIT_SIZE]

        # Read metadata for ALL files in batch via one persistent exiftool
        # process (~13x faster than a process per file).
        batch_paths = [fp for fp, *_ in batch]
        meta_map = await read_many_metadata_async(batch_paths, chunk_size=BATCH_COMMIT_SIZE)
        metadatas: list[dict] = [meta_map.get(fp, {}) for fp in batch_paths]

        # Insert photos to DB (sequential within batch, savepoint per photo)
        batch_photos: list[tuple[int, Path, str]] = []  # (photo_id, path, media_type)
        for (full_path, album_path, filename, file_mtime), metadata in zip(batch, metadatas):
            try:
                async with db.begin_nested():  # savepoint — isolates per-photo failures
                    photo = await import_new_photo(
                        db, full_path, album_path, filename, file_mtime,
                        metadata=metadata, generate_thumbs=False,
                        sha256=known_hashes.get(full_path),
                    )
                batch_photos.append((photo.id, full_path, photo.media_type))
                added_ids.append(photo.id)
                stats["added"] += 1
            except Exception as exc:
                log.warning("import error %s: %s", full_path, exc)
                stats["errors"] += 1

        # Generate thumbnails for all photos in batch concurrently
        if batch_photos:
            thumb_results = await asyncio.gather(*[
                _gen_thumbs_for_photo(thumb_sem, fp, pid, mt)
                for pid, fp, mt in batch_photos
            ], return_exceptions=True)
            for res in thumb_results:
                if isinstance(res, Exception):
                    continue
                photo_id, size_bytes = res
                for size, data in size_bytes.items():
                    try:
                        await loop.run_in_executor(
                            None, write_thumbnail_to_disk, photo_id, size, data
                        )
                    except Exception as thumb_exc:
                        # Silently dropping this left the batch reporting
                        # "0 errors" even though the photo ends up with no
                        # thumbnail for this size.
                        log.warning("thumbnail store failed for photo %d (%s): %s", photo_id, size, thumb_exc)

        await db.commit()
        log.info("[SCAN] batch %d-%d committed (%d added, %d errors)",
                 batch_start, batch_start + len(batch), stats["added"], stats["errors"])

        # Fire callback per photo immediately so callers can pipeline face
        # detection while the next import batch is in flight.
        if photo_added_callback and batch_photos:
            for photo_id, _, _ in batch_photos:
                try:
                    await photo_added_callback(photo_id)
                except Exception as cbe:
                    log.warning("photo_added_callback error for %d: %s", photo_id, cbe)

        if progress_callback:
            await progress_callback(stats)

    # ── Phase 2b: refresh metadata for existing files ─────────────────────
    from fernkam.thumbnails import refresh_thumbnail
    tag_cache: dict = {}
    pixel_changed_ids: list[int] = []
    for batch_start in range(0, len(existing_to_update), BATCH_COMMIT_SIZE):
        batch = existing_to_update[batch_start: batch_start + BATCH_COMMIT_SIZE]

        # One persistent-exiftool batch read for the whole batch.
        batch_paths = [fp for fp, *_ in batch]

        async def _hash(path: Path) -> Optional[str]:
            async with thumb_sem:
                return await loop.run_in_executor(None, _sha256_path, path)

        meta_map, hashes = await asyncio.gather(
            read_many_metadata_async(batch_paths, chunk_size=BATCH_COMMIT_SIZE),
            asyncio.gather(*[_hash(fp) for fp in batch_paths]),
        )
        hash_of = dict(zip(batch_paths, hashes))
        states = await load_states(db, [pid for *_, pid in batch])

        for full_path, album_path, filename, file_mtime, photo_id in batch:
            try:
                async with db.begin_nested():
                    merged = await update_photo_metadata(
                        db, photo_id, full_path, file_mtime,
                        metadata=meta_map.get(full_path, {}),
                        state=states.get(photo_id), tag_cache=tag_cache,
                        sha256=hash_of.get(full_path),
                    )
                if merged is None:
                    stats["errors"] += 1  # unreadable; retried on the next scan
                    continue
                stats["updated"] += 1
                if merged.conflicts:
                    stats["conflicts"] = stats.get("conflicts", 0) + 1
            except Exception as exc:
                tag_cache.clear()  # may hold tags created inside the rolled-back savepoint
                log.warning("metadata refresh error %s: %s", full_path, exc)
                stats["errors"] += 1

        await db.commit()

        # Did the picture itself change (re-exported, cropped, retouched in
        # another program)? The rebuilt thumbnail says. When it did, faces and
        # the search embedding were computed from pixels that no longer exist.
        async def _refresh(photo_id: int, path: Path) -> str:
            async with thumb_sem:
                return await loop.run_in_executor(None, refresh_thumbnail, photo_id, path)

        outcomes = await asyncio.gather(
            *[_refresh(pid, fp) for fp, _a, _f, _m, pid in batch], return_exceptions=True)
        changed = [(pid, fp) for (fp, _a, _f, _m, pid), o in zip(batch, outcomes) if o == "changed"]
        if changed:
            ids = [pid for pid, _ in changed]
            await _forget_pixel_derived_data(db, ids)
            await db.commit()
            stats["pixels_changed"] = stats.get("pixels_changed", 0) + len(ids)
            pixel_changed_ids.extend(ids)
            if photo_added_callback:
                for pid, fp in changed:
                    if media_type_for(fp) == "image":
                        try:
                            await photo_added_callback(pid)  # re-detect faces
                        except Exception as cbe:
                            log.warning("photo_added_callback error for %d: %s", pid, cbe)

        if progress_callback:
            await progress_callback(stats)

    # ── Phase 3: delete removed files ─────────────────────────────────────
    gone_ids: list[int] = []
    # Match on the path as well as the id: a row moved during the scan (e.g.
    # promote-to-portfolio) keeps its id but not its old path, and must not be
    # deleted just because its old path is now empty. Chunked because asyncpg
    # caps a statement at 32,767 bind parameters.
    for i in range(0, len(removed), 5000):
        chunk = removed[i:i + 5000]
        res = await db.execute(
            sa_delete(Photo)
            .where(tuple_(Photo.id, Photo.album_path, Photo.filename).in_(chunk))
            .returning(Photo.id)
        )
        gone_ids.extend(r[0] for r in res)
    stats["deleted"] = len(gone_ids)

    await db.commit()

    # Thumbnails live on disk, so the row delete above no longer cascades to
    # them the way the old `photo_thumbnails` FK did — drop them explicitly.
    if gone_ids:
        from fernkam.thumbnails import delete_thumbnails_from_disk
        await loop.run_in_executor(None, delete_thumbnails_from_disk, gone_ids)

    stats["added_ids"] = added_ids
    stats["pixel_changed_ids"] = pixel_changed_ids
    if progress_callback:
        await progress_callback(stats)
    return stats


def _display_path(album_path: str, filename: str) -> str:
    album = album_path.strip("/")
    return f"{album}/{filename}" if album else filename


async def _match_moves(db: AsyncSession, removed: list, new_files: list, loop, sem):
    """Pair catalogue rows whose file vanished with new files of identical
    content (SHA-256), as digiKam does — a file moved or renamed outside
    fernKam keeps its row, and with it its tags, faces, rating, thumbnails and
    search embedding, instead of being deleted and re-imported bare.

    Returns (moves, still_removed, still_new, {path: sha256}). Every new file
    is hashed at import anyway, so hashing them here first costs nothing: the
    import reuses these hashes. (A size pre-filter was tried and dropped —
    file_size goes stale when metadata is written into a file.)
    """
    if not removed or not new_files:
        return [], removed, new_files, {}
    rows = (await db.execute(
        select(Photo.id, Photo.sha256)
        .where(Photo.id.in_([r[0] for r in removed]), Photo.sha256.is_not(None))
    )).all()
    if not rows:
        return [], removed, new_files, {}
    by_hash: dict[str, list[int]] = {}
    for r in rows:
        by_hash.setdefault(r.sha256, []).append(r.id)

    async def _hash(path: Path) -> Optional[str]:
        async with sem:
            return await loop.run_in_executor(None, _sha256_path, path)

    hashes = await asyncio.gather(*[_hash(nf[0]) for nf in new_files])
    removed_by_id = {r[0]: r for r in removed}
    moves, matched = [], set()
    for i, (nf, digest) in enumerate(zip(new_files, hashes)):
        candidates = by_hash.get(digest) if digest else None
        while candidates:
            photo_id = candidates.pop(0)
            if photo_id in removed_by_id:
                moves.append((photo_id, removed_by_id.pop(photo_id), nf))
                matched.add(i)
                break
    known = {nf[0]: h for nf, h in zip(new_files, hashes) if h}
    still_new = [nf for i, nf in enumerate(new_files) if i not in matched]
    return moves, list(removed_by_id.values()), still_new, known


async def _forget_pixel_derived_data(db: AsyncSession, photo_ids: list[int]) -> None:
    """The image changed: drop what was computed from its old pixels.

    The CLIP embedding is cleared (re-embedded by the scan or the Discover
    indexer). Faces nobody has reviewed (unconfirmed/suggested) are deleted and
    the photo is re-queued for detection; confirmed and ignored faces are
    human decisions and stay, with their crops cleared so they are re-cut from
    the new pixels. Recorded in "Changed outside fernKam". Does not commit.
    """
    from sqlalchemy import text as _text
    from fernkam.sync_merge import record_outside_change
    await db.execute(_text(
        "UPDATE photos SET embedding_v = NULL, embedded_at = NULL, faces_scanned_at = NULL "
        "WHERE id = ANY(:ids)"), {"ids": photo_ids})
    await db.execute(_text(
        "DELETE FROM faces WHERE photo_id = ANY(:ids) AND status IN ('unconfirmed', 'suggested')"),
        {"ids": photo_ids})
    await db.execute(_text("UPDATE faces SET crop_data = NULL WHERE photo_id = ANY(:ids)"), {"ids": photo_ids})
    for pid in photo_ids:
        await record_outside_change(db, pid, "pixels", {})


# Process-level cache: camera/lens tables stay tiny (~20 rows) but scans issue
# a lookup per photo, so caching ids by (make, model[, serial]) turns tens of
# thousands of redundant SELECTs into (at most) one per distinct camera/lens.
_camera_id_cache: dict[tuple, int] = {}
_lens_id_cache: dict[tuple, int] = {}


async def _get_or_create_camera(db: AsyncSession, camera_info: dict) -> int:
    """Find or create a Camera record using a savepoint to isolate failures. Returns its id."""
    from fernkam.db.models.photos import Camera
    make = camera_info.get("make")
    model = camera_info.get("model")
    serial = camera_info.get("serial")
    key = (make, model, serial)
    cached = _camera_id_cache.get(key)
    if cached is not None:
        return cached
    # Try read first (no savepoint needed)
    existing = (await db.execute(
        select(Camera.id).where(Camera.make == make, Camera.model == model, Camera.serial == serial)
    )).scalar_one_or_none()
    if existing is not None:
        _camera_id_cache[key] = existing
        return existing
    # Use savepoint so a constraint violation doesn't kill the parent transaction
    async with db.begin_nested():
        cam = Camera(make=make, model=model, serial=serial)
        db.add(cam)
        await db.flush()
    # Re-fetch in case of concurrent insert
    cam_id = (await db.execute(
        select(Camera.id).where(Camera.make == make, Camera.model == model, Camera.serial == serial)
    )).scalar_one()
    _camera_id_cache[key] = cam_id
    return cam_id


async def _get_or_create_lens(db: AsyncSession, lens_info: dict) -> int:
    """Find or create a Lens record using a savepoint to isolate failures. Returns its id."""
    from fernkam.db.models.photos import Lens
    make = lens_info.get("make")
    model = lens_info.get("model")
    key = (make, model)
    cached = _lens_id_cache.get(key)
    if cached is not None:
        return cached
    existing = (await db.execute(
        select(Lens.id).where(Lens.make == make, Lens.model == model)
    )).scalar_one_or_none()
    if existing is not None:
        _lens_id_cache[key] = existing
        return existing
    async with db.begin_nested():
        lens = Lens(make=make, model=model)
        db.add(lens)
        await db.flush()
    lens_id = (await db.execute(
        select(Lens.id).where(Lens.make == make, Lens.model == model)
    )).scalar_one()
    _lens_id_cache[key] = lens_id
    return lens_id


_ltree_label = ltree_label


async def _get_or_create_person_tag(db: AsyncSession, name: str) -> int:
    """Return the id of a person Tag with the given name, creating it (under People) if needed."""
    # Try exact name match with is_person=True first
    tag = (await db.execute(
        select(Tag).where(Tag.name == name, Tag.is_person == True)
    )).scalar_one_or_none()
    if tag:
        return tag.id

    # Fallback: any tag with this name
    tag = (await db.execute(
        select(Tag).where(Tag.name == name)
    )).scalar_one_or_none()
    if tag:
        if not tag.is_person:
            tag.is_person = True
        return tag.id

    # Create "People" root tag if missing
    people = (await db.execute(
        select(Tag).where(Tag.name == "People", Tag.parent_id == None)
    )).scalar_one_or_none()
    if people is None:
        people = Tag(name="People", path="People", is_person=False)
        db.add(people)
        await db.flush()

    # Create person tag under People
    ltree_path = f"People.{_ltree_label(name)}"
    tag = Tag(name=name, path=ltree_path, parent_id=people.id, is_person=True)
    db.add(tag)
    await db.flush()
    return tag.id


async def import_new_photo(
    db: AsyncSession,
    file_path: Path,
    album_path: str,
    filename: str,
    file_mtime: datetime,
    metadata: Optional[dict] = None,
    generate_thumbs: bool = True,
    sha256: Optional[str] = None,
) -> Photo:
    """Import a new photo into the database.

    Pass pre-read ``metadata`` (from a concurrent gather) to skip re-reading.
    Set ``generate_thumbs=False`` when the caller handles thumbnails externally.
    """
    if metadata is None:
        metadata = await read_file_metadata_async(file_path)
    
    # Determine media type
    media_type = media_type_for(file_path)

    # Resolve camera/lens records
    camera_id = None
    if metadata.get("camera"):
        try:
            camera_id = await _get_or_create_camera(db, metadata["camera"])
        except Exception as cam_exc:
            log.warning("camera lookup/create failed for %s: %s", file_path, cam_exc)

    lens_id = None
    if metadata.get("lens"):
        try:
            lens_id = await _get_or_create_lens(db, metadata["lens"])
        except Exception as lens_exc:
            log.warning("lens lookup/create failed for %s: %s", file_path, lens_exc)

    # Compute sha256 in a thread so we don't block the event loop (unless the
    # move detection already hashed this file)
    if sha256 is None:
        sha256 = await asyncio.get_event_loop().run_in_executor(None, _sha256_path, file_path)

    # Probe video duration
    duration_secs: Optional[float] = None
    if media_type == "video":
        from fernkam.thumbnails import probe_video_duration
        duration_secs = await asyncio.get_event_loop().run_in_executor(
            None, probe_video_duration, file_path
        )

    # Create photo record
    photo = Photo(
        album_path=album_path,
        filename=filename,
        media_type=media_type,
        modified_at=file_mtime,
        file_modified_at_sync=file_mtime,
        sha256=sha256,
        width=metadata.get("width"),
        height=metadata.get("height"),
        file_size=metadata.get("file_size"),
        taken_at=metadata.get("taken_at"),
        imported_at=datetime.now(timezone.utc),
        camera_id=camera_id,
        lens_id=lens_id,
        rating=metadata.get("rating", 0),
        color_label=metadata.get("color_label"),
        title=metadata.get("title"),
        caption=metadata.get("caption"),
        latitude=metadata.get("latitude"),
        longitude=metadata.get("longitude"),
        altitude=metadata.get("altitude"),
        orientation=metadata.get("orientation"),
        exif=metadata.get("exif"),
        duration_secs=duration_secs,
        status=1,
    )
    
    db.add(photo)
    await db.flush()

    # Generate thumbnails into the disk cache (images + video poster)
    if generate_thumbs and media_type in ("image", "video"):
        try:
            from fernkam.thumbnails import generate_thumbnail_bytes, write_thumbnail_to_disk
            for size in ("sm", "md", "lg", "xl"):
                data = generate_thumbnail_bytes(file_path, size)
                if data:
                    write_thumbnail_to_disk(photo.id, size, data)
        except Exception:
            pass  # Thumbnail generation failure is non-fatal

    # Restore tags from the file (HierarchicalSubject "People|Jane", or flat
    # keywords), and remember what the file held as the merge ancestor.
    tag_cache: dict[str, Tag] = {}
    linked: set[int] = set()
    for parts in file_tag_paths(metadata):
        leaf = await ensure_tag_path(db, parts, tag_cache)
        if leaf.id not in linked:
            linked.add(leaf.id)
            db.add(PhotoTag(photo_id=photo.id, tag_id=leaf.id))
    if metadata:
        photo.synced_meta = file_state(metadata)

    # Restore face regions from XMP if present
    xmp_faces = metadata.get("faces") or []
    img_w = metadata.get("width") or metadata.get("img_w")
    img_h = metadata.get("height") or metadata.get("img_h")
    if xmp_faces and img_w and img_h:
        for f in xmp_faces:
            cx, cy = f.get("cx", 0), f.get("cy", 0)
            nw, nh = f.get("nw", 0), f.get("nh", 0)
            if not (nw and nh):
                continue
            px = int((cx - nw / 2) * img_w)
            py = int((cy - nh / 2) * img_h)
            pw = int(nw * img_w)
            ph = int(nh * img_h)
            name = (f.get("name") or "").strip()
            person_tag_id = None
            if name:
                person_tag_id = await _get_or_create_person_tag(db, name)
            face = Face(
                photo_id=photo.id,
                x=px, y=py, w=pw, h=ph,
                region_name=name or None,
                region_type=f.get("type", "Face"),
                status="confirmed" if name else "unknown",
                person_tag_id=person_tag_id,
            )
            db.add(face)

    return photo


async def update_photo_metadata(
    db: AsyncSession,
    photo_id: int,
    file_path: Path,
    file_mtime: datetime,
    metadata: Optional[dict] = None,
    *,
    state: Optional[dict] = None,
    tag_cache: Optional[dict] = None,
    sha256: Optional[str] = None,
) -> Optional[Merge]:
    """Refresh a photo from its file. Used by the library scan, Files → DB, and
    the check before every write-back.

    Facts only the file knows (dimensions, date, GPS, camera, lens, EXIF) are
    taken from the file when present. Rating, label, title, caption and tags
    go through sync_merge, so edits made in fernKam that are not written back
    yet survive a file that another program changed meanwhile.

    Pass ``metadata`` (already read, e.g. via batched read_many_metadata) and
    ``state`` (sync_merge.load_states) to skip the per-photo reads, and
    ``sha256`` when the file's content changed. Returns
    None when the file could not be read — nothing is changed then, not even
    the recorded mtime, so the next scan tries again.
    """
    if metadata is None:
        metadata = await read_file_metadata_async(file_path)
    if not metadata:
        return None

    updates = {
        "modified_at": file_mtime,
        "file_modified_at_sync": file_mtime,
    }
    # The file's bytes changed, so its hash did too. A stale sha256 breaks
    # duplicate detection and matching files that later move.
    if sha256:
        updates["sha256"] = sha256
    for field in ["width", "height", "file_size", "taken_at", "latitude", "longitude",
                  "altitude", "orientation", "exif"]:
        val = metadata.get(field)
        if val is not None:
            updates[field] = val

    if metadata.get("camera"):
        try:
            updates["camera_id"] = await _get_or_create_camera(db, metadata["camera"])
        except Exception as cam_exc:
            log.warning("camera lookup/create failed for photo %d: %s", photo_id, cam_exc)
    if metadata.get("lens"):
        try:
            updates["lens_id"] = await _get_or_create_lens(db, metadata["lens"])
        except Exception as lens_exc:
            log.warning("lens lookup/create failed for photo %d: %s", photo_id, lens_exc)

    await db.execute(update(Photo).where(Photo.id == photo_id).values(**updates))

    if state is None:
        state = (await load_states(db, [photo_id])).get(photo_id)
        if state is None:
            return None
    return await apply_merge(db, photo_id, state, metadata, tag_cache if tag_cache is not None else {})


async def reconcile_outdated(db: AsyncSession, photo_ids: list[int]) -> int:
    """Before writing metadata into files: bring fernKam up to date with them.

    A file whose mtime differs from the one recorded when fernKam last read or
    wrote it was changed by another program; one with no recorded ancestor
    has never been compared. Those are re-read and merged first, so the write
    carries the other program's changes instead of overwriting them. Returns
    how many were re-read. Does not commit.
    """
    if not photo_ids:
        return 0
    rows = (await db.execute(
        select(Photo.id, Photo.album_path, Photo.filename, Photo.file_modified_at_sync,
               Photo.synced_meta.is_(None).label("no_base"))
        .where(Photo.id.in_(photo_ids))
    )).all()
    from fernkam.thumbnails import photo_disk_path

    def _stat(paths: list[Path]) -> dict[Path, datetime]:
        out = {}
        for p in paths:
            try:
                out[p] = datetime.fromtimestamp(p.stat().st_mtime, tz=timezone.utc)
            except OSError:
                pass
        return out

    by_path = {photo_disk_path(r.album_path, r.filename): r for r in rows}
    mtimes = await asyncio.get_running_loop().run_in_executor(None, _stat, list(by_path))
    outdated = [
        (path, by_path[path], mtime) for path, mtime in mtimes.items()
        if by_path[path].no_base or by_path[path].file_modified_at_sync is None
        or abs((mtime - by_path[path].file_modified_at_sync).total_seconds()) > MTIME_SLACK_SECONDS
    ]
    if not outdated:
        return 0

    loop = asyncio.get_running_loop()
    meta_map = await read_many_metadata_async([p for p, *_ in outdated])
    hashes = await asyncio.gather(*[loop.run_in_executor(None, _sha256_path, p) for p, *_ in outdated])
    states = await load_states(db, [r.id for _, r, _ in outdated])
    tag_cache: dict = {}
    for (path, row, mtime), digest in zip(outdated, hashes):
        changed = row.file_modified_at_sync is None or \
            abs((mtime - row.file_modified_at_sync).total_seconds()) > MTIME_SLACK_SECONDS
        await update_photo_metadata(db, row.id, path, mtime, metadata=meta_map.get(path, {}),
                                    state=states.get(row.id), tag_cache=tag_cache,
                                    sha256=digest if changed else None)
    return len(outdated)
