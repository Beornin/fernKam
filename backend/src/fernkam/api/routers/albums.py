from __future__ import annotations

from sqlalchemy import func, select, text

from fastapi import APIRouter
from fernkam.api.deps import DB
from fernkam.api.schemas import AlbumNode
from fernkam.db.models.photos import Photo

router = APIRouter()


def _build_tree(rows: list[tuple[str, int]]) -> list[AlbumNode]:
    """Build nested album tree from (album_path, count) rows."""
    nodes: dict[str, AlbumNode] = {}

    for raw_path, count in sorted(rows, key=lambda r: r[0]):
        # Skip empty paths
        if not raw_path:
            continue
        # Normalize to always start with /
        path = "/" + raw_path.strip("/") if raw_path != "/" else "/"
        # Handle root path "/"
        if path == "/":
            if path not in nodes:
                nodes[path] = AlbumNode(
                    name="Root",
                    path=path,
                    photo_count=0,
                )
            nodes[path].photo_count += count
            continue
        parts = [p for p in path.split("/") if p]
        for depth in range(len(parts)):
            node_path = "/" + "/".join(parts[: depth + 1])
            if node_path not in nodes:
                nodes[node_path] = AlbumNode(
                    name=parts[depth],
                    path=node_path,
                    photo_count=0,
                )
        nodes[path].photo_count += count

    # Propagate counts up + wire children (deepest path first so child totals are ready)
    roots: list[AlbumNode] = []
    for path, node in sorted(nodes.items(), key=lambda x: (x[0].count('/'), x[0]), reverse=True):
        parts = [p for p in path.split("/") if p]
        if len(parts) == 0:  # root "/"
            roots.append(node)
        elif len(parts) == 1:
            roots.append(node)
        else:
            parent_path = "/" + "/".join(parts[:-1])
            if parent_path in nodes:
                parent = nodes[parent_path]
                if node not in parent.children:
                    parent.children.append(node)
                parent.photo_count += node.photo_count

    # Sort children alphabetically
    def sort_children(node: AlbumNode):
        node.children.sort(key=lambda n: n.name)
        for child in node.children:
            sort_children(child)

    for root in roots:
        sort_children(root)

    return roots


@router.get("", response_model=list[AlbumNode])
async def list_albums(db: DB) -> list[AlbumNode]:
    """Return the full album tree with photo counts."""
    rows = (
        await db.execute(
            select(Photo.album_path, func.count().label("cnt"))
            .where(Photo.status == 1)
            .group_by(Photo.album_path)
        )
    ).fetchall()
    print(f"[ALBUMS] Found {len(rows)} album paths", flush=True)
    for r in rows:
        print(f"[ALBUMS] Path: '{r.album_path}', Count: {r.cnt}", flush=True)
    return _build_tree([(r.album_path, r.cnt) for r in rows])


@router.get("/debug")
async def debug_albums(db: DB) -> dict:
    """Debug endpoint to show all photos."""
    result = await db.execute(select(Photo.id, Photo.album_path, Photo.filename, Photo.status))
    photos = result.fetchall()
    return {
        "total_photos": len(photos),
        "photos": [
            {"id": p.id, "album_path": p.album_path, "filename": p.filename, "status": p.status}
            for p in photos
        ]
    }


@router.post("/fix-empty-paths")
async def fix_empty_album_paths(db: DB) -> dict:
    """Fix photos with empty album_path by setting to '/'."""
    from sqlalchemy import update as sa_update
    result = await db.execute(
        sa_update(Photo).where(Photo.album_path == "").values(album_path="/")
    )
    await db.commit()
    return {"fixed": result.rowcount}


@router.post("/rescan-paths")
async def rescan_album_paths(db: DB) -> dict:
    """Re-scan disk to fix incorrect album_paths by searching for each photo file."""
    import os
    from pathlib import Path
    from sqlalchemy import update as sa_update
    from fernkam.config import get_settings
    from fernkam.thumbnails import photo_disk_path

    settings = get_settings()
    library = Path(settings.library_root)
    result = await db.execute(select(Photo.id, Photo.album_path, Photo.filename))
    photos = result.fetchall()

    fixed = 0
    not_found = 0
    for p in photos:
        current_path = photo_disk_path(p.album_path, p.filename)
        if current_path.exists():
            continue
        # Search for the file under library root
        found = False
        for root, _, files in os.walk(library):
            if p.filename in files:
                full = Path(root) / p.filename
                rel = full.relative_to(library)
                new_album = str(rel.parent).replace("\\", "/") if rel.parent != Path(".") else "/"
                await db.execute(sa_update(Photo).where(Photo.id == p.id).values(album_path=new_album))
                fixed += 1
                found = True
                break
        if not found:
            not_found += 1

    await db.commit()
    return {"fixed": fixed, "not_found": not_found}
