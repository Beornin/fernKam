"""
fernKam CLI

Usage:
    fernkam setup-db [--docker] [OPTIONS]   create/connect the database, write .env, migrate
    fernkam preflight                       check database, tools and library before first run
    fernkam serve [OPTIONS]                 run the API + built frontend
"""
from __future__ import annotations

import re
import secrets
import shutil
import sys
import time
from pathlib import Path
from urllib.parse import quote

# The CLI prints ✓/✗; a Windows console on a legacy code page (PowerShell's
# default) can't encode them and `fernkam preflight` crashed on the first check.
# Same as api/app.py does for the server.
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError, OSError):
        pass


import typer
from rich.console import Console

app = typer.Typer(name="fernkam", help="fernKam — photo & video organizer CLI")
console = Console()

REQUIRED_EXTENSIONS = ("vector", "ltree", "pg_trgm")

_PGVECTOR_HINT = {
    "win32": (
        "pgvector is not installed in this PostgreSQL server. Easiest fix: use the\n"
        "    Docker database instead (`docker compose up -d`, then `fernkam setup-db --docker`).\n"
        "    To keep a native install, build it with the Visual Studio C++ build tools —\n"
        "    see 'Native PostgreSQL on Windows' in README.md."
    ),
    "darwin": "pgvector is not installed. Run: brew install pgvector",
    "linux": (
        "pgvector is not installed. Debian/Ubuntu: sudo apt install postgresql-<major>-pgvector\n"
        "    (e.g. postgresql-17-pgvector). Others: https://github.com/pgvector/pgvector#installation"
    ),
}


def _env_path() -> Path:
    from fernkam.config import ENV_FILE
    return ENV_FILE


def _update_env_file(values: "dict[str, str | None]") -> Path:
    """Set keys in backend/.env, creating it from .env.example if missing.

    Existing lines for a key are replaced in place (a commented-out
    `# KEY=...` line is uncommented), a value of None removes the key, and
    everything else in the file is left exactly as it was.
    """
    env_path = _env_path()
    if env_path.exists():
        text = env_path.read_text(encoding="utf-8")
    else:
        example = env_path.with_name(".env.example")
        text = example.read_text(encoding="utf-8") if example.exists() else ""

    pending = dict(values)
    out: list[str] = []
    for line in text.splitlines():
        m = re.match(r"^\s*(#\s*)?([A-Z][A-Z0-9_]*)\s*=", line)
        if m and m.group(2) in values:
            key, commented = m.group(2), bool(m.group(1))
            if key not in pending:
                if not commented:
                    continue  # duplicate active line: drop it
                out.append(line)
                continue
            value = pending.pop(key)
            if value is None:
                if commented:
                    out.append(line)
                continue
            out.append(f"{key}={value}")
            continue
        out.append(line)
    for key, value in pending.items():
        if value is not None:
            out.append(f"{key}={value}")
    env_path.write_text("\n".join(out).rstrip("\n") + "\n", encoding="utf-8")
    return env_path


def _apply_db_url_to_process(pg_url: str) -> None:
    """Make this process see the URL just written to .env (config.py already
    exported the old .env into os.environ at import time)."""
    import os
    from fernkam.config import get_settings
    os.environ["PG_URL"] = pg_url
    os.environ.pop("PG_URL_SYNC", None)
    get_settings.cache_clear()


def _run_migrations() -> None:
    from alembic import command
    from alembic.config import Config
    from fernkam.config import BACKEND_DIR
    console.print("\n[bold]Running database migrations…[/bold]")
    command.upgrade(Config(str(BACKEND_DIR / "alembic.ini")), "head")
    console.print("  [green]✓[/green] Schema is at the latest revision")


