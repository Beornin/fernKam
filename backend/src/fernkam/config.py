from functools import lru_cache
from pathlib import Path

from dotenv import load_dotenv
from pydantic import field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# backend/ — .env, alembic.ini and the default data/ directory live here.
BACKEND_DIR = Path(__file__).resolve().parents[2]
ENV_FILE = BACKEND_DIR / ".env"

# Export .env into the process environment as well as into Settings. Several
# knobs are read straight from os.environ (FERNKAM_FACE_GPU, FERNKAM_*_CONCURRENCY,
# FERNKAM_CLIP_DIR, ...), and so were the migration URLs; without this they
# silently ignored .env and only worked as real environment variables.
# override=False: a variable set in the real environment still wins.
load_dotenv(ENV_FILE, override=False)


class Settings(BaseSettings):
    # Absolute, so running from the repo root (or anywhere) reads backend/.env
    # rather than whatever .env happens to be in the working directory.
    model_config = SettingsConfigDict(env_file=ENV_FILE, env_file_encoding="utf-8", extra="ignore")

    # PostgreSQL. The defaults match docker-compose.yml. PG_URL_SYNC is
    # optional: when unset it is derived from PG_URL.
    pg_url: str = "postgresql+asyncpg://fernkam_user:fernkam@localhost:5432/fernkam"
    pg_url_sync: str = ""

    # DigiKam MariaDB (import only)
    mysql_url: str = "mysql+pymysql://root@localhost:3306/digikam"

    # App
    debug: bool = False
    library_root: str = "D:/Pictures and Videos"

    # Backup
    backup_dir: str = "data/backups"

    # Thumbnails
    thumb_cache_dir: str = "data/thumbnails"

    # External tools — leave blank to rely on PATH lookup (shutil.which), or
    # set an explicit path via env var if the tool isn't on PATH.
    ffmpeg_path: str = ""
    exiftool_path: str = ""

    # Face matching thresholds. Every field in this class is read from the env
    # var of the same name, upper-cased, with no prefix — e.g. SUGGEST_THRESH.
    # The auto-confirm threshold and k-NN margin are not here: they come from
    # the face sensitivity slider on the Settings page (faces/_helpers.py).
    suggest_thresh: float = 0.5        # SUGGEST_THRESH

    # k-NN voting for auto-confirm sweep
    knn_k: int = 15                    # KNN_K: confirmed neighbours per face
    knn_min_votes: int = 2             # KNN_MIN_VOTES: min votes for top person

    # Detection quality gate (0 = disabled)
    min_det_score: float = 0.5         # MIN_DET_SCORE
    min_face_px: int = 30              # MIN_FACE_PX: ignore crops smaller than this
    min_blur_score: float = 10.0       # MIN_BLUR_SCORE: Laplacian variance (0=disabled)

    # Minimum best_match_score for a confirmed face to be used as a k-NN reference
    min_ref_score: float = 0.55        # MIN_REF_SCORE

    # Person birth dates — JSON map of person name → ISO date string
    # Faces suggested for these people on photos taken BEFORE their birth date are dropped.
    # Example: PERSON_MIN_DATES='{"Alice": "2018-03-22", "Bob": "2024-05-18"}'
    person_min_dates: str = "{}"  # PERSON_MIN_DATES

    # ── Staying in sync with the files ──
    # Scan the library in the background every time the server starts, so
    # edits made by other programs while fernKam was closed are picked up
    # (a no-op rescan of ~116k files takes ~12 s).
    scan_on_startup: bool = True              # SCAN_ON_STARTUP
    # Keep watching while running (digiKam's "monitor the albums for external
    # changes"): edits, additions, moves and deletions made by other programs
    # are picked up within seconds. Polling is for network shares that don't
    # deliver change notifications.
    watch_library: bool = True                # WATCH_LIBRARY
    watch_library_polling: bool = False       # WATCH_LIBRARY_POLLING

    # ── Tag Review: local vision model ──
    # A vision-language model double-checks tag suggestions (vision_check.py).
    # Ollama's own API by default; a URL ending in /v1 is treated as any
    # OpenAI-compatible server (LM Studio, llama.cpp). Photos are sent to this
    # URL, so keep it on this machine. The model is picked on the Tag Review
    # page; this is only the starting choice (empty = first vision model found).
    vision_url: str = "http://127.0.0.1:11434"   # VISION_URL
    vision_model: str = ""                        # VISION_MODEL
    # Species range priors (species_range.py): GBIF's public API, no key.
    # Only aggregate record counts for boxes around the library's places are
    # fetched.
    gbif_url: str = "https://api.gbif.org/v1"    # GBIF_URL

    # ── Network exposure ──
    # The API has no authentication — it can delete, move and trash originals.
    # Browsers attach an Origin header to cross-site POSTs; api/app.py rejects
    # state-changing requests whose Origin is neither this server itself nor
    # listed here, so a random web page cannot drive the API through the
    # user's browser. The Vite dev server proxies /api, so it is same-origin
    # and needs no entry. Comma-separated, e.g. "http://192.168.1.20:5173".
    cors_origins: str = ""                    # CORS_ORIGINS

    # ── Backups ──
    # Name of a Docker container running the database (docker-compose.yml
    # names it "fernkam-db"). When set, backup/restore run pg_dump/pg_restore
    # inside that container, so the host needs no matching PostgreSQL client.
    pg_docker_container: str = ""             # PG_DOCKER_CONTAINER

    # ── Postgres per-database tuning, applied at startup (see db/index_setup.py) ──
    # These are set with ALTER DATABASE (scoped to fernKam's own database only,
    # never cluster-wide) and take effect for new connections without a restart.
    # Clear any value to "" to leave that setting untouched.
    #
    # random_page_cost: Postgres ships with 4.0, the historical cost ratio for a
    # 7200rpm spinning disk. On SSD/NVMe that badly over-penalises index scans —
    # measured on a 125k-photo library, the stock 4.0 made the planner pick a
    # Sort+GroupAggregate over a HashAggregate for duplicate detection (977 ms
    # vs 147 ms). Set to "4" if the database really is on a spinning disk.
    pg_random_page_cost: str = "1.1"          # PG_RANDOM_PAGE_COST
    # work_mem is per sort/hash operation. fernKam is a single-user desktop app,
    # so concurrency is low and a larger value is safe.
    pg_work_mem: str = "32MB"                 # PG_WORK_MEM
    # Planner hint only (allocates nothing) — roughly 50-75% of system RAM.
    # Left empty by default because it's machine-specific and can't be guessed;
    # e.g. set PG_EFFECTIVE_CACHE_SIZE=24GB on a 64 GB machine.
    pg_effective_cache_size: str = ""         # PG_EFFECTIVE_CACHE_SIZE

    # Duplicate auto-clean folder priority — comma-separated top-level album
    # path prefixes. Any album_path starting with one of `dedup_staging_folders`
    # is the lowest priority (auto-delete first); `dedup_archive_folder` is the
    # middle priority; anything else (a manually-organized/special folder) is
    # the highest priority and is always kept over the other two.
    dedup_staging_folders: str = "AB_TO_SORT,AC_SORTED"  # DEDUP_STAGING_FOLDERS
    dedup_archive_folder: str = "Ordered by Dates"       # DEDUP_ARCHIVE_FOLDER

    # The sorting pipeline's stages, in the order work flows through them.
    # Reuses the same folder names dedup already knows about; declared here so
    # the stage view and the dedup tier logic cannot drift apart.
    raw_intake_folder: str = "AA_RAW"                    # RAW_INTAKE_FOLDER
    portfolio_folder: str = "Portfolio"                  # PORTFOLIO_FOLDER

    @field_validator("thumb_cache_dir", "backup_dir")
    @classmethod
    def _anchor_to_backend(cls, v: str) -> str:
        # Relative paths used to resolve against the working directory, so
        # starting the server from the repo root instead of backend/ quietly
        # began a second, empty thumbnail cache (and regenerated every tile).
        p = Path(v)
        return str(p if p.is_absolute() else BACKEND_DIR / p)

    @model_validator(mode="after")
    def _derive_sync_url(self) -> "Settings":
        if not self.pg_url_sync:
            self.pg_url_sync = self.pg_url.replace("+asyncpg", "+psycopg2")
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()
