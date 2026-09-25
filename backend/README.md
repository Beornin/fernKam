# fernKam backend

FastAPI app served by Granian, with async SQLAlchemy 2 (asyncpg) on PostgreSQL 17 + pgvector.
It also serves the built frontend (`../frontend/build`) on the same port. Setup and running
are covered in the [top-level README](../README.md); this file covers the code.

## Commands

```sh
uv sync                                  # dependencies (+ dev group: reload support)
uv run fernkam setup-db [--docker]       # database user/db/extensions, write .env, migrate
uv run fernkam preflight [--digikam]     # environment checks; exit code 1 on a hard failure
uv run fernkam serve [--reload] [--host 0.0.0.0] [--port 8000]
uv run fernkam import-digikam [--commit] # one-time digiKam MariaDB import (dry run by default)
uv run fernkam verify                    # digiKam vs fernKam row counts

uv run alembic upgrade head              # the server also does this on every start
uv run alembic revision -m "..."         # new migration in alembic/versions/

for t in tests/test_*.py; do uv run python "$t"; done
```

## Layout

```
src/fernkam/
├── config.py            Settings (pydantic-settings). Loads backend/.env into the process
│                        environment too, so FERNKAM_* knobs read via os.getenv work from .env.
├── cli.py               Typer CLI (setup-db, preflight, serve, import-digikam, verify)
├── api/
│   ├── app.py           FastAPI app: startup sequence, cross-site write guard, routers, SPA fallback
│   ├── deps.py          `DB` dependency (one AsyncSession per request)
│   ├── schemas.py       Pydantic response models
│   └── routers/         photos, albums, tags, people, faces/, media, sync/ (scan, metadata,
│                        maintenance, tasks), dedup, stacks, workflows, semantic, geocode,
│                        saved_searches, outside_changes, backup, logs, debug
├── services/
│   ├── photo_query.py   The one photo filter/search/sort/keyset-cursor builder (grid, search,
│   │                    smart albums, debug EXPLAIN)
│   └── stacks.py        RAW+derivative stack detection
├── importers/
│   ├── filesystem.py    Library scanner: walk → import new / refresh changed / remove gone
│   └── digikam.py       digiKam MariaDB → PostgreSQL importer
├── workflows/           File-system workflows (sort videos, move stray RAWs, remove unkept
│                        RAWs, sync stack tags) — plain functions run in a worker thread
├── db/
│   ├── models/          ORM models (photos.py holds almost everything; tasks.py)
│   ├── session.py       Engine + session factories (async pool: 20 + 30 overflow)
│   ├── index_setup.py   Idempotent startup indexes + per-database planner settings
│   └── *_setup.py       Idempotent startup DDL for tables created outside Alembic
├── task_manager.py      Background task registry (the `tasks` table + in-memory cache)
├── face_processor.py    InsightFace detection/embedding, pgvector similarity helpers
├── clip_embed.py        CLIP ViT-B/32 image/text towers via onnxruntime (downloaded on first use)
├── metadata_sync.py     exiftool read (persistent -stay_open process) and XMP write-back
├── sync_merge.py        three-way merge of editable metadata between catalogue and file
├── library_watch.py     watches LIBRARY_ROOT while running and triggers scans
├── thumbnails.py        WebP thumbnails in the disk cache, RAW previews (rawpy), video frames (ffmpeg)
├── media_types.py       Extension → image/video/RAW classification
├── logs_sink.py         Batched, de-duplicated WARNING+ logs into app_logs (the Logs page)
└── stderr_capture.py    Captures native stderr (OpenCV/ffmpeg noise) into the same sink
```

## Things worth knowing before changing code

**Startup** (`api/app.py` lifespan): Alembic `upgrade head`, connectivity check, pgvector
columns/HNSW indexes, `app_logs`/`tasks`/`person_centroids`/`face_clusters` tables, index
maintenance, then any tasks left "running" by a previous process are marked cancelled. Then a
background library scan refreshes from disk (`SCAN_ON_STARTUP`), the library watcher starts
(`WATCH_LIBRARY`), and InsightFace warms up. Every step is idempotent and logs instead of
crashing.

**Staying in sync with the files.** `photos.synced_meta` holds rating, label, title, caption and
tag keys as the file had them at the last read or write. Every path that reads a file
(`importers.filesystem.update_photo_metadata`: scans, Files → DB, the pre-write check) runs
`sync_merge.apply`: changes only in the file are taken, fernKam-only changes are kept, and on
conflict the file wins with the replaced value recorded in `outside_changes` for restore. Tags
are compared by ltree key (`sync_merge.tag_key`), so names written from the database read back
equal. Rows without an ancestor never have tags touched. A file that lost all its metadata is
treated as damaged. Every path that writes a file calls `metadata_sync.mark_files_synced`: it
records mtime, size and sha256, and reads the file back for the new ancestor. Before writing,
`importers.filesystem.reconcile_outdated` re-reads and merges files changed since the last read.

