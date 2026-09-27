"""Check a video shorter than the 1 s seek still gets a thumbnail.

121 videos (phone motion clips, 0.1-0.9 s) had none, so the image models
skipped them as unreadable on every run.

Run directly: python backend/tests/test_video_thumb.py
"""
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from fernkam.thumbnails import _resolve_ffmpeg, generate_thumbnail_bytes


def main() -> None:
    ffmpeg = _resolve_ffmpeg()
    if not ffmpeg:
        print("skip - no ffmpeg")
        return
    with tempfile.TemporaryDirectory() as t:
        for secs in (0.4, 3):
            clip = Path(t) / f"clip_{secs}.mp4"
            subprocess.run([str(ffmpeg), "-f", "lavfi", "-i", f"color=c=red:s=64x64:d={secs}",
                            "-pix_fmt", "yuv420p", "-y", str(clip)], capture_output=True, check=True)
            assert generate_thumbnail_bytes(clip, "md"), secs
    print("ok - short and normal videos both get a thumbnail")


if __name__ == "__main__":
    main()
