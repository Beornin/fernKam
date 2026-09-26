"""Burst grouping and sharpness ranking for the cull.

Frames shot within GAP seconds of the previous frame, in the same folder, are
one moment. Review Mode shows each moment's sharpest frame first, and one key
keeps it and rejects the rest. At 20 fps that turns 60 near-identical frames
into one decision.

Frames shot with Nikon focus shift (FocusShiftShooting, read from the RAW:
PureRAW's JPGs drop it) form a focus stack instead. Each is sharp at a
different depth, so they stay together, in order, and are never ranked.
"""
from __future__ import annotations

from collections import defaultdict
from io import BytesIO
from typing import Optional

import numpy as np

GAP = 1.0   # seconds between frames of one burst; 20 fps is 0.05 s, a new moment is usually several s


def group(items: list[tuple[int, str, Optional[float]]]) -> dict[int, int]:
    """(photo_id, folder, seconds) -> {photo_id: id of its burst's first frame}.
    Undated frames are their own burst."""
    by_folder: dict[str, list[tuple[float, int]]] = defaultdict(list)
    out: dict[int, int] = {}
    for pid, folder, t in items:
        if t is None:
            out[pid] = pid
        else:
            by_folder[folder].append((t, pid))
    for frames in by_folder.values():
        prev, head = None, None
        for t, pid in sorted(frames):
            if prev is None or t - prev > GAP:
                head = pid
            out[pid], prev = head, t
    return out


def subsec(value) -> float:
    """SubSecTimeOriginal as a fraction of a second. exiftool -n turns the
    Z 9's "05" into 5, so a number below 100 is read as hundredths."""
    # ponytail: assumes 2-digit subseconds for bare numbers, as Nikon writes; other makers may differ.
    if value is None or value == "":
        return 0.0
    if isinstance(value, (int, float)):
        return value / 100 if value < 100 else value / 10 ** len(str(int(value)))
    digits = "".join(ch for ch in str(value) if ch.isdigit())
    return int(digits) / 10 ** len(digits) if digits else 0.0


def sharpness(image_bytes: bytes, tiles: int = 6) -> float:
    """Variance of the Laplacian in the sharpest of tiles x tiles regions.
    The sharpest region is the in-focus subject; the whole frame would favour
    a busy background over a sharp bird on a smooth sky."""
    from PIL import Image
    g = np.asarray(Image.open(BytesIO(image_bytes)).convert("L"), dtype=np.float32)
    lap = 4 * g[1:-1, 1:-1] - g[:-2, 1:-1] - g[2:, 1:-1] - g[1:-1, :-2] - g[1:-1, 2:]
    h, w = lap.shape
    return max(float(lap[i * h // tiles:(i + 1) * h // tiles, j * w // tiles:(j + 1) * w // tiles].var())
               for i in range(tiles) for j in range(tiles))
