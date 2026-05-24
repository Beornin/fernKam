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

    # Thumbnails
    thumb_cache_dir: str = "data/thumbnails"
    ffmpeg_path: str = r"C:\Program Files\digiKam\ffmpeg.exe"

    # Extensions
    has_pgvector: bool = False
    has_postgis: bool = False


@lru_cache
def get_settings() -> Settings:
    return Settings()
