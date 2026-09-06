#!/usr/bin/env python
"""save-screenshot.py <folder> <capture.png> [--crop L,T,R,B] - store a canvas capture as assets/screenshot.png.

Ensures the image is >= 1200 px wide (upscales if a capture came out smaller), optionally crops the editor
chrome away, converts to RGB PNG, and prints the final size.
"""
from __future__ import annotations

import sys
from pathlib import Path


def main() -> int:
    if len(sys.argv) < 3:
        print(__doc__)
        return 2
    try:
        from PIL import Image  # type: ignore
    except ImportError:
        print("Pillow is required: pip install pillow")
        return 1
    folder = Path(sys.argv[1])
    src = Path(sys.argv[2])
    img = Image.open(src).convert("RGB")
    if "--crop" in sys.argv:
        l, t, r, b = (int(x) for x in sys.argv[sys.argv.index("--crop") + 1].split(","))
        img = img.crop((l, t, r, b))
    if img.width < 1200:
        scale = 1200 / img.width
        img = img.resize((1200, int(img.height * scale)))
    out = folder / "assets" / "screenshot.png"
    out.parent.mkdir(parents=True, exist_ok=True)
    img.save(out, optimize=True)
    print(f"saved {out} ({img.width}x{img.height})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
