# -*- coding: utf-8 -*-
"""Спільні елементи карток і відео NK: фон, рамка, шапка з логотипом, підвал, текст з переносами.
Колір гілки — BRANCH['color'] у content.py; фон завжди кремовий."""
import os
from PIL import Image, ImageDraw, ImageFont

HERE = os.path.dirname(os.path.abspath(__file__))
FONTS = os.environ.get("NK_FONTS", os.path.join(HERE, "fonts") if os.path.isdir(os.path.join(HERE, "fonts")) else os.path.join(HERE, "..", "..", "fonts"))
BG = (245, 241, 232)
DARK = (26, 26, 26)
GRAY = (140, 134, 124)
LIGHT = (205, 196, 180)
_LOGO = Image.open(os.path.join(HERE, "logo_mask.png")).convert("L")


def font(kind, size):
    name = {"r": "Montserrat-400.ttf", "m": "Montserrat-500.ttf", "b": "Montserrat-600.ttf",
            "l": "Montserrat-300.ttf", "i": "PTSerif-Italic.ttf"}[kind]
    return ImageFont.truetype(os.path.join(FONTS, name), size)


def mix(c, k):
    """Колір гілки, розбавлений кремовим (k=1 — чистий колір)."""
    return tuple(round(b * (1 - k) + v * k) for b, v in zip(BG, c))


def spaced(dr, xy, text, f, fill, sp):
    x, y = xy
    for ch in text:
        dr.text((x, y), ch, font=f, fill=fill)
        x += dr.textlength(ch, font=f) + sp
    return x


def swidth(dr, text, f, sp):
    return sum(dr.textlength(c, font=f) + sp for c in text) - sp if text else 0


def wrap(dr, text, f, maxw):
    words, lines, cur = text.split(" "), [], ""
    for w in words:
        t = (cur + " " + w).strip()
        if dr.textlength(t, font=f) <= maxw or not cur:
            cur = t
        else:
            lines.append(cur)
            cur = w
    if cur:
        lines.append(cur)
    return lines


def para(dr, xy, text, f, fill, maxw, lh, align="left"):
    """Абзац з переносами. Повертає y під останнім рядком."""
    x, y = xy
    for line in wrap(dr, text, f, maxw):
        lx = x if align == "left" else x + maxw - dr.textlength(line, font=f)
        dr.text((lx, y), line, font=f, fill=fill)
        y += lh
    return y


def fit(dr, text, kind, size, maxw, minsize=20):
    """Найбільший шрифт, з яким текст уміщується в один рядок."""
    while size > minsize and dr.textlength(text, font=font(kind, size)) > maxw:
        size -= 2
    return font(kind, size)


def logo(im, color, cx, cy, h):
    s = h / _LOGO.height
    m = _LOGO.resize((round(_LOGO.width * s), round(h)), Image.LANCZOS)
    im.paste(Image.new("RGB", m.size, color), (round(cx - m.width / 2), round(cy - m.height / 2)), m)


def base(W, H, br, inset, scale=1.0):
    """Фон, рамка і шапка. scale=1 — картка 1080×1350, для відео шапку передаємо окремо."""
    im = Image.new("RGB", (W, H), BG)
    dr = ImageDraw.Draw(im)
    dr.rectangle([inset, inset, W - inset - 1, H - inset - 1], outline=br["color"], width=2)
    return im, dr


def header(im, dr, br, x, y, k=1.0, line_to=None, line_color=None):
    """Логотип + назва гілки + теглайн; лінія під шапкою."""
    logo(im, br["color"], x + 44 * k, y + 44 * k, 90 * k)
    spaced(dr, (x + 114 * k, y + 22 * k), br["name"], font("b", round(23 * k)), DARK, round(4.2 * k))
    dr.text((x + 114 * k, y + 56 * k), br["tagline"], font=font("r", round(17 * k)), fill=GRAY)
    if line_to:
        ly = y + 119 * k
        dr.line([x + 10 * k, ly, line_to, ly], fill=line_color or mix(br["color"], 0.8), width=max(1, round(1.2 * k)))


def footer_card(dr, br, tag, code, page, W=1080, y=1200):
    dr.line([96, y, W - 96, y], fill=br["color"], width=1)
    spaced(dr, (96, y + 28), tag, font("b", 17), br["color"], 3.2)
    right = f"{code} · {page}"
    f = font("r", 17)
    spaced(dr, (W - 96 - swidth(dr, right, f, 2.4), y + 28), right, f, GRAY, 2.4)
