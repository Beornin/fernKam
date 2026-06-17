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
    backup_dir: str = r"C:\Users\Ben\Documents\Database Backups\fernKam"

    # Thumbnails
    thumb_cache_dir: str = "data/thumbnails"
    ffmpeg_path: str = r"C:\Program Files\digiKam\ffmpeg.exe"

    # Face matching thresholds (override via env vars)
    auto_confirm_thresh: float = 0.8   # FERNKAM_AUTO_CONFIRM_THRESH
    suggest_thresh: float = 0.50        # FERNKAM_SUGGEST_THRESH

    # k-NN voting for auto-confirm sweep
    knn_k: int = 15                    # FERNKAM_KNN_K: confirmed neighbours per face
    knn_min_votes: int = 2             # FERNKAM_KNN_MIN_VOTES: min votes for top person
    knn_margin: float = 0.05           # FERNKAM_KNN_MARGIN: min score gap between top-2

    # Detection quality gate (0 = disabled)
    min_det_score: float = 0.0         # FERNKAM_MIN_DET_SCORE

    # Extensions
    has_pgvector: bool = False
    has_postgis: bool = False


@lru_cache
def get_settings() -> Settings:
    return Settings()
