"""Check burst grouping and the sharpness score used to rank a burst.

Run directly: python backend/tests/test_bursts.py
"""
import sys
from io import BytesIO
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import numpy as np
from PIL import Image, ImageFilter

from fernkam.bursts import group, sharpness, subsec


def jpeg(img: Image.Image) -> bytes:
    buf = BytesIO()
    img.save(buf, "JPEG", quality=92)
    return buf.getvalue()


def main() -> None:
    # 20 fps pass, a pause, another pass; a frame in another folder; an undated frame.
    items = [(1, "s", 0.00), (2, "s", 0.05), (3, "s", 0.10), (4, "s", 4.0), (5, "s", 4.05),
             (6, "other", 0.07), (7, "s", None)]
    g = group(items)
    assert g[1] == g[2] == g[3] == 1 and g[4] == g[5] == 4, g
    assert g[6] == 6 and g[7] == 7, g            # folders don't mix; undated stands alone

    assert subsec(29) == 0.29 and subsec(5) == 0.05 and subsec("05") == 0.05 and subsec(None) == 0.0

    # A small sharp subject on a smooth sky must beat the same frame slightly
    # out of focus, even though most of the frame is identical.
    rng = np.random.default_rng(0)
    sky = np.full((480, 640), 180, np.uint8)
    sky[200:260, 300:370] = rng.integers(0, 255, (60, 70))          # textured "bird"
    sharp = Image.fromarray(sky)
    soft = sharp.filter(ImageFilter.GaussianBlur(2))
    s_sharp, s_soft = sharpness(jpeg(sharp)), sharpness(jpeg(soft))
    assert s_sharp > 3 * s_soft, (s_sharp, s_soft)

    print(f"ok - bursts group by time and folder; sharp subject scores {s_sharp / s_soft:.0f}x the soft one")


if __name__ == "__main__":
    main()