**The watcher** (`library_watch.py`) runs watchfiles' synchronous `watch()` in its own daemon
thread. Its async `awatch()` uses AnyIO's thread pool, whose non-daemon workers kept the
Granian worker from exiting. Events only say *where*: after a quiet period it calls
`start_library_scan()` on the smallest folder covering them, so every scan rule applies.

**Schema.** Alembic owns the core tables (`photos`, `tags`, `photo_tags`, `faces`, `cameras`,
`lenses`, `photo_stacks`, `saved_searches`, `app_settings`). A few support tables and indexes are
also created by the `db/*_setup.py` helpers at startup, for historical reasons. The pgvector
extension must exist before migrations run, and creating it needs a superuser; `setup-db` and
`docker-initdb` handle that. Migrations must work on an empty database: `setup-db` runs the
whole chain from scratch.

**Background tasks.** Long jobs are `asyncio` tasks started from an endpoint, tracked through
`task_manager` (`create_task` / `update_task`), and visible on the Tasks page. They open their
own sessions (the request's session closes when the handler returns). Loops should check
`task.status == "cancelled"` between batches. Four task types touch files (`FILE_MUTATING_TASKS`:
the library scan and three workflows), and `create_task` refuses to start one while another is
still working. Cancelling one only takes effect once its work actually stops. Endpoints that move
files without a task (promote to portfolio) call `task_manager.ensure_no_file_task()`.

**Deleting rows is expensive to get wrong.** A photo row cascades to its tags, faces and rating.
The scanner only removes rows for files it could positively see were gone. It keeps rows under
folders it could not read, deletes nothing if the walk found no media at all (unplugged drive),
and matches on id *and* path so a row moved mid-scan survives. Before deleting, vanished rows
are matched to newly found files by sha256 (`_match_moves`): a moved or renamed file keeps its
row. So sha256 must stay current; it is refreshed whenever a changed file is re-read or
written. Scans only walk inside `LIBRARY_ROOT` (`resolve_scan_root`). Photo trash and dedup
auto-clean are soft deletes (`status = 0`).

**Destructive endpoints default to preview.** Every workflow request model has
`dry_run: bool = True` and `extra="forbid"`, so a typo'd field is a 422, not a silent real run.

**Thumbnails** live on disk at `data/thumbnails/{id % 1000:03d}/{id}_{size}.webp` (sizes
`sm`/`md`/`lg`/`xl`/`xxl`), written atomically, generated on demand if missing, served with an
ETag and `no-cache` so browsers revalidate. When a file changes, `thumbnails.refresh_thumbnail`
rebuilds the `md` size and reports whether the picture visibly changed, which decides whether
faces and the CLIP embedding are reset. Face crops (200 px) are stored in `faces.crop_data`.

**XMP write-back** (`metadata_sync.write_metadata_batch`) runs one exiftool process per batch
and returns (written, {failed: reason}), using `-efile`. Only written files get
`mark_files_synced()`; failed ones stay "needs sync". Payloads never contain `None`, which
exiftool writes as the text "null". To clear a field, send `""` or `[]`, and only when
`synced_meta` says the file has a value.

**Sorting and pagination.** Every sort in `photo_query.SORT_OPTIONS` ends in an `id`
tiebreaker matching the keyset cursor's direction. Without it, pages skip or repeat photos
that tie on the sort key.

**Security model.** No authentication, because it's a single-user desktop app. `serve` binds
to `127.0.0.1`. A middleware in `app.py` rejects state-changing requests whose `Origin` is not
loopback, not the address the server was reached on, and not in `CORS_ORIGINS`. That blocks
drive-by requests from other websites and DNS rebinding.

**Event loops.** Workflows run in worker threads. If one needs the database, it must create
its own engine (see `workflows/sync_stack_tags.py`). The shared async engine's pooled
connections belong to the server's event loop.

## Tests

Standalone scripts; each prints `ok - ...` or raises:

| | needs DB |
|---|---|
| `test_scan_skip.py`: unchanged files are skipped by mtime | no |
| `test_scan_removals.py`: which rows a scan may delete | no |
| `test_photo_sort.py`: sort tiebreakers match the cursor | no |
| `test_task_conflict.py`: file-mutating task guard, incl. double-click and cancel | no |
| `test_promote_destination.py`: RAW/ convention preserved when promoting | no |
| `test_sync_merge.py`: three-way metadata merge rules | no |
| `test_move_match.py`: moved/renamed files matched by content hash | no |
| `test_scan_root.py`: scans stay inside `LIBRARY_ROOT` | no |
| `test_no_duplicate_photos.py`: live catalogue has no duplicate paths | yes (`.env`) |
