"""生成 App 图标（512x512 PNG）：打开的书 + 字母 A + 圆角渐变背景。

用途：buildozer.spec 的 icon = icon.png。
用带 Pillow 的 Python 运行：
  ~/.workbuddy/binaries/python/envs/default/Scripts/python.exe tools/make_icon.py

设计要点（与 PDF 工具箱的默认黑色三角彻底区分）：
* 亮色青蓝→蓝紫渐变，圆角方块底
* 中间一本打开的白书（两页 + 书脊）
* 书页上一个大号字母 "A"（背单词/字母表的意象）
"""
from __future__ import annotations

import math
import os

from PIL import Image, ImageDraw, ImageFont

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "icon.png")
SZ = 512


def lerp(a, b, t):
    return int(a + (b - a) * t)


def gradient_bg():
    """青蓝 → 蓝紫 对角渐变。"""
    img = Image.new("RGB", (SZ, SZ))
    px = img.load()
    c0 = (34, 211, 238)   # 青
    c1 = (79, 70, 229)    # 靛蓝紫
    for y in range(SZ):
        for x in range(SZ):
            t = (x / SZ * 0.5 + y / SZ * 0.5)
            px[x, y] = (lerp(c0[0], c1[0], t),
                        lerp(c0[1], c1[1], t),
                        lerp(c0[2], c1[2], t))
    return img


def find_font(size):
    """找一个大号无衬线字体画字母 A。"""
    cands = [
        "C:/Windows/Fonts/arialbd.ttf",
        "C:/Windows/Fonts/segoeuib.ttf",
        "C:/Windows/Fonts/georgiab.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    ]
    for p in cands:
        if os.path.exists(p):
            return ImageFont.truetype(p, size)
    return ImageFont.load_default()


def main():
    img = gradient_bg().convert("RGBA")
    d = ImageDraw.Draw(img)

    # 圆角遮罩
    mask = Image.new("L", (SZ, SZ), 0)
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, SZ - 1, SZ - 1), radius=96, fill=255)

    # 柔和投影（书下方）
    shadow = Image.new("RGBA", (SZ, SZ), (0, 0, 0, 0))
    sd = ImageDraw.Draw(shadow)
    sd.ellipse((70, 360, 442, 440), fill=(0, 0, 0, 45))
    shadow = shadow.filter(__import__("PIL.ImageFilter", fromlist=["GaussianBlur"]).GaussianBlur(18))
    img = Image.alpha_composite(img, shadow)
    d = ImageDraw.Draw(img)

    # 打开的书：两片白色书页 + 中间书脊，微微倾斜的立体感
    # 左页
    d.polygon([(256, 150), (110, 178), (96, 350), (256, 322)], fill=(255, 255, 255, 245))
    # 右页
    d.polygon([(256, 150), (402, 178), (416, 350), (256, 322)], fill=(240, 248, 255, 245))
    # 书脊阴影
    d.line([(256, 152), (256, 322)], fill=(180, 200, 225, 180), width=5)
    # 页边线
    d.line([(110, 178), (96, 350)], fill=(210, 225, 240, 200), width=4)
    d.line([(402, 178), (416, 350)], fill=(200, 218, 238, 200), width=4)

    # 书页上的文字行（暗示"词条"）
    for i, y in enumerate((210, 238, 266)):
        w = 96 if i != 1 else 120
        d.rounded_rectangle((130, y, 130 + w, y + 10), radius=5, fill=(120, 150, 210, 120))
        d.rounded_rectangle((286, y, 286 + w, y + 10), radius=5, fill=(120, 150, 210, 120))

    # 中间大字母 A（覆盖在书上，突出"单词/字母"）
    font = find_font(190)
    bbox = d.textbbox((0, 0), "A", font=font)
    tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
    ax = (SZ - tw) / 2 - bbox[0]
    ay = 118 - bbox[1]
    # A 的描边（深蓝）让它在白书上醒目
    d.text((ax, ay), "A", font=font, fill=(37, 99, 235, 255),
           stroke_width=6, stroke_fill=(255, 255, 255, 255))

    # 应用圆角
    out = Image.new("RGBA", (SZ, SZ), (0, 0, 0, 0))
    out.paste(img, (0, 0), mask)
    out.convert("RGB").save(OUT, "PNG")
    print(f"图标已生成：{OUT} ({os.path.getsize(OUT)} 字节)")


if __name__ == "__main__":
    main()
