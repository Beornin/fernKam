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

    for path, count in sorted(rows, key=lambda r: r[0]):
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
    for path, node in sorted(nodes.items(), reverse=True):
        parts = [p for p in path.split("/") if p]
        if len(parts) == 1:
            roots.append(node)
        else:
            parent_path = "/" + "/".join(parts[:-1])
            if parent_path in nodes:
                parent = nodes[parent_path]
                if node not in parent.children:
                    parent.children.append(node)
                parent.photo_count += node.photo_count

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
    return _build_tree([(r.album_path, r.cnt) for r in rows])