@app.command("setup-db")
def cmd_setup_db(
    docker: bool = typer.Option(
        False, "--docker",
        help="Use the database from docker-compose.yml (already has the user, "
             "database and extensions) instead of creating them on a native server.",
    ),
    admin_url: str = typer.Option(
        "postgresql://postgres@localhost:5432/postgres", "--admin-url",
        help="Superuser connection for a native server. You are prompted for the "
             "password if it is not in the URL and the server asks for one.",
    ),
    db_name: str = typer.Option("fernkam", "--db-name"),
    db_user: str = typer.Option("fernkam_user", "--db-user"),
    password: str = typer.Option(
        None, "--password",
        help="Password for the fernKam database user. Native: generated if the user "
             "is new. Docker: defaults to FERNKAM_DB_PASSWORD or 'fernkam'.",
    ),
    host: str = typer.Option(None, "--host", help="Database host for PG_URL (default: from --admin-url, or localhost)."),
    port: int = typer.Option(None, "--port", help="Database port (Docker default: FERNKAM_DB_PORT or 5432)."),
    library_root: str = typer.Option(None, "--library-root", help="Folder holding your photos; written to LIBRARY_ROOT in .env."),
    migrate: bool = typer.Option(True, "--migrate/--no-migrate", help="Run schema migrations when done."),
) -> None:
    """One-step database setup: user, database, extensions, backend/.env, schema.

    Safe to re-run: every step checks what already exists.
    """
    import os
    import psycopg2
    from psycopg2 import sql
    from sqlalchemy.engine import make_url

    console.rule("[bold cyan]fernKam database setup[/bold cyan]")
    env_updates: dict[str, "str | None"] = {"PG_URL_SYNC": None}  # derived from PG_URL
    if library_root:
        if not Path(library_root).is_dir():
            console.print(f"[yellow]![/yellow] LIBRARY_ROOT {library_root!r} is not a folder on this machine — writing it anyway.")
        env_updates["LIBRARY_ROOT"] = library_root.replace("\\", "/")

    if docker:
        pw = password or os.environ.get("FERNKAM_DB_PASSWORD") or "fernkam"
        db_host = host or "localhost"
        db_port = port or int(os.environ.get("FERNKAM_DB_PORT") or 5432)
        dsn = dict(host=db_host, port=db_port, dbname=db_name, user=db_user, password=pw)
        console.print(f"Waiting for the Docker database on {db_host}:{db_port}…")
        deadline = time.monotonic() + 90
        while True:
            try:
                conn = psycopg2.connect(connect_timeout=3, **dsn)
                break
            except psycopg2.OperationalError as exc:
                if time.monotonic() > deadline:
                    console.print(f"[red]✗[/red] Could not connect: {exc}".rstrip())
                    console.print("  Is the container running? `docker compose up -d` from the repo root, "
                                  "then `docker compose logs db`.")
                    raise typer.Exit(1)
                time.sleep(2)
        with conn, conn.cursor() as cur:
            cur.execute("SELECT extname FROM pg_extension")
            installed = {r[0] for r in cur.fetchall()}
        conn.close()
        missing = [e for e in REQUIRED_EXTENSIONS if e not in installed]
        if missing:
            console.print(f"[red]✗[/red] Connected, but extension(s) {', '.join(missing)} are missing. The container's "
                          "init script only runs on an empty data volume — `docker compose down -v` resets it "
                          "(this deletes the database).")
            raise typer.Exit(1)
        console.print("  [green]✓[/green] Connected; vector, ltree and pg_trgm are installed")
        env_updates["PG_DOCKER_CONTAINER"] = "fernkam-db"
    else:
        admin = make_url(admin_url)
        db_host = host or admin.host or "localhost"
        db_port = port or admin.port or 5432

        def _admin_connect(dbname: str, pw: "str | None"):
            return psycopg2.connect(
                host=admin.host or "localhost", port=admin.port or 5432,
                user=admin.username or "postgres", password=pw,
                dbname=dbname, connect_timeout=5,
            )

        admin_pw = admin.password
        try:
            conn = _admin_connect(admin.database or "postgres", admin_pw)
        except psycopg2.OperationalError as exc:
            if "password" not in str(exc).lower() or admin_pw:
                console.print(f"[red]✗[/red] Could not connect as {admin.username or 'postgres'}: {str(exc).strip()}")
                raise typer.Exit(1)
            admin_pw = typer.prompt(f"Password for PostgreSQL superuser '{admin.username or 'postgres'}'", hide_input=True)
            try:
                conn = _admin_connect(admin.database or "postgres", admin_pw)
            except psycopg2.OperationalError as exc2:
                console.print(f"[red]✗[/red] Could not connect: {str(exc2).strip()}")
                raise typer.Exit(1)
        conn.autocommit = True
        cur = conn.cursor()

        cur.execute("SHOW server_version_num")
        if int(cur.fetchone()[0]) < 130000:
            console.print("[red]✗[/red] PostgreSQL 13 or newer is required (17 recommended).")
            raise typer.Exit(1)
        cur.execute("SELECT rolsuper FROM pg_roles WHERE rolname = current_user")
        if not cur.fetchone()[0]:
            console.print("[red]✗[/red] --admin-url must be a superuser: creating the pgvector extension needs one.")
            raise typer.Exit(1)
        # Checked before creating anything, so a missing pgvector doesn't
        # leave a half-built database behind.
        cur.execute("SELECT name FROM pg_available_extensions WHERE name = ANY(%s)", (list(REQUIRED_EXTENSIONS),))
        available = {r[0] for r in cur.fetchall()}
        if "vector" not in available:
            console.print(f"[red]✗[/red] {_PGVECTOR_HINT.get(sys.platform, _PGVECTOR_HINT['linux'])}")
            raise typer.Exit(1)
        missing = [e for e in REQUIRED_EXTENSIONS if e not in available]
        if missing:
            console.print(f"[red]✗[/red] Missing contrib extension(s): {', '.join(missing)} — install your "
                          "distribution's postgresql-contrib package.")
            raise typer.Exit(1)

        cur.execute("SELECT 1 FROM pg_roles WHERE rolname = %s", (db_user,))
        role_exists = cur.fetchone() is not None
        pw = password
        if not role_exists:
            pw = pw or secrets.token_urlsafe(18)
            cur.execute(sql.SQL("CREATE ROLE {} LOGIN PASSWORD {}").format(sql.Identifier(db_user), sql.Literal(pw)))
            console.print(f"  [green]✓[/green] Created role {db_user}")
        elif pw:
            cur.execute(sql.SQL("ALTER ROLE {} WITH LOGIN PASSWORD {}").format(sql.Identifier(db_user), sql.Literal(pw)))
            console.print(f"  [green]✓[/green] Role {db_user} exists — password updated")
        else:
            # Re-run without --password: reuse the one already in .env if it works.
            from fernkam.config import get_settings
            current = make_url(get_settings().pg_url)
            if current.username == db_user and current.password:
                try:
                    psycopg2.connect(host=db_host, port=db_port, user=db_user, password=current.password,
                                     dbname="postgres", connect_timeout=5).close()
                    pw = current.password
                except psycopg2.OperationalError:
                    pass
            if not pw:
                console.print(f"[red]✗[/red] Role {db_user} already exists and its password is not in .env. "
                              "Re-run with --password <new-password> to reset it.")
                raise typer.Exit(1)
            console.print(f"  [green]✓[/green] Role {db_user} exists — reusing the password from .env")

        cur.execute("SELECT 1 FROM pg_database WHERE datname = %s", (db_name,))
        if cur.fetchone() is None:
            cur.execute(sql.SQL("CREATE DATABASE {} OWNER {}").format(sql.Identifier(db_name), sql.Identifier(db_user)))
            console.print(f"  [green]✓[/green] Created database {db_name}")
        else:
            console.print(f"  [green]✓[/green] Database {db_name} exists")
        cur.close()
        conn.close()

        # Extensions need a superuser (pgvector is not a "trusted" extension),
        # which is why migrations run as the app user cannot create it.
        conn = _admin_connect(db_name, admin_pw)
        conn.autocommit = True
        with conn.cursor() as cur:
            for ext in REQUIRED_EXTENSIONS:
                cur.execute(sql.SQL("CREATE EXTENSION IF NOT EXISTS {}").format(sql.Identifier(ext)))
            cur.execute(sql.SQL("GRANT ALL ON SCHEMA public TO {}").format(sql.Identifier(db_user)))
        conn.close()
        console.print(f"  [green]✓[/green] Extensions {', '.join(REQUIRED_EXTENSIONS)} enabled")

    pg_url = f"postgresql+asyncpg://{quote(db_user, safe='')}:{quote(pw, safe='')}@{db_host}:{db_port}/{db_name}"
    env_updates["PG_URL"] = pg_url
    env_path = _update_env_file(env_updates)
    console.print(f"  [green]✓[/green] Wrote database settings to {env_path}")
    _apply_db_url_to_process(pg_url)

    if migrate:
        _run_migrations()

    console.print("\n[bold green]Database ready.[/bold green] Next: `uv run fernkam preflight`, then `uv run fernkam serve`.")


