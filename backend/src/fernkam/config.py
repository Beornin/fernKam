from pydantic_settings import BaseSettings, SettingsConfigDict
from functools import lru_cache


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # PostgreSQL
    pg_url: str = "postgresql+asyncpg://fernkam_user:changeme@localhost:5432/fernkam"
    pg_url_sync: str = "postgresql+psycopg2://fernkam_user:changeme@localhost:5432/fernkam"

    # DigiKam MariaDB (import only)
    mysql_url: str = "mysql+pymysql://root@localhost:3306/digikam"

    # App
    app_name: str = "fernKam"
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

    # Face matching thresholds (override via env vars)
    auto_confirm_thresh: float = 0.85  # FERNKAM_AUTO_CONFIRM_THRESH
    suggest_thresh: float = 0.5        # FERNKAM_SUGGEST_THRESH

    # k-NN voting for auto-confirm sweep
    knn_k: int = 15                    # FERNKAM_KNN_K: confirmed neighbours per face
    knn_min_votes: int = 2             # FERNKAM_KNN_MIN_VOTES: min votes for top person
    knn_margin: float = 0.05           # FERNKAM_KNN_MARGIN: min score gap between top-2

    # Detection quality gate (0 = disabled)
    min_det_score: float = 0.5         # FERNKAM_MIN_DET_SCORE
    min_face_px: int = 30              # FERNKAM_MIN_FACE_PX: ignore crops smaller than this
    min_blur_score: float = 10.0        # FERNKAM_MIN_BLUR_SCORE: Laplacian variance (0=disabled)

    # Minimum best_match_score for a confirmed face to be used as a k-NN reference
    min_ref_score: float = 0.55        # FERNKAM_MIN_REF_SCORE

    # Person birth dates — JSON map of person name → ISO date string
    # Faces suggested for these people on photos taken BEFORE their birth date are dropped.
    # Example: PERSON_MIN_DATES='{"Alice": "2018-03-22", "Bob": "2024-05-18"}'
    person_min_dates: str = "{}"  # PERSON_MIN_DATES

    # Extensions
    has_pgvector: bool = False
    has_postgis: bool = False


@lru_cache
def get_settings() -> Settings:
    return Settings()
