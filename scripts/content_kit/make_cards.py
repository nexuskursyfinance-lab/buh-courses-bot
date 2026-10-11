# -*- coding: utf-8 -*-
"""Три картки 1080×1350 на кожне ГП: 1 — таблиця, 2 — міф/насправді, 3 — приклад з цифрами.
Запуск: python3 make_cards.py <папка_виводу> [21 22 …]"""
import os, sys
from render import *
from content import BRANCH as BR, GP

W, H, L, R = 1080, 1350, 110, 970


def card1(q, code):
    c = q["card1"]
    im, dr = base(W, H, BR, 48)
    header(im, dr, BR, 86, 86, line_to=985)
    spaced(dr, (L, 284), c["kicker"], font("b", 22), BR["color"], 5.5)
    y = para(dr, (L, 336), c["title"], font("r", 44), DARK, R - L, 56) + 64
    for lab, val in c["rows"]:
        fv = font("b", 40)
        vl = wrap(dr, val, fv, 470)
        ll = wrap(dr, lab, font("r", 33), 360)
        top = y
        for i, line in enumerate(ll):
            dr.text((L, top + 8 + i * 42), line, font=font("r", 33), fill=DARK)
        for i, line in enumerate(vl):
            dr.text((R - dr.textlength(line, font=fv), top + i * 48), line, font=fv, fill=BR["color"])
        h = max(len(vl) * 48, len(ll) * 42 + 8)
        y = top + h + 66
        dr.line([L, y - 33, R, y - 33], fill=LIGHT, width=1)
    para(dr, (L, 1090), c["sources"], font("r", 21), GRAY, R - L, 30)
    footer_card(dr, BR, q["tag"], code, "1 / 3")
    return im


def card2(q, code):
    c = q["card2"]
    im, dr = base(W, H, BR, 48)
    header(im, dr, BR, 86, 86, line_to=985)
    spaced(dr, (L, 284), "МІФ", font("b", 22), BR["color"], 5.5)
    y = para(dr, (L, 330), c["myth"], font("r", 44), DARK, R - L, 58) + 26
    dr.line([L, y, L + 310, y], fill=BR["color"], width=2)
    spaced(dr, (L, y + 52), "НАСПРАВДІ", font("b", 22), BR["color"], 6.5)
    fb = fit(dr, c["big"], "m", 168, R - L, 80)
    dr.text((L - 4, y + 88), c["big"], font=fb, fill=DARK)
    y = y + 110 + fb.size * 1.22
    y = para(dr, (L, y), c["expl"], font("r", 32), DARK, R - L, 42) + 18
    para(dr, (L, y), c["src"], font("r", 23), GRAY, R - L, 32)
    footer_card(dr, BR, q["tag"], code, "2 / 3")
    return im


def card3(q, code):
    c = q["card3"]
    im, dr = base(W, H, BR, 48)
    header(im, dr, BR, 86, 86, line_to=985)
    y = para(dr, (L, 272), c["head"], fit(dr, c["head"], "r", 50, R - L, 34), DARK, R - L, 62)
    y = para(dr, (L, y + 6), c["sub"], font("r", 28), GRAY, R - L, 38)
    rows = c["rows"]
    rh = 142 if len(rows) == 2 else 120
    y = max(y + 60, 560 if len(rows) == 2 else 500)
    for lab, val in rows:
        fv = fit(dr, val, "b", 80 if len(rows) == 2 else 64, 560, 34)
        ll = wrap(dr, lab, font("r", 31), 330)
        vy = y + (rh - fv.size) / 2 - 18
        dr.text((R - dr.textlength(val, font=fv), vy), val, font=fv, fill=BR["color"])
        ly = y + (rh - len(ll) * 40) / 2 - 8
        for i, line in enumerate(ll):
            dr.text((L, ly + i * 40), line, font=font("r", 31), fill=GRAY)
        y += rh
        dr.line([L, y, R, y], fill=LIGHT, width=1)
    para(dr, (L, 1020), c["concl"], font("r", 28), DARK, R - L - 40, 38)
    x = spaced(dr, (L, 1126), "ПОВНИЙ РОЗБІР — У БОТІ", font("b", 21), BR["color"], 5.5)
    dr.text((x + 16, 1124), "›", font=font("b", 23), fill=BR["color"])
    footer_card(dr, BR, q["tag"], code, "3 / 3")
    return im


def main(out, nums=None):
    for q in GP:
        if nums and q["n"] not in nums:
            continue
        code = f"ГП-{q['n']:02d}"
        d = os.path.join(out, code)
        os.makedirs(d, exist_ok=True)
        for i, fn in enumerate((card1, card2, card3), 1):
            fn(q, code).save(os.path.join(d, f"{code}_картка-{i}.png"), optimize=True)
        print(code, "ok")


if __name__ == "__main__":
    main(sys.argv[1], [int(a) for a in sys.argv[2:]] or None)