@app.command("preflight")
def cmd_preflight() -> None:
    """Check the database, external tools and library before the first run."""
    import sqlalchemy as sa
    from fernkam.config import BACKEND_DIR, get_settings

    settings = get_settings()
    failures = 0

    def ok(msg: str) -> None:
        console.print(f"  [green]✓[/green] {msg}")

    def warn(msg: str) -> None:
        console.print(f"  [yellow]![/yellow] {msg}")

    def fail(msg: str) -> None:
        nonlocal failures
        failures += 1
        console.print(f"  [red]✗[/red] {msg}")

    console.rule("[bold cyan]fernKam Pre-flight[/bold cyan]")

    console.print("\n[bold]PostgreSQL[/bold]")
    try:
        engine = sa.create_engine(settings.pg_url_sync)
        with engine.connect() as conn:
            version = conn.execute(sa.text("SHOW server_version")).scalar()
            ok(f"Connected to {engine.url.database} on {engine.url.host}:{engine.url.port or 5432} (PostgreSQL {str(version).split()[0]})")
            installed = {r[0]: r[1] for r in conn.execute(sa.text("SELECT extname, extversion FROM pg_extension"))}
            for ext in REQUIRED_EXTENSIONS:
                if ext in installed:
                    ok(f"Extension {ext} {installed[ext]}")
                else:
                    fail(f"Extension {ext} is not installed in this database — run `fernkam setup-db`")
            try:
                rev = conn.execute(sa.text("SELECT version_num FROM alembic_version")).scalar()
            except Exception:
                rev = None
        engine.dispose()
        from alembic.config import Config
        from alembic.script import ScriptDirectory
        head = ScriptDirectory.from_config(Config(str(BACKEND_DIR / "alembic.ini"))).get_current_head()
        if rev == head:
            ok(f"Schema at latest revision ({head})")
        else:
            warn(f"Schema at {rev or 'nothing'}, latest is {head} — the server migrates on start, "
                 "or run `uv run alembic upgrade head`")
    except Exception as exc:
        fail(f"Cannot connect with PG_URL: {str(exc).strip().splitlines()[0]}")
        console.print("    Run `uv run fernkam setup-db` (native) or `uv run fernkam setup-db --docker`.")

    console.print("\n[bold]Library[/bold]")
    root = Path(settings.library_root)
    if root.is_dir():
        ok(f"LIBRARY_ROOT {root}")
    else:
        fail(f"LIBRARY_ROOT {root} does not exist — set it in {BACKEND_DIR / '.env'}")
    ok(f"Thumbnail cache {settings.thumb_cache_dir}")

    console.print("\n[bold]Tools[/bold]")
    from fernkam.metadata_sync import _et
    from fernkam.thumbnails import _resolve_ffmpeg
    et = _et()
    if et:
        ok(f"exiftool {et}")
    else:
        fail("exiftool not found — needed to read/write metadata. Install it or set EXIFTOOL_PATH")
    ff = _resolve_ffmpeg()
    if ff:
        ok(f"ffmpeg {ff}")
    else:
        warn("ffmpeg not found — video thumbnails, durations and playback transcoding are disabled. "
             "Install it or set FFMPEG_PATH")
    if settings.pg_docker_container:
        docker = shutil.which("docker")
        (ok if docker else warn)(f"Backups run inside container {settings.pg_docker_container!r}"
                                 + ("" if docker else " — but `docker` is not on PATH"))
    else:
        from fernkam.api.routers.backup import _PG_DUMP_CANDIDATES, _find_tool
        pg_dump = _find_tool("pg_dump", _PG_DUMP_CANDIDATES)
        (ok if pg_dump else warn)(f"pg_dump {pg_dump}" if pg_dump else
                                  "pg_dump not found — the Backup button will not work (or set PG_DOCKER_CONTAINER)")
    try:
        import onnxruntime as ort
        providers = ort.get_available_providers()
        if "CUDAExecutionProvider" in providers:
            ok("onnxruntime can use CUDA (face detection and CLIP run on the GPU)")
        else:
            warn(f"onnxruntime providers: {', '.join(providers)} — face detection and CLIP will run on the CPU")
    except Exception as exc:
        warn(f"onnxruntime not importable: {exc}")

    console.print("\n[bold]Frontend[/bold]")
    build = BACKEND_DIR.parent / "frontend" / "build" / "index.html"
    if build.is_file():
        ok(f"Built UI found at {build.parent}")
    else:
        warn("frontend/build not found — run `npm ci && npm run build` in frontend/ "
             "(or use `npm run dev` on :5173 during development)")

    if failures:
        console.print(f"\n[red]Pre-flight found {failures} problem(s).[/red]")
        raise typer.Exit(1)
    console.print("\n[green]Pre-flight complete.[/green]")


