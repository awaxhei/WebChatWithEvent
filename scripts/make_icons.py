"""
生成 PWA 图标（192 / 512）
用法：python scripts/make_icons.py
输出：static/image/icon-192.png, static/image/icon-512.png
"""
import os
from PIL import Image, ImageDraw

OUT_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "static", "image")
C1 = (99, 102, 241)    # #6366f1
C2 = (79, 70, 229)     # #4f46e5
WHITE = (255, 255, 255)
EYE = (55, 48, 163)


def _gradient(size):
    img = Image.new("RGB", (size, size), C1)
    px = img.load()
    for y in range(size):
        t = y / max(size - 1, 1)
        r = int(C1[0] + (C2[0] - C1[0]) * t)
        g = int(C1[1] + (C2[1] - C1[1]) * t)
        b = int(C1[2] + (C2[2] - C1[2]) * t)
        for x in range(size):
            px[x, y] = (r, g, b)
    return img


def draw_icon(size):
    img = _gradient(size)
    d = ImageDraw.Draw(img, "RGBA")
    u = size / 512.0  # 以 512 为设计基准缩放

    # 猫头（白色椭圆）
    head = (156 * u, 176 * u, 356 * u, 400 * u)
    d.ellipse(head, fill=WHITE)

    # 两只耳朵（三角）
    d.polygon([(176 * u, 216 * u), (166 * u, 118 * u), (250 * u, 178 * u)], fill=WHITE)
    d.polygon([(336 * u, 216 * u), (346 * u, 118 * u), (262 * u, 178 * u)], fill=WHITE)
    # 耳朵内耳（浅紫）
    inner = (198, 190, 255)
    d.polygon([(196 * u, 208 * u), (190 * u, 152 * u), (238 * u, 190 * u)], fill=inner)
    d.polygon([(316 * u, 208 * u), (322 * u, 152 * u), (274 * u, 190 * u)], fill=inner)

    # 眼睛
    d.ellipse((210 * u, 262 * u, 240 * u, 300 * u), fill=EYE)
    d.ellipse((272 * u, 262 * u, 302 * u, 300 * u), fill=EYE)
    # 高光
    d.ellipse((218 * u, 268 * u, 228 * u, 280 * u), fill=WHITE)
    d.ellipse((280 * u, 268 * u, 290 * u, 280 * u), fill=WHITE)

    # 鼻子 + 嘴
    d.polygon([(249 * u, 318 * u), (263 * u, 318 * u), (256 * u, 328 * u)], fill=(244, 114, 182))
    d.arc((230 * u, 316 * u, 256 * u, 348 * u), start=10, end=90, fill=(203, 213, 225), width=max(1, int(3 * u)))
    d.arc((256 * u, 316 * u, 282 * u, 348 * u), start=90, end=170, fill=(203, 213, 225), width=max(1, int(3 * u)))

    # 胡须
    for y1, y2, x_end in ((300, 292, 150), (322, 322, 146), (344, 352, 152)):
        d.line([(196 * u, y1 * u), (x_end * u, y2 * u)], fill=(203, 213, 225), width=max(1, int(3 * u)))
    for y1, y2, x_end in ((300, 292, 362), (322, 322, 366), (344, 352, 360)):
        d.line([(316 * u, y1 * u), (x_end * u, y2 * u)], fill=(203, 213, 225), width=max(1, int(3 * u)))

    # 腮红
    d.ellipse((196 * u, 330 * u, 224 * u, 350 * u), fill=(252, 165, 165, 110))
    d.ellipse((288 * u, 330 * u, 316 * u, 350 * u), fill=(252, 165, 165, 110))

    # 圆角遮罩（非 maskable 时视觉更贴合系统图标）
    mask = Image.new("L", (size, size), 0)
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, size - 1, size - 1), radius=int(size * 0.22), fill=255)
    out = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    out.paste(img, (0, 0), mask)
    return out


def main():
    os.makedirs(OUT_DIR, exist_ok=True)
    for size in (192, 512):
        path = os.path.join(OUT_DIR, f"icon-{size}.png")
        draw_icon(size).save(path, format="PNG", optimize=True)
        print("wrote", path, os.path.getsize(path), "bytes")


if __name__ == "__main__":
    main()
