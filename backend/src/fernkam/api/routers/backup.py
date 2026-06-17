"""Database backup and restore via pg_dump / pg_restore."""
from __future__ import annotations

import asyncio
import os
import shutil
import subprocess
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse

from fastapi import APIRouter
from pydantic import BaseModel

router = APIRouter()

_PG_DUMP_CANDIDATES = [
    "pg_dump",
    r"C:\Program Files\PostgreSQL\17\bin\pg_dump.exe",
    r"C:\Program Files\PostgreSQL\16\bin\pg_dump.exe",
    r"C:\Program Files\PostgreSQL\15\bin\pg_dump.exe",
    r"C:\Program Files\PostgreSQL\14\bin\pg_dump.exe",
]

_PG_RESTORE_CANDIDATES = [
    "pg_restore",
    r"C:\Program Files\PostgreSQL\17\bin\pg_restore.exe",
    r"C:\Program Files\PostgreSQL\16\bin\pg_restore.exe",
    r"C:\Program Files\PostgreSQL\15\bin\pg_restore.exe",
    r"C:\Program Files\PostgreSQL\14\bin\pg_restore.exe",
]


def _find_tool(name: str, candidates: list[str]) -> str | None:
    found = shutil.which(name)
    if found:
        return found
    for c in candidates[1:]:
        if os.path.isfile(c):
            return c
    return None


def _backup_dir() -> Path:
    from fernkam.config import get_settings
    return Path(get_settings().backup_dir)


def _parsed_db():
    from fernkam.config import get_settings
    return urlparse(get_settings().pg_url_sync)


# ── Endpoints ──────────────────────────────────────────────────────────────────

@router.post("/create")
async def create_backup() -> dict:
    """Run pg_dump and save a custom-format .dump file in a datestamped subfolder."""
    pg_dump = _find_tool("pg_dump", _PG_DUMP_CANDIDATES)
    if not pg_dump:
        return {"ok": False, "message": "pg_dump not found. Add PostgreSQL bin to your PATH."}

    backup_dir = _backup_dir()
    parsed = _parsed_db()
    now = datetime.now()
    dated_dir = backup_dir / now.strftime("%Y-%m-%d")
    dated_dir.mkdir(parents=True, exist_ok=True)
    backup_file = dated_dir / f"fernkam_{now.strftime('%H-%M-%S')}.dump"

    env = os.environ.copy()
    if parsed.password:
        env["PGPASSWORD"] = parsed.password

    cmd = [
        pg_dump,
        "-h", parsed.hostname or "localhost",
        "-p", str(parsed.port or 5432),
        "-U", parsed.username or "postgres",
        "-F", "c",
        "-f", str(backup_file),
        parsed.path.lstrip("/"),
    ]

    def _run() -> dict:
        try:
            result = subprocess.run(cmd, env=env, capture_output=True, text=True, timeout=300)
            if result.returncode == 0:
                size_kb = backup_file.stat().st_size // 1024
                return {
                    "ok": True,
                    "message": f"Backup saved ({size_kb:,} KB)",
                    "file": str(backup_file),
                    "size_kb": size_kb,
                }
            return {"ok": False, "message": f"pg_dump failed: {result.stderr.strip()}"}
        except subprocess.TimeoutExpired:
            return {"ok": False, "message": "Backup timed out after 5 minutes."}
        except Exception as exc:
            return {"ok": False, "message": f"Backup error: {exc}"}

    return await asyncio.to_thread(_run)


@router.get("/list")
async def list_backups() -> dict:
    """Return available .dump files, newest first."""
    backup_dir = _backup_dir()
    backups: list[dict] = []

    if backup_dir.is_dir():
        for date_folder in sorted(backup_dir.iterdir(), reverse=True):
            if not date_folder.is_dir():
                continue
            for fpath in sorted(date_folder.iterdir(), reverse=True):
                if fpath.suffix != ".dump":
                    continue
                time_str = fpath.stem.replace("fernkam_", "").replace("-", ":")
                backups.append({
                    "label": f"{date_folder.name}  —  {time_str}",
                    "path": str(fpath),
                    "size_kb": fpath.stat().st_size // 1024,
                    "date": date_folder.name,
                    "time": time_str,
                })

    return {"backups": backups, "backup_dir": str(backup_dir)}


class RestoreRequest(BaseModel):
    backup_path: str


@router.post("/restore")
async def restore_backup(body: RestoreRequest) -> dict:
    """Restore the database from a .dump file using pg_restore."""
    backup_dir = _backup_dir()
    try:
        Path(body.backup_path).resolve().relative_to(backup_dir.resolve())
    except ValueError:
        return {"ok": False, "message": "Invalid backup path — must be inside the backup directory."}

    if not Path(body.backup_path).is_file():
        return {"ok": False, "message": "Backup file not found."}

    pg_restore = _find_tool("pg_restore", _PG_RESTORE_CANDIDATES)
    if not pg_restore:
        return {"ok": False, "message": "pg_restore not found. Add PostgreSQL bin to your PATH."}

    parsed = _parsed_db()
    env = os.environ.copy()
    if parsed.password:
        env["PGPASSWORD"] = parsed.password

    cmd = [
        pg_restore,
        "-h", parsed.hostname or "localhost",
        "-p", str(parsed.port or 5432),
        "-U", parsed.username or "postgres",
        "-d", parsed.path.lstrip("/"),
        "--clean", "--if-exists",
        "-F", "c",
        body.backup_path,
    ]

    def _run() -> dict:
        try:
            result = subprocess.run(cmd, env=env, capture_output=True, text=True, timeout=300)
            if result.returncode == 0:
                return {"ok": True, "message": f"Restored successfully. Restart the server to reload fresh data."}
            return {"ok": False, "message": f"pg_restore failed: {result.stderr.strip()}"}
        except subprocess.TimeoutExpired:
            return {"ok": False, "message": "Restore timed out after 5 minutes."}
        except Exception as exc:
            return {"ok": False, "message": f"Restore error: {exc}"}

    return await asyncio.to_thread(_run)
