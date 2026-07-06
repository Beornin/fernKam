import time

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
