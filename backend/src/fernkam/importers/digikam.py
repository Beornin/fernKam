"""
DigiKam MariaDB → fernKam PostgreSQL importer.

Mapping (in dependency order):
  AlbumRoots  → config / path prefix
  Albums      → album_path prefix on Photo
  Tags + TagsTree → tags (with ltree path)
  Images ⨝ ImageInformation → photos
  ImageMetadata → photos.exif JSONB
  ImageCopyright → photos.exif.copyright JSONB
  ImagePositions → photos.latitude / longitude / altitude
  ImageComments → photos.caption / photos.title
  ImageTags → photo_tags
  ImageTagProperties → faces
"""
from __future__ import annotations

import re
import time
from datetime import datetime, timezone
from typing import Optional

import structlog
from rich.console import Console
from rich.progress import (
    BarColumn,
    MofNCompleteColumn,
    Progress,
    SpinnerColumn,
    TaskProgressColumn,
    TextColumn,
    TimeRemainingColumn,
)
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session, sessionmaker

log = structlog.get_logger(__name__)
console = Console()


# ─────────────────────────── helpers ──────────────────────────────────────────


def _safe_path_component(name: str) -> str:
    """Convert arbitrary tag name to a valid ltree label."""
    if not name:
        return "unknown"
    # ltree labels: [A-Za-z0-9_] only, max 255 chars per label
    cleaned = re.sub(r"[^A-Za-z0-9_]", "_", name.strip())
    cleaned = re.sub(r"_+", "_", cleaned).strip("_") or "tag"
    return cleaned[:63]


def _dt_utc(val) -> Optional[datetime]:
    if val is None:
        return None
    if isinstance(val, datetime):
        if val.tzinfo is None:
            return val.replace(tzinfo=timezone.utc)
        return val
    return None


# ─────────────────────────── importer class ───────────────────────────────────


