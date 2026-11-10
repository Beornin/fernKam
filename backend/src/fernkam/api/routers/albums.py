from __future__ import annotations

from sqlalchemy import func, select

from fastapi import APIRouter, Body
from fernkam.api.deps import DB
from fernkam.api.schemas import AlbumNode
from fernkam.db.models.photos import Photo

router = APIRouter()


def _build_tree(rows: list[tuple[str, int]]) -> list[AlbumNode]:
    """Nested album tree from (album_path, count) rows: each folder's count includes everything
    below it. Plain dicts and one pass; the models are built at the end (comparing nested models to
    see whether a child was already listed made this quadratic: 200 ms for 3,800 folders)."""
    own: dict[str, int] = {}      # "/a/b" -> photos directly in it
    for raw_path, count in rows:
        if not raw_path:
            continue
        path = "/" + raw_path.strip("/") if raw_path != "/" else "/"
        own[path] = own.get(path, 0) + count
    # Every folder on the way down gets a node, even with no photos of its own.
    names: dict[str, str] = {}
    for path in list(own):
        if path == "/":
            names["/"] = "Root"
            continue
        parts = path.split("/")[1:]
        for depth in range(len(parts)):
            names.setdefault("/" + "/".join(parts[: depth + 1]), parts[depth])
    total = {p: own.get(p, 0) for p in names}
    children: dict[str, list[str]] = {p: [] for p in names}
    roots: list[str] = []
    for path in sorted(names, key=lambda p: (p.count("/"), p), reverse=True):   # deepest first: child totals are ready
        parts = [x for x in path.split("/") if x]
        parent = "/" + "/".join(parts[:-1]) if len(parts) > 1 else None
        if parent is None:
            roots.append(path)
        elif parent in names:
            children[parent].append(path)
            total[parent] += total[path]
        # a parent that is missing can't happen (ancestors are added above); such a folder is left out

    def node(path: str) -> AlbumNode:
        kids = sorted(children[path], key=lambda c: names[c])
        return AlbumNode(name=names[path], path=path, photo_count=total[path], children=[node(c) for c in kids])

    return [node(r) for r in sorted(roots, key=lambda r: names[r])]


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
