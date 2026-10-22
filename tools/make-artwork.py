#!/usr/bin/env python3
"""Render the VMBox boot artwork (blue gradient + logo) with Pillow.

    python3 tools/make-artwork.py artwork/

Produces splash.png (640x480, live boot menu) and grub.png (1920x1080,
installed system).  The PNGs are committed so building the ISO does not
need Pillow.
"""

import os
import sys

from PIL import Image, ImageDraw, ImageFilter, ImageFont

STOPS = [(0.0, (4, 18, 46)), (0.42, (10, 45, 110)), (0.78, (18, 88, 184)), (1.0, (42, 157, 244))]
FONTS = ["/usr/share/fonts/opentype/inter/Inter-Bold.otf", "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"]
FONTS_LIGHT = ["/usr/share/fonts/opentype/inter/Inter-Regular.otf", "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"]


def lerp(a, b, t):
    return tuple(int(round(x + (y - x) * t)) for x, y in zip(a, b))


def gradient_color(t):
    for (t0, c0), (t1, c1) in zip(STOPS, STOPS[1:]):
        if t <= t1:
            return lerp(c0, c1, (t - t0) / (t1 - t0))
    return STOPS[-1][1]


def background(w, h):
    img = Image.new("RGB", (w, h))
    px = img.load()
    for y in range(h):
        for x in range(w):
            px[x, y] = gradient_color(min(1.0, (x / w * 0.55 + y / h * 0.45)))
    glow = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    d = ImageDraw.Draw(glow)
    d.ellipse((int(w * 0.55), int(-h * 0.45), int(w * 1.35), int(h * 0.55)), fill=(79, 195, 247, 70))
    d.ellipse((int(-w * 0.3), int(h * 0.6), int(w * 0.45), int(h * 1.5)), fill=(30, 136, 229, 60))
    glow = glow.filter(ImageFilter.GaussianBlur(min(w, h) // 6))
    img.paste(glow, (0, 0), glow)
    return img


def font(paths, size):
    for path in paths:
        if os.path.exists(path):
            return ImageFont.truetype(path, size)
    return ImageFont.load_default()


def logo(size):
    """The VMBox cube, drawn at 4x and downsampled for smooth edges."""
    s = size * 4
    img = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    k = s / 64.0
    P = lambda pts: [(x * k, y * k) for x, y in pts]  # noqa: E731
    d.polygon(P([(32, 4), (56, 17.5), (56, 46.5), (32, 60), (8, 46.5), (8, 17.5)]), fill=(30, 111, 224))
    d.polygon(P([(32, 4), (56, 17.5), (32, 31), (8, 17.5)]), fill=(127, 211, 255))
    d.polygon(P([(32, 31), (32, 60), (8, 46.5), (8, 17.5)]), fill=(15, 79, 184))
    w = int(3.2 * k)
    d.line(P([(18, 26.5), (32, 34.4), (46, 26.5)]), fill=(255, 255, 255, 245), width=w, joint="curve")
    d.line(P([(18, 35.5), (32, 43.4), (46, 35.5)]), fill=(255, 255, 255, 140), width=w, joint="curve")
    return img.resize((size, size), Image.LANCZOS)


def compose(w, h, logo_size, title_size, subtitle, logo_y):
    img = background(w, h).convert("RGBA")
    lg = logo(logo_size)
    shadow = Image.new("RGBA", img.size, (0, 0, 0, 0))
    shadow.paste((2, 10, 40, 140), (w // 2 - logo_size // 2 + 4, logo_y + 10), lg)
    img = Image.alpha_composite(img, shadow.filter(ImageFilter.GaussianBlur(logo_size // 10)))
    img.paste(lg, (w // 2 - logo_size // 2, logo_y), lg)
    d = ImageDraw.Draw(img)
    f = font(FONTS, title_size)
    d.text((w // 2, logo_y + logo_size + title_size // 3), "VMBox", font=f, fill=(255, 255, 255), anchor="mt")
    f2 = font(FONTS_LIGHT, max(12, title_size // 3))
    d.text((w // 2, logo_y + logo_size + title_size * 1.45), subtitle, font=f2, fill=(205, 228, 255), anchor="mt")
    return img.convert("RGB")


def main(out):
    os.makedirs(out, exist_ok=True)
    # Live boot menu: the menu itself is drawn on the lower half.
    compose(640, 480, 84, 34, "Secure Desktop · VirtualBox", 40).save(os.path.join(out, "splash.png"), optimize=True)
    compose(1920, 1080, 150, 64, "Secure Desktop · VirtualBox", 120).save(os.path.join(out, "grub.png"), optimize=True)
    background(1920, 1080).save(os.path.join(out, "background.png"), optimize=True)


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "artwork")