@app.command("export-model")
def cmd_export_model(
    key: str = typer.Argument(..., help="Model to build, e.g. bioclip2"),
) -> None:
    """Build a Tag Review model's ONNX files from its official PyTorch weights.

    Needs PyTorch, which fernKam does not install; run it through uv:
    uv run --with open-clip-torch fernkam export-model bioclip2
    """
    from fernkam.embed_models import MODELS
    from fernkam.model_export import export

    if key not in MODELS:
        console.print(f"[red]Unknown model {key!r}. Known: {', '.join(MODELS)}[/red]")
        raise typer.Exit(1)
    try:
        export(key, progress=lambda m: console.print(f"  {m}"))
    except (RuntimeError, ValueError) as exc:
        console.print(f"[red]✗ {exc}[/red]")
        raise typer.Exit(1)
    console.print("[green]✓ Done. Index it from Tag Review → Models.[/green]")


@app.command("download-model")
def cmd_download_model(
    key: str = typer.Argument(..., help="Model to download, e.g. siglip2"),
    text: bool = typer.Option(False, "--text", help="Also fetch the text tower (find photos by tag name)"),
) -> None:
    """Download a Tag Review model (the same as Tag Review → Models → Install)."""
    from fernkam.embed_models import MODELS, download

    if key not in MODELS:
        console.print(f"[red]Unknown model {key!r}. Known: {', '.join(MODELS)}[/red]")
        raise typer.Exit(1)
    try:
        download(key, progress=lambda m: console.print(f"  {m}", end="\r"))
        if text:
            download(key, text=True, progress=lambda m: console.print(f"  {m}", end="\r"))
    except ValueError as exc:
        console.print(f"[red]✗ {exc}[/red]")
        raise typer.Exit(1)
    console.print(f"\n[green]✓ {MODELS[key].label} downloaded.[/green]")


@app.command("serve")
def cmd_serve(
    # Loopback only by default: the API is unauthenticated and can move, trash
    # and delete originals. Pass --host 0.0.0.0 to reach it from another device.
    host: str = typer.Option("127.0.0.1", "--host"),
    port: int = typer.Option(8000, "--port"),
    workers: int = typer.Option(1, "--workers"),
    reload: bool = typer.Option(False, "--reload"),
) -> None:
    """Start the fernKam API server (Granian)."""
    import granian
    from granian.constants import Interfaces

    console.rule("[bold cyan]fernKam API[/bold cyan]")
    console.print(f"  [green]->[/green] http://{host}:{port}")
    console.print(f"  [green]->[/green] Docs: http://{host}:{port}/docs")

    granian.Granian(
        "fernkam.api.app:app",
        address=host,
        port=port,
        workers=workers,
        interface=Interfaces.ASGI,
        reload=reload,
    ).serve()


def main() -> None:
    app()


if __name__ == "__main__":
    main()
