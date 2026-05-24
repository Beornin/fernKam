"""
fernKam CLI

Usage:
    fernkam import-digikam [OPTIONS]
    fernkam verify
    fernkam preflight
"""
from __future__ import annotations

import typer
from rich.console import Console

app = typer.Typer(name="fernkam", help="fernKam — photo & video organizer CLI")
console = Console()


@app.command("preflight")
def cmd_preflight(
    pg_url: str = typer.Option(None, "--pg-url", help="PostgreSQL URL (overrides .env)"),
    mysql_url: str = typer.Option(None, "--mysql-url", help="MariaDB URL (overrides .env)"),
) -> None:
    """Check that required Postgres extensions and MariaDB connectivity are available."""
    from fernkam.config import get_settings
    import sqlalchemy as sa

    settings = get_settings()
    pg = pg_url or settings.pg_url_sync
    mysql = mysql_url or settings.mysql_url

    console.rule("[bold cyan]fernKam Pre-flight[/bold cyan]")

    # PostgreSQL
    console.print("\n[bold]PostgreSQL[/bold]")
    try:
        engine = sa.create_engine(pg)
        with engine.connect() as conn:
            version = conn.execute(sa.text("SELECT version()")).scalar()
            console.print(f"  [green]✓[/green] Connected: {version}")
            rows = conn.execute(
                sa.text(
                    "SELECT name, default_version FROM pg_available_extensions "
                    "WHERE name IN ('vector','postgis','ltree','pg_trgm') ORDER BY name"
                )
            ).fetchall()
            found = {r.name for r in rows}
            for r in rows:
                console.print(f"  [green]✓[/green] Extension {r.name} v{r.default_version}")
            for needed in ("vector", "postgis", "ltree", "pg_trgm"):
                if needed not in found:
                    console.print(f"  [yellow]![/yellow] Extension [bold]{needed}[/bold] NOT available — install before running migrations")
    except Exception as exc:
        console.print(f"  [red]✗[/red] Postgres error: {exc}")

    # MariaDB
    console.print("\n[bold]MariaDB / DigiKam[/bold]")
    try:
        engine = sa.create_engine(mysql)
        with engine.connect() as conn:
            version = conn.execute(sa.text("SELECT VERSION()")).scalar()
            console.print(f"  [green]✓[/green] Connected: {version}")
            counts = {
                "Images": conn.execute(sa.text("SELECT COUNT(*) FROM Images WHERE status=1")).scalar(),
                "Tags": conn.execute(sa.text("SELECT COUNT(*) FROM Tags")).scalar(),
                "ImageTagProperties (faces)": conn.execute(
                    sa.text("SELECT COUNT(DISTINCT imageid,tagid) FROM ImageTagProperties WHERE property IN ('face','faceSuggestion')")
                ).scalar(),
            }
            for name, count in counts.items():
                console.print(f"  [green]✓[/green] {name}: {count:,}")
    except Exception as exc:
        console.print(f"  [red]✗[/red] MariaDB error: {exc}")

    console.print("\nPre-flight complete.")


@app.command("import-digikam")
def cmd_import_digikam(
    mysql_url: str = typer.Option(None, "--mysql-url", envvar="MYSQL_URL"),
    pg_url: str = typer.Option(None, "--pg-url", envvar="PG_URL"),
    dry_run: bool = typer.Option(True, "--dry-run/--commit", help="Dry-run (default) or commit"),
    batch_size: int = typer.Option(500, "--batch-size"),
    resume: bool = typer.Option(False, "--resume/--no-resume", help="Skip already-imported photos"),
) -> None:
    """Import DigiKam MariaDB data into fernKam PostgreSQL."""
    from fernkam.config import get_settings
    from fernkam.importers.digikam import DigiKamImporter

    settings = get_settings()
    mysql = mysql_url or settings.mysql_url
    pg = pg_url or settings.pg_url_sync

    importer = DigiKamImporter(
        mysql_url=mysql,
        pg_url=pg,
        dry_run=dry_run,
        batch_size=batch_size,
        resume=resume,
    )
    stats = importer.run()

    if dry_run:
        console.print("\n[bold yellow]Dry-run complete. Run with --commit to persist.[/bold yellow]")
    else:
        raise typer.Exit(0)


