#!/usr/bin/env python3
"""Generate the neutral placeholder picture-in-picture card (config/ugc_pip_insert.jpg).

The UGC ad style composites a brand/product card over the video in post (the video model never
draws it). This script makes a plain, license-free placeholder locally with Pillow: warm cream
background, a terracotta frame, and the text from `ad_script.pip_card_caption` in the brand
profile. Replace config/ugc_pip_insert.jpg with your own brand card for real use.

    python scripts/make_placeholder_pip_card.py [--out config/ugc_pip_insert.jpg]
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from agents.brand import load_brand_profile  # noqa: E402

SIZE = (1140, 1150)  # same aspect as the card the assembler expects (it scales to a fixed width)
CREAM, TERRACOTTA, BROWN = (245, 236, 220), (178, 92, 62), (84, 58, 42)
_FONTS = [
    r"C:\Windows\Fonts\arialbd.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/Library/Fonts/Arial Bold.ttf",
]


def _font(size: int) -> ImageFont.ImageFont:
    for path in _FONTS:
        if Path(path).exists():
            return ImageFont.truetype(path, size)
    return ImageFont.load_default()


def make_card(text: str, out: Path) -> Path:
    img = Image.new("RGB", SIZE, CREAM)
    draw = ImageDraw.Draw(img)
    draw.rectangle([40, 40, SIZE[0] - 40, SIZE[1] - 40], outline=TERRACOTTA, width=14)
    # a simple steaming-cup glyph, drawn from primitives (no third-party art)
    cx, cy = SIZE[0] // 2, SIZE[1] // 2 - 120
    draw.rounded_rectangle([cx - 150, cy - 40, cx + 110, cy + 150], radius=40, fill=BROWN)
    draw.arc([cx + 60, cy + 10, cx + 210, cy + 120], start=270, end=90, fill=BROWN, width=22)
    for dx in (-70, 0, 70):
        draw.arc([cx + dx - 25, cy - 150, cx + dx + 25, cy - 60], start=90, end=270, fill=TERRACOTTA, width=10)
    font = _font(72)
    box = draw.textbbox((0, 0), text, font=font)
    draw.text(((SIZE[0] - (box[2] - box[0])) / 2, cy + 260), text, font=font, fill=BROWN)
    out.parent.mkdir(parents=True, exist_ok=True)
    img.save(out, quality=92)
    return out


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--out", type=Path, default=REPO_ROOT / "config" / "ugc_pip_insert.jpg")
    args = parser.parse_args()
    caption = load_brand_profile()["ad_script"]["pip_card_caption"]
    print(f"wrote {make_card(caption, args.out)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
