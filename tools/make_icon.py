"""生成程序图标 assets/app.ico。

设计：砖红圆角方块 + 米白衬线 "P"（PressRead 的首字母），
下方一道细横线当作「页面底线」。所有尺寸从 1024px 母版降采样，
保证 16px 下依然能看出是字母而不是一团色块。

用法：
    python tools/make_icon.py
"""

from __future__ import annotations

import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "assets" / "app.ico"

SIZE = 1024
BRICK = (176, 74, 47)        # #B04A2F
BRICK_DARK = (140, 56, 35)   # #8C3823
CREAM = (246, 242, 232)      # #F6F2E8
FONT_CANDIDATES = [
    "C:/Windows/Fonts/georgiab.ttf",
    "C:/Windows/Fonts/timesbd.ttf",
    "C:/Windows/Fonts/arialbd.ttf",
]


def _font(size: int) -> ImageFont.FreeTypeFont:
    for path in FONT_CANDIDATES:
        if Path(path).exists():
            return ImageFont.truetype(path, size)
    raise SystemExit("找不到可用字体，请修改 FONT_CANDIDATES")


def _background() -> Image.Image:
    """垂直渐变的圆角方块。"""
    grad = Image.new("RGB", (SIZE, SIZE))
    top, bottom = (196, 86, 56), (152, 62, 40)
    for y in range(SIZE):
        t = y / (SIZE - 1)
        grad.paste(
            tuple(round(top[i] + (bottom[i] - top[i]) * t) for i in range(3)),
            (0, y, SIZE, y + 1),
        )
    mask = Image.new("L", (SIZE, SIZE), 0)
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, SIZE - 1, SIZE - 1), radius=228, fill=255)
    canvas = Image.new("RGBA", (SIZE, SIZE), (0, 0, 0, 0))
    canvas.paste(grad, (0, 0), mask)
    return canvas


def build() -> Image.Image:
    img = _background()
    draw = ImageDraw.Draw(img)

    # 字母 P：先画一层深色偏移当投影，再叠米白，边缘会更有厚度
    font = _font(650)
    box = draw.textbbox((0, 0), "P", font=font)
    width, height = box[2] - box[0], box[3] - box[1]
    x = (SIZE - width) / 2 - box[0]
    y = 452 - height / 2 - box[1]

    draw.text((x, y), "P", font=font, fill=CREAM + (255,))

    # 底部横线：像书页的下缘，也给小尺寸一个稳定的视觉重心
    bar_w, bar_h = 300, 34
    draw.rounded_rectangle(
        ((SIZE - bar_w) / 2, 776, (SIZE + bar_w) / 2, 776 + bar_h),
        radius=bar_h / 2,
        fill=CREAM + (235,),
    )
    return img


def main() -> None:
    if "--probe" in sys.argv:
        master = build()
        master.resize((256, 256), Image.LANCZOS).save(ROOT / "tools" / "_icon_256.png")
        master.resize((32, 32), Image.LANCZOS).save(ROOT / "tools" / "_icon_32.png")
        print("probe ok")
        return

    OUT.parent.mkdir(parents=True, exist_ok=True)
    master = build()
    sizes = [16, 24, 32, 48, 64, 128, 256]
    master.save(OUT, format="ICO", sizes=[(s, s) for s in sizes])
    print(f"icon -> {OUT} ({OUT.stat().st_size / 1024:.1f} KB)")


if __name__ == "__main__":
    main()
