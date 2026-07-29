import os
import time
from pathlib import Path

from fernkam.media_types import (  # noqa: F401 (re-exported for existing workflow modules)
    ALL_EXTENSIONS,
    PICTURE_EXTENSIONS,
    RAW_EXTENSIONS,
    VIDEO_EXTENSIONS,
)


def format_elapsed(start: float) -> str:
    elapsed = time.perf_counter() - start
    if elapsed < 60:
        return f"{elapsed:.2f}s"
    minutes = int(elapsed // 60)
    seconds = elapsed % 60
    return f"{minutes}m {seconds:.1f}s"


def gather_files(directory: str, extensions: set) -> list[Path]:
    """Recursively list non-empty files under `directory` matching `extensions`."""
    result = []
    for root, _, filenames in os.walk(directory):
        for name in filenames:
            p = Path(root) / name
            if p.suffix.lower() in extensions:
                try:
                    if p.stat().st_size > 0:
                        result.append(p)
                except OSError:
                    pass
    return result
