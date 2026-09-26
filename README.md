# fernKam

A self-hosted photo and video organizer for one person and one big library, built as a
faster replacement for digiKam. Your files stay where they are on disk; fernKam keeps a
PostgreSQL catalogue next to them for search, faces, tags and culling. It writes changes back
into the files (embedded XMP) when you ask it to, and picks up edits other programs make to
them.

It runs as a local web app (FastAPI + SvelteKit). An optional Windows launcher (`fernKam.exe`)
shows it in a native window.

## Features

- **Browse**: album folders mirrored from disk, a timeline, hierarchical tags (`ltree`), star
  ratings, colour labels, a GPS map, and smart albums (saved searches).
- **Search**: full-text search over filenames, titles, captions, tags, camera and lens
  (`pg_trgm` + `tsvector`), plus combinable filters.
- **Semantic search** (*Discover*): type "dog on a beach" and get matching photos, even
  untagged ones. It uses CLIP image embeddings in pgvector with an HNSW index. The same index
  finds visually similar photos and near-duplicates, and suggests tags for untagged photos
  based on the tags you have approved.
- **Faces**: detection and recognition with InsightFace (`buffalo_l`), on the GPU when one is
  available. Includes auto-confirm with a sensitivity slider, clustering of unknown faces so a
  whole group can be named at once, and a review queue.
- **Tag Review**: tags work like faces. Tags read from files start
  unverified, and you check them one tag at a time: click the wrong photos, press Enter to
  approve the rest. Once a tag has 8 approved photos, fernKam learns it from your decisions
  and suggests it on other photos for you to accept or reject. Every decision retrains it.
  Unverified tags are never used for learning.
  - **Several image models**: CLIP, plus optional SigLIP 2 (general scenes, much stronger;
    the 512 px version keeps small, distant subjects sharp) and BioCLIP 2 (wildlife: tells
    similar species apart). For each tag, fernKam learns how much to trust each model, so
    species tags lean on BioCLIP and scenes on SigLIP.
  - **Where and when**: place and season are learned per tag from your approvals, and a
    species tag can be linked to GBIF so real sighting records around the places you shoot,
    by month, become a prior. A heron in Norway in January is doubted, and flagged on the
    photo.
  - **Local vision model double-check**: a vision-language model in Ollama (e.g.
    `qwen3-vl:32b` on a 24 GB GPU) looks at each suggestion and answers yes or no. Its answers show on the
    photo, and fernKam measures how often it agrees with you. It never trains anything.
  - **Find by name**: before a tag has any approved photos, search for it by its name to get
    the first ones quickly.
- **Culling**: keyboard-driven Review Mode (`1`–`5` rate, `0` clear, `X` reject, wheel zoom),
  shift-click range selection, and a right-click menu.
- **Library tools**: exact (SHA-256) duplicate finder with folder-priority auto-clean, RAW+JPEG
  stacks, date inference for undated photos, reverse geocoding (offline), and pipeline
  workflows (sort, move stray RAWs, remove unkept RAWs, promote to portfolio). Every workflow
  has a dry-run preview.
- **Metadata in the files**: tags, ratings, labels, captions and named face regions are written
  into the files as embedded XMP, in a digiKam-compatible format. Only photos with unsaved
  changes are written, and a file that can't be written stays pending for the next run.
  Right-click any selection to reread it from, or write it to, its files.
- **Stays in sync with other programs**: fernKam refreshes from disk at startup and watches the
  library while running. An edit made elsewhere (Lightroom, digiKam, exiftool) is merged field by
  field: changes only in the file are taken, unsaved fernKam edits survive, and where both
  changed the same field the outside edit wins. You see all of it on **Changed outside
  fernKam**, with one-click restore of any fernKam value that was replaced. Files moved or
  renamed outside fernKam keep their tags, faces and ratings (matched by content hash, as in
  digiKam). A re-edited picture gets fresh thumbnails, faces and search embedding.
- **Maintenance**: background tasks you can watch and cancel, one-click database backup and
  restore, VACUUM/REINDEX, and an in-app log viewer.

## Recent changes

