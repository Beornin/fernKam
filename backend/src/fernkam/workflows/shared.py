import time

PICTURE_EXTENSIONS = {".gif", ".png", ".bmp", ".jpg", ".jpeg", ".heic", ".dng", ".nef"}
RAW_EXTENSIONS     = {".raw", ".cr2", ".cr3", ".nef"}
VIDEO_EXTENSIONS   = {".mp4", ".mov", ".avi", ".vlc", ".wmv"}
ALL_EXTENSIONS     = PICTURE_EXTENSIONS | RAW_EXTENSIONS | VIDEO_EXTENSIONS


def format_elapsed(start: float) -> str:
    elapsed = time.perf_counter() - start
    if elapsed < 60:
        return f"{elapsed:.2f}s"
    minutes = int(elapsed // 60)
    seconds = elapsed % 60
    return f"{minutes}m {seconds:.1f}s"
