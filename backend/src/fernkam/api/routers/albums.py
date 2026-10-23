from __future__ import annotations

from sqlalchemy import func, select

from fastapi import APIRouter, Body
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

    roots.sort(key=lambda n: n.name)
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


@router.post("/reveal")
async def reveal_album(album_path: str = Body(..., embed=True)) -> dict:
    """Open the folder in the OS file manager. Local desktop app: the server and the person
    clicking are on the same machine, which is the only reason this is reasonable."""
    import subprocess
    import sys
    from pathlib import Path

    from fastapi import HTTPException
    from fernkam.config import get_settings

    lib = Path(get_settings().library_root).resolve()
    folder = (lib / album_path.strip("/")).resolve()
    if not folder.is_relative_to(lib) or not folder.is_dir():
        raise HTTPException(404, f"Not a folder in the library: {album_path}")
    opener = ["explorer"] if sys.platform == "win32" else ["open"] if sys.platform == "darwin" else ["xdg-open"]
    subprocess.Popen([*opener, str(folder)])
    return {"opened": str(folder)}
