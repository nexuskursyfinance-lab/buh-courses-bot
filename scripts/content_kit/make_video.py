# -*- coding: utf-8 -*-
"""Відео 1080×1920, 18 с, 5 сцен (хук → 3 факти → бот) з мелодією бренду.
Безпечні зони: текст не правіше x=940 (справа ~140 px вільно) і не нижче y=1520 (знизу 400 px).
Запуск: python3 make_video.py <папка_виводу> <мелодія.wav> [21 22 …]"""
import os, sys, subprocess, tempfile
from render import *
from content import BRANCH as BR, GP

W, H, L, R = 1080, 1920, 110, 940
SCENE, XF = 3.8, 0.4          # 4×3.8 + 4.4 − 4×0.4 = 18 с


def frame(q, code):
    im, dr = base(W, H, BR, 64)
    header(im, dr, BR, 120, 146, k=1.15, line_to=R)
    spaced(dr, (L, 1452), q["tag"], font("b", 26), BR["color"], 3.6)
    spaced(dr, (L, 1496), code, font("r", 24), GRAY, 3)
    return im, dr


def s_hook(q, code):
    im, dr = frame(q, code)
    t, sub = q["hook"]
    y = para(dr, (L, 500), t, font("r", 66), DARK, R - L, 84)
    para(dr, (L, y + 26), sub, font("r", 38), BR["color"], R - L, 48)
    return im


def s_fact(q, code, f):
    src, lab, val, note = f
    im, dr = frame(q, code)
    spaced(dr, (L, 440), src, font("b", 27), BR["color"], 3.6)
    y = para(dr, (L, 500), lab, font("r", 60), DARK, R - L, 74) + 22
    y = para(dr, (L, y), val, font("b", 76), BR["color"], R - L, 88) + 22
    para(dr, (L, y), note, font("i", 40), GRAY, R - L, 52)
    return im


def s_cta(q, code):
    im, dr = frame(q, code)
    dr.text((L, 560), "Повний розбір", font=font("r", 86), fill=DARK)
    dr.text((L, 672), f"{code} — у Telegram-боті", font=font("r", 52), fill=DARK)
    dr.text((L, 790), "@" + BR["bot"], font=fit(dr, "@" + BR["bot"], "b", 46, R - L, 30), fill=BR["color"])
    x = spaced(dr, (L, 888), "ПОСИЛАННЯ В ПРОФІЛІ", font("r", 32), BR["color"], 6.5)
    dr.text((x + 12, 878), "↑", font=font("r", 44), fill=BR["color"])
    dr.text((L, 970), "норма · приклад з цифрами · помилки", font=font("i", 42), fill=GRAY)
    return im


def build(q, out, wav):
    code = f"ГП-{q['n']:02d}"
    scenes = [s_hook(q, code)] + [s_fact(q, code, f) for f in q["facts"]] + [s_cta(q, code)]
    with tempfile.TemporaryDirectory() as tmp:
        ins, durs = [], [SCENE] * 4 + [SCENE + 0.6]
        for i, (im, d) in enumerate(zip(scenes, durs)):
            p = os.path.join(tmp, f"s{i}.png")
            im.save(p)
            ins += ["-loop", "1", "-t", str(d), "-framerate", "30", "-i", p]
        flt, prev, off = [], "0:v", 0.0
        for i in range(1, 5):
            off += durs[i - 1] - XF
            lab = f"v{i}"
            flt.append(f"[{prev}][{i}:v]xfade=transition=fade:duration={XF}:offset={off:.2f}[{lab}]")
            prev = lab
        flt.append(f"[{prev}]format=yuv420p[v]")
        dst = os.path.join(out, code)
        os.makedirs(dst, exist_ok=True)
        path = os.path.join(dst, f"{code}_відео_9x16.mp4")
        cmd = ["ffmpeg", "-v", "error", "-y", *ins, "-i", wav, "-filter_complex", ";".join(flt),
               "-map", "[v]", "-map", "5:a", "-c:v", "libx264", "-preset", "medium", "-crf", "20",
               "-r", "30", "-c:a", "aac", "-b:a", "160k", "-t", "18", "-movflags", "+faststart", path]
        subprocess.run(cmd, check=True)
    return path


if __name__ == "__main__":
    out, wav = sys.argv[1], sys.argv[2]
    nums = [int(a) for a in sys.argv[3:]]
    for q in GP:
        if not nums or q["n"] in nums:
            print(build(q, out, wav))
