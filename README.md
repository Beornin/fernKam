# fernKam

Self-hosted photo & video organizer — a purpose-built DigiKam replacement.

## Features (v1 target)

- Browse by album folder (mirrors your HDD structure)
- Tags with hierarchical tree (ltree)
- Color labels & star ratings
- GPS map view (MapLibre GL)
- Face tagging & recognition (InsightFace/buffalo_l)
- Fast full-text search (pg_trgm)
- Vector similarity search for faces (pgvector)

## Stack

| Layer | Tech |
|---|---|
| Backend | Python 3.12, FastAPI, Granian |
| Database | PostgreSQL 17, pgvector, PostGIS, ltree, pg_trgm |
| ORM / Migrations | SQLAlchemy 2.0, Alembic |
| ML | InsightFace, ONNX Runtime (CPU+CUDA) |
| Frontend | SvelteKit, TailwindCSS, shadcn-svelte, MapLibre GL |
| Jobs | Dramatiq + Redis |
| Images | libvips (pyvips), ffmpeg |

## Phase 1 Status ✅

- [x] Repo scaffolded
- [x] PostgreSQL schema (photos, tags, photo_tags, faces, people, cameras, lenses, audit_log)
- [x] Alembic migrations
- [x] DigiKam MariaDB → PostgreSQL importer CLI
- [ ] pgvector + PostGIS extensions installed (see Setup)
- [ ] Import run against production data

## Setup

### 1. Install PostgreSQL extensions

**pgvector** (required for face embeddings):
1. Download the installer from https://github.com/pgvector/pgvector — Windows binary for PG17
2. Copy `vector.dll` → `C:\Program Files\PostgreSQL\17\lib\`
3. Copy `vector--*.sql` + `vector.control` → `C:\Program Files\PostgreSQL\17\share\extension\`

**PostGIS** (required for geo queries):
1. Open StackBuilder: `C:\Program Files\PostgreSQL\17\bin\stackbuilder.exe`
2. Select PostgreSQL 17 → Spatial Extensions → PostGIS

### 2. Create the database

```powershell
& "C:\Program Files\PostgreSQL\17\bin\psql.exe" -U postgres -f scripts\init-db.sql
```

Then edit the password in `backend\.env`.

### 3. Install Python dependencies

```powershell
cd backend
# Install uv if needed: pip install uv
uv sync
```

### 4. Run migrations

```powershell
cd backend
uv run alembic upgrade head
```

### 5. Run pre-flight check

```powershell
uv run fernkam preflight
```

### 6. Import DigiKam data

```powershell
# Dry run first (default)
uv run fernkam import-digikam

# When happy, commit
uv run fernkam import-digikam --commit

# Verify counts
uv run fernkam verify
```

## Project Structure

```
fernKam/
├── backend/
│   ├── src/fernkam/
│   │   ├── config.py          # Pydantic settings
│   │   ├── cli.py             # Typer CLI (preflight, import-digikam, verify)
│   │   ├── db/
│   │   │   ├── base.py        # DeclarativeBase
│   │   │   ├── session.py     # Engine factories
│   │   │   └── models/
│   │   │       └── photos.py  # All ORM models
│   │   └── importers/
│   │       └── digikam.py     # DigiKam → PG importer
│   ├── alembic/               # Migrations
│   ├── alembic.ini
│   ├── pyproject.toml
│   └── .env.example
├── scripts/
│   └── init-db.sql            # One-time DB + user setup
└── README.md
```

## Phase 2 (next)

- FastAPI REST endpoints
- SvelteKit frontend (albums, tags, map, faces)
- Thumbnail generation (libvips)
- Background workers (Dramatiq)
- Face embedding generation (InsightFace)