- **Tag Review** (left rail): every tag from files starts unverified; you approve
  or reject them like faces, and only those decisions teach fernKam. It then suggests tags
  using several image models (CLIP, SigLIP 2, BioCLIP 2 for wildlife), where and when the
  photo was taken (with GBIF species ranges), and a local vision model in Ollama as a second
  opinion. See [Tag Review models](#tag-review-models).
- **Honours edits made in other programs**: refresh from disk at startup, a live folder
  watcher, a field-by-field merge with **Changed outside fernKam** (restore what was
  replaced), and moved or renamed files keep their tags.
- **Write-back** goes into the files only (no sidecars), per file, retrying only failures;
  right-click to reread from or write to files.
- Upgrading? See [Updating an existing install](#updating-an-existing-install).

## How it fits together

```
fernKam.exe (optional, Windows)          your browser (http://localhost:8000)
        │ starts + hosts in a window              │
        ▼                                         ▼
  backend: FastAPI on Granian ── serves /api, /media and the built UI (frontend/build)
        │            │                 │
        │            │                 ├─ exiftool, ffmpeg
        │            │                 ├─ InsightFace, CLIP, SigLIP 2, BioCLIP 2 (onnxruntime, CUDA)
        │            │                 ├─ Ollama (optional): local vision model for Tag Review
        │            │                 └─ GBIF API (optional): species sighting counts
        │            └─ thumbnail cache and model files on disk (backend/data/)
        ▼
  PostgreSQL 17 + pgvector ── catalogue, tags, faces, embeddings (Docker or native)
```

The backend runs database migrations every time it starts.

## Requirements

| | |
|---|---|
| Python | 3.12, managed with [uv](https://docs.astral.sh/uv/) |
| Node.js | 22 LTS (to build the UI) |
| PostgreSQL | 17 with the **pgvector** extension (Docker makes this one command) |
| exiftool | required: reads and writes metadata ([exiftool.org](https://exiftool.org/)) |
| ffmpeg | recommended: video thumbnails, durations and in-browser playback |
| GPU | optional: an NVIDIA GPU with CUDA 12 makes face detection and the image models much faster; everything also runs on the CPU |
| Ollama | optional: runs the local vision model that double-checks tag suggestions ([ollama.com](https://ollama.com/)) |

Windows is the primary platform (the launcher, CUDA paths and "Reveal in Explorer" assume
it). The backend and UI also run on Linux and macOS.

## Quick start

### 1. Get a database

**Option A: Docker (recommended).** This gives you PostgreSQL 17 with pgvector already
installed, reachable only from this machine:

```sh
docker compose up -d
```

That's all. The user, database and extensions are created on first start, and data persists in
a Docker volume. On Windows this needs [Docker Desktop](https://www.docker.com/products/docker-desktop/).
Only the database runs in Docker; your photos and the GPU stay on the host.

**Option B: native PostgreSQL.** Install PostgreSQL 17 and pgvector (see
[Native PostgreSQL](#native-postgresql) below). You'll need the superuser (`postgres`)
password for the next step.

### 2. Set up the backend

```sh
cd backend
uv sync                                                          # installs Python 3.12 + dependencies

# Docker database:
uv run fernkam setup-db --docker --library-root "D:/Pictures"
# ...or native database (prompts for the postgres password):
uv run fernkam setup-db --library-root "D:/Pictures"

uv run fernkam preflight                                         # checks DB, exiftool, ffmpeg, GPU, UI build
```

`setup-db` is safe to re-run. It creates the database user (with a generated password), the
database and the extensions. It then writes `backend/.env` and migrates the schema to the
latest version. Every other setting is optional; see [`backend/.env.example`](backend/.env.example).

### 3. Build the UI

```sh
cd frontend
npm ci
npm run build
```

### 4. Run it

```sh
cd backend
uv run fernkam serve
```

Open <http://localhost:8000>. The first start scans everything under `LIBRARY_ROOT` in the
background (metadata, thumbnails, and face detection as it goes); watch it in the status bar.
After that, every start refreshes whatever changed on disk, and the library is watched while
fernKam runs. **Quick Scan** on the Home page runs the same scan on demand. For semantic search,
open **Discover** and start indexing; after that, new and re-edited photos are indexed
automatically. The CLIP model (~600 MB) downloads on first use. So does the face model
(~300 MB), on the first scan.

## Tag Review models

Tag Review works with CLIP alone. These make its suggestions much better. All of it is
optional, runs locally, and is managed from **Tag Review → Models**. With a 24 GB GPU
(RTX 3090/4090) the recommended set is SigLIP 2 at 512 px, BioCLIP 2 and `qwen3-vl:32b`.

1. **CLIP**: open **Discover** and index the library (~600 MB download). Find-by-name uses
   its text tower.
2. **SigLIP 2 (512 px)**: **Install and index** (1.7 GB). Or from `backend/`:
   `uv run fernkam download-model siglip2_512 --text`, then **Index library** in the panel.
   The 384 px and base versions are there too, for smaller GPUs or a CPU.
3. **BioCLIP 2** (wildlife): **Build and index**. It exists only as PyTorch weights, so it is
   exported to ONNX once, in a throwaway environment that uv creates (PyTorch never enters
   fernKam's own). This downloads PyTorch and the ~1.7 GB checkpoint. From a terminal:

   ```sh
   cd backend
   uv run --with open-clip-torch fernkam export-model bioclip2
   ```

   The export is kept only if onnxruntime reproduces PyTorch's output. Then **Index library**.
4. **Vision model**: install [Ollama](https://ollama.com/) and pull one:
   `ollama pull qwen3-vl:32b` (most accurate that fits 24 GB) or `qwen3-vl:8b` (quick).
   The Models panel finds it at `http://127.0.0.1:11434` and picks the best one pulled.
5. **Review**: pick a tag, click the wrong photos, press Enter to approve the rest. After 8
   approvals the tag learns; the learning panel shows how much it trusts each model. For a
   species, **Link species…** fetches its GBIF range for the places you photograph.
   **Learn all tags** (with *+ vision check*) relearns everything in the background.

Model files live in `backend/data/models/`: about 4 GB for the recommended set, 6 GB with
SigLIP 2's text tower. Indexing runs in the background (progress and Cancel on the Tasks
page); the 512 px model is the slowest, so a large library takes a while the first time.
After that, new and re-edited photos are indexed at every scan. Image models free the GPU
after indexing, so Ollama has room.

## Updating an existing install

```sh
git pull
cd backend && uv sync
cd ../frontend && npm install && npm run build
cd ../backend && uv run fernkam serve
```

The server applies new database migrations on start. What to expect the first time:

- All existing tags show as **unverified** in Tag Review. Nothing changes in your files.
- Each photo's file state is recorded the next time fernKam reads or writes that file (a
  changed file at a scan, a reread, or just before a write-back). From then on, edits made
  in other programs are merged instead of overwritten. Until then, a scan never touches that
  photo's tags.
- Photos flagged "needs sync" stay flagged; **Maintenance → Write N pending** writes them.

## Native PostgreSQL

`fernkam setup-db` needs a superuser once, because pgvector is not a "trusted" extension:
the app's own user cannot create it. The command checks for pgvector *before* it creates
anything, and tells you if it's missing.

- **Linux (Debian/Ubuntu)**: `sudo apt install postgresql-17 postgresql-17-pgvector`
- **macOS**: `brew install postgresql@17 pgvector`
- **Windows**: install PostgreSQL 17 from
  [postgresql.org/download/windows](https://www.postgresql.org/download/windows/). pgvector has
  no official Windows binaries, so it must be built once with the Visual Studio 2022 C++ build
  tools ("Desktop development with C++"). From an **x64 Native Tools Command Prompt for VS 2022**
  run as Administrator:

  ```bat
  set "PGROOT=C:\Program Files\PostgreSQL\17"
  cd %TEMP%
  git clone --branch v0.8.6 https://github.com/pgvector/pgvector.git
  cd pgvector
  nmake /F Makefile.win
  nmake /F Makefile.win install
  ```

  If that's more than you want to deal with, use the Docker database instead.

If you'd rather use `psql` than `setup-db`, [`scripts/init-db.sql`](scripts/init-db.sql) does the
same thing:

```sh
psql -U postgres -v pw='choose-a-password' -f scripts/init-db.sql
```

Then set `PG_URL` in `backend/.env` yourself.

PostGIS is **not** needed.

## Configuration

Settings live in `backend/.env` (created by `setup-db`, or copy `.env.example`). Real environment
variables override it. The ones you're most likely to touch:

| Setting | Default | |
|---|---|---|
| `PG_URL` | Docker defaults | `postgresql+asyncpg://user:password@host:port/db` |
| `LIBRARY_ROOT` | — | Folder holding your photos. Album paths are stored relative to it. |
| `PG_DOCKER_CONTAINER` | unset | `fernkam-db` when using Docker, so backups run inside the container |
| `EXIFTOOL_PATH`, `FFMPEG_PATH` | from `PATH` | Explicit tool locations |
| `THUMB_CACHE_DIR` | `data/thumbnails` | Relative to `backend/` |
| `SCAN_ON_STARTUP` | `true` | Refresh from disk in the background at every start |
| `PURERAW_EXE` | `C:\Program Files\DxO\DxO PureRAW 6\PureRAWv6.exe` | DxO PureRAW, for *Develop with DxO PureRAW* (Workflows) and developing new AA_RAW shoots automatically (Settings) |
| `CLIENT_FOLDER` | empty | Where *Finish shoot* sends JPGs of client work (the L destination). Outside the library, so they leave the catalogue; empty hides the option |
| `WATCH_LIBRARY` | `true` | Pick up outside edits while running, scanned every 10 minutes by default (change it under Settings; 0 = off). `WATCH_LIBRARY_POLLING=true` for network shares |
| `FERNKAM_FACE_GPU`, `FERNKAM_CLIP_GPU` | `1` | `0` forces the CPU (`FERNKAM_CLIP_GPU` covers all image models) |
| `FERNKAM_EMBED_BATCH` | per model (8 at 512 px, 16 at 384 px, else 32) | Images per GPU call when indexing Tag Review models. Lower it if indexing crawls on a smaller card (VRAM spilling into system RAM) |
| `VISION_URL` | `http://127.0.0.1:11434` | Vision model server for Tag Review: Ollama, or any OpenAI-compatible URL ending in `/v1`. Photos are sent here, so keep it local. |
| `VISION_MODEL` | best vision model found | Starting choice; pick another on Tag Review → Models |
| `GBIF_URL` | `https://api.gbif.org/v1` | Species range data for Tag Review (public API, no key; only record counts for boxes around your places are fetched) |
| `CORS_ORIGINS` | none | Extra browser origins allowed to make changes (see below) |
| `DEBUG` | `false` | Enables `/api/debug` query plans and SQL echo |

`.env.example` documents the rest: face-matching thresholds, pipeline folder names,
concurrency knobs, Postgres planner settings and log retention.

**Network exposure.** The API has no login, and it can move, trash and delete originals. So
`fernkam serve` listens on `127.0.0.1` only. Browser requests that change data are rejected
unless they come from this machine or from the address the server was reached on. That stops a
random web page from driving the API through your browser. To use fernKam from another device,
run `fernkam serve --host 0.0.0.0` on a network you trust.

## Desktop launcher (Windows)

`launcher.py` starts the backend from `backend/.venv` and shows it in a native pywebview window,
with a splash screen while the backend starts. Closing the window shuts everything down.
To build a standalone `fernKam.exe`:

```sh
cd frontend && npm run build && cd ..
pip install -r requirements-launcher.txt
pyinstaller launcher.spec
```

The exe records this checkout's path when it's built, so you can move or pin it anywhere. If
you move the repository itself, rebuild. Logs go to `logs/launcher.log`. Set
`DEBUG_CONSOLE=1` before building to get a console window.

## Backups

**Maintenance → Backup Database** writes a `pg_dump` file to `backend/data/backups/`, and
**Restore** loads one back. With a Docker database, set `PG_DOCKER_CONTAINER=fernkam-db`
(`setup-db --docker` does this) and no PostgreSQL client is needed on the host. Otherwise
`pg_dump`/`pg_restore` (same major version as the server or newer) must be on `PATH`, or in the
default `C:\Program Files\PostgreSQL\<version>\bin`.

Thumbnails and face/CLIP models are regenerable caches and are not part of the backup. Your XMP
metadata lives in the files themselves once written back (**Maintenance → DB → Files**).

## Development

```sh
# backend with auto-reload (API on :8000)
cd backend && uv run fernkam serve --reload

# frontend dev server with hot reload on :5173 (proxies /api and /media to :8000)
cd frontend && npm run dev

# tests: standalone scripts, no test framework needed
cd backend && for t in tests/test_*.py; do uv run python "$t"; done

# type-check the frontend
cd frontend && npm run check

# new migration
cd backend && uv run alembic revision -m "describe the change"
```

`test_no_duplicate_photos.py` checks the configured database; the other tests need no database.
See [`backend/README.md`](backend/README.md) and [`frontend/README.md`](frontend/README.md) for
the code layout.

## Project structure

```
fernKam/
├── backend/                 FastAPI app, CLI, migrations — see backend/README.md
│   ├── src/fernkam/         incl. tag_learning, embed_models, vision_check, species_range
│   ├── data/                thumbnails, backups, model files (created at runtime)
│   ├── alembic/versions/    schema history (applied automatically on startup)
│   ├── tests/
│   └── .env.example         every setting, documented
├── frontend/                SvelteKit (Svelte 5) static SPA — see frontend/README.md
├── scripts/
│   ├── docker-initdb/       extensions for the Docker database (first start only)
│   ├── init-db.sql          manual psql alternative to `fernkam setup-db`
│   └── generate_icon.py     regenerates assets/fernkam.ico
├── docker-compose.yml       PostgreSQL 17 + pgvector
├── launcher.py / .spec      Windows desktop launcher (pywebview + PyInstaller)
├── ROADMAP.md               what was built and why, with measurements
└── WORKFLOWS.md             what each action does to the database and your files
```

## Troubleshooting

- **Anything setup-related**: run `uv run fernkam preflight` first. It names the problem.
- **`permission denied to create extension "vector"`**: the extensions must be created by a
  superuser. Run `uv run fernkam setup-db` (or `scripts/init-db.sql`) instead of creating the
  database by hand.
- **Port 5432 already in use** when starting Docker: a native PostgreSQL is running. Use
  `FERNKAM_DB_PORT=5433 docker compose up -d` and `uv run fernkam setup-db --docker --port 5433`.
- **Faces/CLIP are slow**: check `preflight`'s onnxruntime line. `CPUExecutionProvider` only
  means CUDA 12 + cuDNN 9 aren't visible to `onnxruntime-gpu`.
- **Import refuses a folder**: fernKam catalogues only what's under `LIBRARY_ROOT`. Copy the
  photos into the library first, then import that folder.
- **Tag Review suggests nothing**: a tag needs 8 approved photos that an image model has
  indexed. Before that, use **Find by name** (needs CLIP indexed, or a model's text tower).
- **BioCLIP 2 build fails**: run the export command in a terminal to see why. It needs `uv` on
  `PATH` and internet access to PyPI and Hugging Face. **Build and index** only appears when
  the server can find `uv`.
- **Model download stops**: files download to `backend/data/models/<model>/` with a `.part`
  suffix and are renamed only when complete, so a broken download is never used. **Install**
  again downloads that file again.
- **Vision model not reachable**: start Ollama (`ollama serve`), check `VISION_URL`, and pull a
  vision model (`ollama list` should show one). Any OpenAI-compatible server works with a URL
  ending in `/v1`.
- **Link species… fails**: fernKam could not reach `api.gbif.org` (firewall or offline).
  Species priors are optional; everything else keeps working.
- **403 "Cross-site request blocked"**: you're reaching the UI through an address the server
  doesn't recognise (a reverse proxy, a custom hostname). Add that origin to `CORS_ORIGINS`.