class DigiKamImporter:
    def __init__(
        self,
        mysql_url: str,
        pg_url: str,
        dry_run: bool = True,
        batch_size: int = 500,
        resume: bool = False,
    ):
        self.mysql_url = mysql_url
        self.pg_url = pg_url
        self.dry_run = dry_run
        self.batch_size = batch_size
        self.resume = resume

        self.mysql_engine = create_engine(mysql_url, pool_pre_ping=True)
        self.pg_engine = create_engine(pg_url, pool_pre_ping=True)
        self.MySQLSession: sessionmaker = sessionmaker(bind=self.mysql_engine)
        self.PGSession: sessionmaker = sessionmaker(bind=self.pg_engine, autocommit=False, autoflush=False)

        self.stats: dict[str, int] = {
            "album_roots": 0,
            "tags": 0,
            "photos": 0,
            "photo_tags": 0,
            "faces": 0,
            "skipped": 0,
            "errors": 0,
        }

    # ──────────────────────────────────────────────────────────────────────────
    # Entry point
    # ──────────────────────────────────────────────────────────────────────────

    def run(self) -> dict[str, int]:
        start = time.perf_counter()
        mode_label = "[DRY-RUN]" if self.dry_run else "[COMMIT]"
        console.rule(f"[bold cyan]fernKam import {mode_label}[/bold cyan]")

        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            BarColumn(),
            MofNCompleteColumn(),
            TaskProgressColumn(),
            TimeRemainingColumn(),
            console=console,
            transient=False,
        ) as progress:
            self._progress = progress

            with self.MySQLSession() as mysql_sess, self.PGSession() as pg_sess:
                self._mysql = mysql_sess
                self._pg = pg_sess
                try:
                    album_roots = self._import_album_roots()
                    self._import_tags()
                    self._import_photos(album_roots)
                    self._import_photo_tags()
                    self._import_faces()

                    if not self.dry_run:
                        pg_sess.commit()
                        console.print("[bold green]✓ Transaction committed[/bold green]")
                    else:
                        pg_sess.rollback()
                        console.print("[bold yellow]↩ Dry-run — rolled back[/bold yellow]")
                except Exception:
                    pg_sess.rollback()
                    raise
                finally:
                    elapsed = time.perf_counter() - start
                    self._print_summary(elapsed)

        return self.stats

    # ──────────────────────────────────────────────────────────────────────────
    # 1. Album roots → path prefix map
    # ──────────────────────────────────────────────────────────────────────────

    def _import_album_roots(self) -> dict[int, str]:
        rows = self._mysql.execute(
            text("SELECT id, label, specificPath FROM AlbumRoots WHERE status = 0")
        ).fetchall()

        roots: dict[int, str] = {}
        for r in rows:
            path = (r.specificPath or "").rstrip("/").rstrip("\\")
            roots[r.id] = path
            console.print(f"  AlbumRoot {r.id}: [italic]{r.label}[/italic] → {path}")

        self.stats["album_roots"] = len(roots)
        return roots

    # ──────────────────────────────────────────────────────────────────────────
    # 2. Tags → tags table with ltree path
    # ──────────────────────────────────────────────────────────────────────────

    def _import_tags(self) -> None:
        task = self._progress.add_task("Importing tags…", total=None)

        # Fetch all tags
        tag_rows = {
            r.id: r
            for r in self._mysql.execute(
                text("SELECT id, pid, name, icon FROM Tags ORDER BY id")
            ).fetchall()
        }

        # Build tree to compute ltree paths
        children: dict[int, list[int]] = {}
        for tid, r in tag_rows.items():
            children.setdefault(r.pid or 0, []).append(tid)

        id_to_path: dict[int, str] = {}

        def build_path(tid: int, parent_path: str) -> None:
            label = _safe_path_component(tag_rows[tid].name)
            full = f"{parent_path}.{label}" if parent_path else label
            # Deduplicate sibling labels
            base = full
            counter = 1
            while full in id_to_path.values():
                full = f"{base}_{counter}"
                counter += 1
            id_to_path[tid] = full
            for child_id in children.get(tid, []):
                build_path(child_id, full)

        for root_id in children.get(0, []):
            build_path(root_id, "")

        # Also handle tags with no parent properly — DigiKam root tag is id=0 (virtual)
        for child_id in children.get(0, []):
            if child_id not in id_to_path:
                build_path(child_id, "")

        person_tag_ids = set()
        # Identify People tag subtree (DigiKam stores confirmed faces under "_People" tag)
        for tid, r in tag_rows.items():
            if r.name.lower() in ("people", "_people", "persons"):
                # Mark this tag and all descendants
                def mark_person(pid: int) -> None:
                    person_tag_ids.add(pid)
                    for c in children.get(pid, []):
                        mark_person(c)
                mark_person(tid)

        total = len(tag_rows)
        self._progress.update(task, total=total)

        # UPSERT tags
        for i, (tid, r) in enumerate(tag_rows.items()):
            path = id_to_path.get(tid, _safe_path_component(r.name))
            parent_pg_id = self._resolve_tag_pg_id(r.pid) if r.pid else None

            self._pg.execute(
                text(
                    """
                    INSERT INTO tags (digikam_id, name, path, parent_id, icon, is_person)
                    VALUES (:did, :name, :path, :parent_id, :icon, :is_person)
                    ON CONFLICT (digikam_id) DO UPDATE
                      SET name = EXCLUDED.name,
                          path = EXCLUDED.path,
                          parent_id = EXCLUDED.parent_id,
                          icon = EXCLUDED.icon,
                          is_person = EXCLUDED.is_person
                    """
                ),
                {
                    "did": tid,
                    "name": r.name,
                    "path": path,
                    "parent_id": parent_pg_id,
                    "icon": r.icon,
                    "is_person": tid in person_tag_ids,
                },
            )
            self.stats["tags"] += 1
            self._progress.update(task, advance=1)

        console.print(f"  [green]✓[/green] {self.stats['tags']} tags")

    def _resolve_tag_pg_id(self, digikam_id: int) -> Optional[int]:
        if not digikam_id:
            return None
        row = self._pg.execute(
            text("SELECT id FROM tags WHERE digikam_id = :did"),
            {"did": digikam_id},
        ).fetchone()
        return row.id if row else None

    # ──────────────────────────────────────────────────────────────────────────
    # 3. Images + ImageInformation + metadata → photos
    # ──────────────────────────────────────────────────────────────────────────

    def _import_photos(self, album_roots: dict[int, str]) -> None:
        # Build album_id → full path map
        album_rows = self._mysql.execute(
            text("SELECT id, albumRoot, relativePath FROM Albums")
        ).fetchall()
        album_paths: dict[int, str] = {}
        for a in album_rows:
            root = album_roots.get(a.albumRoot, "")
            rel = (a.relativePath or "").lstrip("/").lstrip("\\")
            album_paths[a.id] = f"{root}/{rel}".replace("\\", "/").rstrip("/")

        total_count = self._mysql.execute(
            text("SELECT COUNT(*) FROM Images WHERE status = 1")
        ).scalar()
        task = self._progress.add_task("Importing photos…", total=total_count)

        offset = 0
        if self.resume:
            existing = self._pg.execute(text("SELECT MAX(digikam_id) FROM photos")).scalar() or 0
            # Find offset corresponding to that digikam id
            offset_row = self._mysql.execute(
                text("SELECT COUNT(*) FROM Images WHERE status = 1 AND id <= :eid"),
                {"eid": existing},
            ).scalar()
            offset = offset_row or 0
            self._progress.update(task, advance=offset)
            console.print(f"  Resume: skipping {offset} already-imported photos")

        while True:
            rows = self._mysql.execute(
                text(
                    """
                    SELECT
                        i.id AS img_id,
                        i.album AS album_id,
                        i.name AS filename,
                        i.status,
                        i.uniqueHash AS sha256,
                        i.fileSize AS file_size,
                        i.modificationDate AS modified_at,
                        ii.rating,
                        ii.creationDate AS taken_at,
                        ii.orientation,
                        ii.width,
                        ii.height,
                        ii.colorDepth AS color_depth,
                        ii.colorModel AS color_model,
                        -- ImageMetadata
                        im.make,
                        im.model,
                        im.lens,
                        im.aperture,
                        im.focalLength,
                        im.focalLength35,
                        im.exposureTime,
                        im.exposureProgram,
                        im.exposureMode,
                        im.sensitivity,
                        im.flash,
                        im.whiteBalance,
                        im.whiteBalanceColorTemperature,
                        im.meteringMode,
                        im.subjectDistance,
                        im.subjectDistanceCategory,
                        -- Position
                        ip.latitudeNumber  AS latitude,
                        ip.longitudeNumber AS longitude,
                        ip.altitude,
                        -- Comment
                        ic.comment AS caption,
                        ict.comment AS title,
                        0 AS color_label
                    FROM Images i
                    LEFT JOIN ImageInformation  ii    ON ii.imageid = i.id
                    LEFT JOIN ImageMetadata     im    ON im.imageid = i.id
                    LEFT JOIN ImagePositions    ip    ON ip.imageid = i.id
                    LEFT JOIN ImageComments     ic    ON ic.imageid = i.id AND ic.type = 1
                    LEFT JOIN ImageComments     ict   ON ict.imageid = i.id AND ict.type = 2
                    WHERE i.status = 1
                    ORDER BY i.id
                    LIMIT :lim OFFSET :off
                    """
                ),
                {"lim": self.batch_size, "off": offset},
            ).fetchall()

            if not rows:
                break

            for r in rows:
                album_path = album_paths.get(r.album_id, "")
                media_type = "video" if r.filename.lower().rsplit(".", 1)[-1] in (
                    "mp4", "mov", "avi", "mkv", "wmv", "flv", "m4v", "mts", "m2ts", "webm",
                ) else "image"

                exif: dict = {}
                for field in (
                    "make", "model", "lens", "aperture", "focalLength", "focalLength35",
                    "exposureTime", "exposureProgram", "exposureMode", "sensitivity",
                    "flash", "whiteBalance", "whiteBalanceColorTemperature",
                    "meteringMode", "subjectDistance", "subjectDistanceCategory",
                ):
                    val = getattr(r, field, None)
                    if val is not None:
                        exif[field] = val

                # Copyright
                copyright_rows = self._mysql.execute(
                    text("SELECT property, value, extraValue FROM ImageCopyright WHERE imageid = :iid"),
                    {"iid": r.img_id},
                ).fetchall()
                if copyright_rows:
                    exif["copyright"] = {cr.property: cr.value for cr in copyright_rows}

                camera_id = self._upsert_camera(r.make, r.model) if r.make or r.model else None
                lens_id = self._upsert_lens(None, r.lens) if r.lens else None

                self._pg.execute(
                    text(
                        """
                        INSERT INTO photos (
                            digikam_id, filename, album_path, sha256, file_size,
                            media_type, taken_at, modified_at, rating,
                            orientation, width, height, color_depth, color_model,
                            latitude, longitude, altitude,
                            caption, title, color_label,
                            camera_id, lens_id, exif
                        ) VALUES (
                            :digikam_id, :filename, :album_path, :sha256, :file_size,
                            :media_type, :taken_at, :modified_at, :rating,
                            :orientation, :width, :height, :color_depth, :color_model,
                            :latitude, :longitude, :altitude,
                            :caption, :title, :color_label,
                            :camera_id, :lens_id, CAST(:exif AS jsonb)
                        )
                        ON CONFLICT (digikam_id) DO UPDATE
                          SET filename    = EXCLUDED.filename,
                              album_path  = EXCLUDED.album_path,
                              sha256      = EXCLUDED.sha256,
                              file_size   = EXCLUDED.file_size,
                              media_type  = EXCLUDED.media_type,
                              taken_at    = EXCLUDED.taken_at,
                              modified_at = EXCLUDED.modified_at,
                              rating      = EXCLUDED.rating,
                              latitude    = EXCLUDED.latitude,
                              longitude   = EXCLUDED.longitude,
                              altitude    = EXCLUDED.altitude,
                              caption     = EXCLUDED.caption,
                              title       = EXCLUDED.title,
                              color_label = EXCLUDED.color_label,
                              exif        = EXCLUDED.exif
                        """
                    ),
                    {
                        "digikam_id": r.img_id,
                        "filename": r.filename,
                        "album_path": album_path,
                        "sha256": r.sha256,
                        "file_size": r.file_size,
                        "media_type": media_type,
                        "taken_at": _dt_utc(r.taken_at),
                        "modified_at": _dt_utc(r.modified_at),
                        "rating": r.rating or 0,
                        "orientation": r.orientation,
                        "width": r.width,
                        "height": r.height,
                        "color_depth": r.color_depth,
                        "color_model": r.color_model,
                        "latitude": float(r.latitude) if r.latitude is not None else None,
                        "longitude": float(r.longitude) if r.longitude is not None else None,
                        "altitude": float(r.altitude) if r.altitude is not None else None,
                        "caption": r.caption,
                        "title": r.title,
                        "color_label": r.color_label or 0,
                        "camera_id": camera_id,
                        "lens_id": lens_id,
                        "exif": __import__("json").dumps(exif) if exif else None,
                    },
                )
                self.stats["photos"] += 1
                self._progress.update(task, advance=1)

            offset += len(rows)

        console.print(f"  [green]✓[/green] {self.stats['photos']} photos")

    _camera_cache: dict[tuple, int] = {}
    _lens_cache: dict[tuple, int] = {}

    def _upsert_camera(self, make: Optional[str], model: Optional[str]) -> int:
        key = (make, model)
        if key not in self._camera_cache:
            row = self._pg.execute(
                text(
                    """
                    INSERT INTO cameras (make, model)
                    VALUES (:make, :model)
                    ON CONFLICT ON CONSTRAINT uq_cameras_make_model_serial DO UPDATE SET make = EXCLUDED.make
                    RETURNING id
                    """
                ),
                {"make": make, "model": model},
            ).fetchone()
            self._camera_cache[key] = row.id
        return self._camera_cache[key]

    def _upsert_lens(self, make: Optional[str], model: Optional[str]) -> int:
        key = (make, model)
        if key not in self._lens_cache:
            row = self._pg.execute(
                text(
                    """
                    INSERT INTO lenses (make, model)
                    VALUES (:make, :model)
                    ON CONFLICT ON CONSTRAINT uq_lenses_make_model DO UPDATE SET make = EXCLUDED.make
                    RETURNING id
                    """
                ),
                {"make": make, "model": model},
            ).fetchone()
            self._lens_cache[key] = row.id
        return self._lens_cache[key]

    # ──────────────────────────────────────────────────────────────────────────
    # 4. ImageTags → photo_tags
    # ──────────────────────────────────────────────────────────────────────────

    def _build_id_caches(self) -> tuple[dict[int, int], dict[int, int]]:
        """Build digikam_id → pg_id lookup dicts for photos and tags (eliminates N+1)."""
        photo_map = {
            r.digikam_id: r.id
            for r in self._pg.execute(text("SELECT id, digikam_id FROM photos WHERE digikam_id IS NOT NULL")).fetchall()
        }
        tag_map = {
            r.digikam_id: r.id
            for r in self._pg.execute(text("SELECT id, digikam_id FROM tags WHERE digikam_id IS NOT NULL")).fetchall()
        }
        return photo_map, tag_map

    def _import_photo_tags(self) -> None:
        total_count = self._mysql.execute(
            text(
                """
                SELECT COUNT(*) FROM ImageTags it
                JOIN Images i ON i.id = it.imageid AND i.status = 1
                """
            )
        ).scalar()
        task = self._progress.add_task("Importing photo↔tag links…", total=total_count)

        photo_map, tag_map = self._build_id_caches()

        offset = 0
        batch_rows = []
        while True:
            rows = self._mysql.execute(
                text(
                    """
                    SELECT it.imageid, it.tagid
                    FROM ImageTags it
                    JOIN Images i ON i.id = it.imageid AND i.status = 1
                    ORDER BY it.imageid, it.tagid
                    LIMIT :lim OFFSET :off
                    """
                ),
                {"lim": self.batch_size, "off": offset},
            ).fetchall()

            if not rows:
                break

            for r in rows:
                photo_pg_id = photo_map.get(r.imageid)
                tag_pg_id = tag_map.get(r.tagid)

                if not photo_pg_id or not tag_pg_id:
                    self.stats["skipped"] += 1
                    continue

                batch_rows.append({"pid": photo_pg_id, "tid": tag_pg_id})
                self.stats["photo_tags"] += 1

            # Bulk insert in chunks
            if batch_rows:
                self._pg.execute(
                    text("INSERT INTO photo_tags (photo_id, tag_id) VALUES (:pid, :tid) ON CONFLICT DO NOTHING"),
                    batch_rows,
                )
                self._progress.update(task, advance=len(batch_rows))
                batch_rows = []

            offset += len(rows)

        console.print(f"  [green]✓[/green] {self.stats['photo_tags']} photo↔tag links")

    # ──────────────────────────────────────────────────────────────────────────
    # 5. ImageTagProperties → faces
    # ──────────────────────────────────────────────────────────────────────────

    def _import_faces(self) -> None:
        """
        DigiKam face region properties (actual property names in MariaDB):
          tagRegion         → confirmed face with person tag (XML rect)
          autodetectedFace  → detected but unconfirmed
          autodetectedPerson→ AI suggested a person
          faceToTrain       → needs training data
          ignoredFace       → user ignored this detection
        """
        FACE_PROPS = ("tagRegion", "autodetectedFace", "autodetectedPerson", "faceToTrain", "ignoredFace")
        STATUS_MAP = {
            "tagRegion": "confirmed",
            "autodetectedFace": "unconfirmed",
            "autodetectedPerson": "suggestion",
            "faceToTrain": "training",
            "ignoredFace": "ignored",
        }

        total_count = self._mysql.execute(
            text(
                "SELECT COUNT(DISTINCT imageid, tagid) FROM ImageTagProperties "
                "WHERE property IN ('tagRegion','autodetectedFace','autodetectedPerson','faceToTrain','ignoredFace')"
            )
        ).scalar()
        task = self._progress.add_task("Importing face regions…", total=total_count)

        photo_map, tag_map = self._build_id_caches()

        # Fetch all face property rows
        rows = self._mysql.execute(
            text(
                """
                SELECT itp.imageid, itp.tagid, itp.property, itp.value
                FROM ImageTagProperties itp
                JOIN Images i ON i.id = itp.imageid AND i.status = 1
                WHERE itp.property IN ('tagRegion','autodetectedFace','autodetectedPerson','faceToTrain','ignoredFace')
                ORDER BY itp.imageid, itp.tagid, itp.property
                """
            )
        ).fetchall()

        # Group by (imageid, tagid) — keep highest-priority property
        from collections import defaultdict
        PRIORITY = {"tagRegion": 0, "autodetectedPerson": 1, "faceToTrain": 2, "autodetectedFace": 3, "ignoredFace": 4}
        grouped: dict[tuple, dict] = defaultdict(dict)
        for r in rows:
            grouped[(r.imageid, r.tagid)][r.property] = r.value

        batch: list[dict] = []
        for (img_id, tag_id), props in grouped.items():
            photo_pg_id = photo_map.get(img_id)
            tag_pg_id = tag_map.get(tag_id)

            if not photo_pg_id or not tag_pg_id:
                self.stats["skipped"] += 1
                continue

            # Use the highest-priority property for status + rect
            best_prop = min(props.keys(), key=lambda p: PRIORITY.get(p, 99))
            status = STATUS_MAP.get(best_prop, "unconfirmed")
            x, y, w, h = self._parse_face_rect(props.get(best_prop) or "")

            batch.append({
                "photo_id": photo_pg_id,
                "person_tag_id": tag_pg_id,
                "x": x, "y": y, "w": w, "h": h,
                "region_type": "face",
                "status": status,
                "dimg_id": img_id,
                "dtag_id": tag_id,
            })
            self.stats["faces"] += 1

            if len(batch) >= self.batch_size:
                self._pg.execute(
                    text(
                        "INSERT INTO faces (photo_id, person_tag_id, x, y, w, h, "
                        "region_type, status, digikam_image_id, digikam_tag_id) "
                        "VALUES (:photo_id, :person_tag_id, :x, :y, :w, :h, "
                        ":region_type, :status, :dimg_id, :dtag_id) ON CONFLICT DO NOTHING"
                    ),
                    batch,
                )
                self._progress.update(task, advance=len(batch))
                batch = []

        if batch:
            self._pg.execute(
                text(
                    "INSERT INTO faces (photo_id, person_tag_id, x, y, w, h, "
                    "region_type, status, digikam_image_id, digikam_tag_id) "
                    "VALUES (:photo_id, :person_tag_id, :x, :y, :w, :h, "
                    ":region_type, :status, :dimg_id, :dtag_id) ON CONFLICT DO NOTHING"
                ),
                batch,
            )
            self._progress.update(task, advance=len(batch))

        console.print(f"  [green]✓[/green] {self.stats['faces']} face regions")

    @staticmethod
    def _parse_face_rect(raw: str) -> tuple[Optional[float], ...]:
        """Parse DigiKam face rect.

        Formats seen in practice:
          XML: <rect x="1353" y="948" width="595" height="768"/>
          JSON: {"x":100,"y":200,"width":300,"height":400}
          Space-separated: "x y w h"
        """
        import json as _json
        import re as _re

        if not raw:
            return None, None, None, None

        # XML format: <rect x="..." y="..." width="..." height="..."/>
        xml_match = _re.search(
            r'<rect\s+x="([^"]+)"\s+y="([^"]+)"\s+width="([^"]+)"\s+height="([^"]+)"',
            raw,
        )
        if xml_match:
            try:
                return tuple(float(v) for v in xml_match.groups())
            except ValueError:
                pass

        # JSON format
        try:
            d = _json.loads(raw)
            return float(d["x"]), float(d["y"]), float(d["width"]), float(d["height"])
        except (ValueError, TypeError, KeyError):
            pass

        # Space-separated fallback
        parts = str(raw).split()
        if len(parts) >= 4:
            try:
                return tuple(float(p) for p in parts[:4])
            except ValueError:
                pass

        return None, None, None, None

    # ──────────────────────────────────────────────────────────────────────────
    # Summary
    # ──────────────────────────────────────────────────────────────────────────

    def _print_summary(self, elapsed: float) -> None:
        from rich.table import Table

        table = Table(title="Import Summary", show_header=True, header_style="bold cyan")
        table.add_column("Entity", style="dim")
        table.add_column("Count", justify="right")

        for key, val in self.stats.items():
            table.add_row(key.replace("_", " ").title(), str(val))

        table.add_row("Elapsed (s)", f"{elapsed:.1f}")
        console.print(table)
