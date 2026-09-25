# fernKam

A self-hosted photo and video organizer for one person and one big library, built as a
faster replacement for digiKam. Your files stay where they are on disk; fernKam keeps a
PostgreSQL catalogue next to them for search, faces, tags and culling. It writes changes back
to the files as XMP when you ask it to.

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
  based on how your own library is tagged.
- **Faces**: detection and recognition with InsightFace (`buffalo_l`), on the GPU when one is
  available. Includes auto-confirm with a sensitivity slider, clustering of unknown faces so a
  whole group can be named at once, and a review queue.
- **Culling**: keyboard-driven Review Mode (`1`–`5` rate, `0` clear, `X` reject, wheel zoom),
  shift-click range selection, and a right-click menu.
- **Library tools**: exact (SHA-256) duplicate finder with folder-priority auto-clean, RAW+JPEG
  stacks, date inference for undated photos, reverse geocoding (offline), and pipeline
  workflows (sort, move stray RAWs, remove unkept RAWs, promote to portfolio). Every workflow
  has a dry-run preview.
- **XMP sync**: tags, ratings, labels, captions and named face regions are written to the files
  in a digiKam-compatible format, and can be read back in.
- **Maintenance**: background tasks you can watch and cancel, one-click database backup and
  restore, VACUUM/REINDEX, and an in-app log viewer.
- **digiKam import**: one-time migration of photos, tags and faces from a digiKam MariaDB.

## How it fits together

```
fernKam.exe (optional, Windows)          your browser (http://localhost:8000)
        │ starts + hosts in a window              │
        ▼                                         ▼
  backend: FastAPI on Granian ── serves /api, /media and the built UI (frontend/build)
        │            │                 │
        │            │                 └─ exiftool, ffmpeg, InsightFace + CLIP (onnxruntime, CUDA)
        │            └─ thumbnail cache on disk (backend/data/thumbnails)
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
| GPU | optional: an NVIDIA GPU with CUDA 12 makes face detection and CLIP much faster; everything also runs on the CPU |

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

Open <http://localhost:8000>. On the Home page, **Quick Scan** imports everything under
`LIBRARY_ROOT`: metadata, thumbnails, and face detection as it goes. For semantic search, open
**Discover** and start indexing. The CLIP model (~600 MB) downloads on first use. So does the
face model (~300 MB), on the first scan.

Coming from digiKam? See [Migrating from digiKam](#migrating-from-digikam).

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
| `FERNKAM_FACE_GPU`, `FERNKAM_CLIP_GPU` | `1` | `0` forces the CPU |
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

## Migrating from digiKam

The importer copies photos, tags, ratings, labels and face regions from digiKam's MariaDB
database. It never modifies digiKam's data.

```sh
cd backend
# set MYSQL_URL in .env (e.g. mysql+pymysql://root@localhost:3306/digikam), then:
uv run fernkam preflight --digikam
uv run fernkam import-digikam            # dry run: shows what would be imported
uv run fernkam import-digikam --commit
uv run fernkam verify                    # compares row counts
```

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
│   ├── src/fernkam/
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
- **403 "Cross-site request blocked"**: you're reaching the UI through an address the server
  doesn't recognise (a reverse proxy, a custom hostname). Add that origin to `CORS_ORIGINS`.
