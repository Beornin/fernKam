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


def _docker_container() -> str:
    from fernkam.config import get_settings
    return get_settings().pg_docker_container.strip()


def _docker_cmd(tool: str, parsed, *args: str, stdin: bool = False) -> list[str] | None:
    """pg_dump/pg_restore run inside the database container (PG_DOCKER_CONTAINER).

    Avoids needing a host PostgreSQL client at all — and a matching one, since
    pg_dump refuses to dump a newer server. Connects over the container's own
    socket, where the official image trusts local connections.
    """
    docker = shutil.which("docker")
    if not docker:
        return None
    return [docker, "exec", *(["-i"] if stdin else []), _docker_container(),
            tool, "-U", parsed.username or "postgres", *args]


# ── Endpoints ──────────────────────────────────────────────────────────────────

@router.post("/create")
async def create_backup() -> dict:
    """Run pg_dump and save a custom-format .dump file in a datestamped subfolder."""
    parsed = _parsed_db()
    dbname = parsed.path.lstrip("/")
    if _docker_container():
        cmd = _docker_cmd("pg_dump", parsed, "-F", "c", dbname)
        if not cmd:
            return {"ok": False, "message": "PG_DOCKER_CONTAINER is set but `docker` is not on PATH."}
    else:
        pg_dump = _find_tool("pg_dump", _PG_DUMP_CANDIDATES)
        if not pg_dump:
            return {"ok": False, "message": "pg_dump not found. Add PostgreSQL bin to your PATH, "
                                            "or set PG_DOCKER_CONTAINER if the database runs in Docker."}
        cmd = None

    backup_dir = _backup_dir()
    now = datetime.now()
    dated_dir = backup_dir / now.strftime("%Y-%m-%d")
    dated_dir.mkdir(parents=True, exist_ok=True)
    backup_file = dated_dir / f"fernkam_{now.strftime('%H-%M-%S')}.dump"

    env = os.environ.copy()
    if parsed.password:
        env["PGPASSWORD"] = parsed.password

    if cmd is None:
        cmd = [
            pg_dump,
            "-h", parsed.hostname or "localhost",
            "-p", str(parsed.port or 5432),
            "-U", parsed.username or "postgres",
            "-F", "c",
            "-f", str(backup_file),
            dbname,
        ]
        dump_to_stdout = False
    else:
        dump_to_stdout = True  # the dump is written inside the container's stdout, not a host path

    def _run() -> dict:
        try:
            if dump_to_stdout:
                with open(backup_file, "wb") as out:
                    result = subprocess.run(cmd, env=env, stdout=out, stderr=subprocess.PIPE, timeout=300)
                result.stderr = result.stderr.decode(errors="replace")
                if result.returncode != 0:
                    backup_file.unlink(missing_ok=True)
            else:
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

    parsed = _parsed_db()
    dbname = parsed.path.lstrip("/")
    env = os.environ.copy()
    if parsed.password:
        env["PGPASSWORD"] = parsed.password

    if _docker_container():
        cmd = _docker_cmd("pg_restore", parsed, "-d", dbname, "--clean", "--if-exists", "-F", "c", stdin=True)
        if not cmd:
            return {"ok": False, "message": "PG_DOCKER_CONTAINER is set but `docker` is not on PATH."}
        restore_from_stdin = True
    else:
        pg_restore = _find_tool("pg_restore", _PG_RESTORE_CANDIDATES)
        if not pg_restore:
            return {"ok": False, "message": "pg_restore not found. Add PostgreSQL bin to your PATH, "
                                            "or set PG_DOCKER_CONTAINER if the database runs in Docker."}
        cmd = [
            pg_restore,
            "-h", parsed.hostname or "localhost",
            "-p", str(parsed.port or 5432),
            "-U", parsed.username or "postgres",
            "-d", dbname,
            "--clean", "--if-exists",
            "-F", "c",
            body.backup_path,
        ]
        restore_from_stdin = False

    def _run() -> dict:
        list_path = None
        try:
            if restore_from_stdin:
                with open(body.backup_path, "rb") as src:
                    result = subprocess.run(cmd, env=env, stdin=src, capture_output=True, timeout=300)
                result.stderr = result.stderr.decode(errors="replace")
            else:
                # Leave extensions out of the restore. They belong to the
                # superuser that created them (`fernkam setup-db`), so --clean's
                # DROP EXTENSION fails for the app's own non-superuser role and
                # the whole restore reported failure; they already exist in
                # the target database anyway.
                toc = subprocess.run([pg_restore, "-l", body.backup_path],
                                     capture_output=True, text=True, timeout=60)
                if toc.returncode == 0:
                    import re
                    import tempfile
                    keep = [ln for ln in toc.stdout.splitlines() if not re.search(r"\bEXTENSION\b", ln)]
                    fd, list_path = tempfile.mkstemp(suffix=".list")
                    with os.fdopen(fd, "w", encoding="utf-8") as fh:
                        fh.write("\n".join(keep) + "\n")
                    cmd.insert(-1, f"--use-list={list_path}")
                result = subprocess.run(cmd, env=env, capture_output=True, text=True, timeout=300)
            if result.returncode == 0:
                return {"ok": True, "message": "Restored successfully. Restart the server to reload fresh data."}
            return {"ok": False, "message": f"pg_restore failed: {result.stderr.strip()}"}
        except subprocess.TimeoutExpired:
            return {"ok": False, "message": "Restore timed out after 5 minutes."}
        except Exception as exc:
            return {"ok": False, "message": f"Restore error: {exc}"}
        finally:
            if list_path:
                os.unlink(list_path)

    return await asyncio.to_thread(_run)
