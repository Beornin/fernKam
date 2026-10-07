"""Rotate / flip writes the orientation tag so the picture shows turned, pixels untouched.

A left-heavy JPEG gets each turn written with exiftool exactly as the endpoint
writes it; Pillow's exif_transpose (how thumbnails are drawn) must then show
the same picture as turning the pixels directly. A short video gets its
QuickTime Rotation tag turned. No database.

Run directly: python backend/tests/test_orient.py
"""
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from PIL import Image, ImageOps

from fernkam.api.routers.photos import ORIENT_OPS, VIDEO_DEGREES, orientation_after
from fernkam.metadata_sync import _get_et_session


def main() -> None:
    et = _get_et_session()
    assert et, "exiftool not found"
    with tempfile.TemporaryDirectory() as td:
        src = Path(td) / "a.jpg"
        # A block in the top-left corner: every turn and flip moves it somewhere else.
        img = Image.new("RGB", (60, 40), "white")
        img.paste((255, 0, 0), (0, 0, 20, 10))
        img.save(src, quality=100)
        base = ImageOps.exif_transpose(Image.open(src)).convert("L")
        orient = 1
        for op in ["right", "right", "flip_h", "left", "180", "flip_v", "left"]:
            before = ImageOps.exif_transpose(Image.open(src)).convert("L")
            orient = orientation_after(orient, op)
            out = et.execute(["-n", f"-Orientation={orient}", "-overwrite_original", str(src)])
            assert out and "1 image files updated" in out, out
            shown = ImageOps.exif_transpose(Image.open(src)).convert("L")
            want = before.transpose(Image.Transpose[ORIENT_OPS[op]])
            assert shown.size == want.size and shown.tobytes() == want.tobytes(), (op, orient)
        assert base.size == (60, 40)

        ffmpeg = shutil.which("ffmpeg")
        if ffmpeg:
            vid = Path(td) / "v.mp4"
            subprocess.run([ffmpeg, "-loglevel", "error", "-f", "lavfi", "-i", "color=c=red:s=64x32:d=1",
                            "-pix_fmt", "yuv420p", str(vid)], check=True)
            rot = 0
            for op in ["right", "180"]:
                rot = (rot + VIDEO_DEGREES[op]) % 360
                out = et.execute([f"-Rotation={rot}", "-overwrite_original", str(vid)])
                assert out and "1 image files updated" in out, out
                assert et.execute(["-n", "-s3", "-Rotation", str(vid)]).strip() == str(rot)
    print("ok - rotate/flip: orientation tag shows the picture turned as asked; video rotation tag too")


if __name__ == "__main__":
    main()