@app.command("verify")
def cmd_verify(
    mysql_url: str = typer.Option(None, "--mysql-url", envvar="MYSQL_URL"),
    pg_url: str = typer.Option(None, "--pg-url", envvar="PG_URL"),
) -> None:
    """Compare row counts between DigiKam MariaDB and fernKam PostgreSQL."""
    from fernkam.config import get_settings
    import sqlalchemy as sa
    from rich.table import Table

    settings = get_settings()
    mysql = mysql_url or settings.mysql_url
    pg = pg_url or settings.pg_url_sync

    console.rule("[bold cyan]fernKam Verification[/bold cyan]")

    try:
        mysql_engine = sa.create_engine(mysql)
        pg_engine = sa.create_engine(pg)

        checks = [
            ("Photos", "SELECT COUNT(*) FROM Images WHERE status=1", "SELECT COUNT(*) FROM photos"),
            ("Tags", "SELECT COUNT(*) FROM Tags", "SELECT COUNT(*) FROM tags"),
            (
                "Photo↔Tag links",
                "SELECT COUNT(*) FROM ImageTags it JOIN Images i ON i.id=it.imageid AND i.status=1",
                "SELECT COUNT(*) FROM photo_tags",
            ),
            (
                "Faces",
                "SELECT COUNT(DISTINCT itp.imageid,itp.tagid) FROM ImageTagProperties itp JOIN Images i ON i.id=itp.imageid AND i.status=1 WHERE itp.property IN ('tagRegion','autodetectedFace','autodetectedPerson','faceToTrain','ignoredFace')",
                "SELECT COUNT(*) FROM faces",
            ),
        ]

        table = Table(title="Count Comparison", header_style="bold cyan")
        table.add_column("Entity")
        table.add_column("MariaDB", justify="right")
        table.add_column("PostgreSQL", justify="right")
        table.add_column("Match?", justify="center")

        with mysql_engine.connect() as mc, pg_engine.connect() as pc:
            for label, mysql_q, pg_q in checks:
                m_count = mc.execute(sa.text(mysql_q)).scalar() or 0
                p_count = pc.execute(sa.text(pg_q)).scalar() or 0
                match = "[green]✓[/green]" if m_count == p_count else "[red]✗[/red]"
                table.add_row(label, f"{m_count:,}", f"{p_count:,}", match)

        console.print(table)

        # Sample 10 random photos and show their tag counts
        console.print("\n[bold]Sample photo tag counts[/bold]")
        with mysql_engine.connect() as mc, pg_engine.connect() as pc:
            sample = mc.execute(
                sa.text(
                    "SELECT i.id, i.name, COUNT(it.tagid) AS tag_count "
                    "FROM Images i LEFT JOIN ImageTags it ON it.imageid=i.id "
                    "WHERE i.status=1 GROUP BY i.id, i.name ORDER BY RAND() LIMIT 10"
                )
            ).fetchall()

            st = Table(header_style="bold")
            st.add_column("DigiKam ID")
            st.add_column("Filename")
            st.add_column("MySQL tags", justify="right")
            st.add_column("PG tags", justify="right")

            for row in sample:
                pg_tags = pc.execute(
                    sa.text(
                        "SELECT COUNT(*) FROM photo_tags pt "
                        "JOIN photos p ON p.id=pt.photo_id AND p.digikam_id=:did"
                    ),
                    {"did": row.id},
                ).scalar() or 0
                st.add_row(str(row.id), row.name, str(row.tag_count), str(pg_tags))

            console.print(st)

    except Exception as exc:
        console.print(f"[red]✗ Verification failed: {exc}[/red]")
        raise typer.Exit(1)


@app.command("serve")
def cmd_serve(
    host: str = typer.Option("0.0.0.0", "--host"),
    port: int = typer.Option(8000, "--port"),
    workers: int = typer.Option(1, "--workers"),
    reload: bool = typer.Option(False, "--reload"),
) -> None:
    """Start the fernKam API server (Granian)."""
    import granian
    from granian.constants import Interfaces

    console.rule("[bold cyan]fernKam API[/bold cyan]")
    console.print(f"  [green]→[/green] http://{host}:{port}")
    console.print(f"  [green]→[/green] Docs: http://{host}:{port}/docs")

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
