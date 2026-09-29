"""
pdf_nk.py — генератор PDF «Гарячих питань» у фірмовому стилі NK
(чорне + золото, Montserrat + PT Serif, номери розділів у розірваному колі,
ліве поле 28 мм під підшивку, «стор. X з Y», водяний знак з ID покупця).

Вхід: словник питання з data/gp_content.json (поле content_json у БД).
Вихід: (BytesIO, order_ref).
"""
import hashlib
import os
from datetime import datetime
from io import BytesIO

from reportlab.lib import colors
from reportlab.lib.enums import TA_JUSTIFY
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.pdfmetrics import registerFontFamily
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen import canvas as rl_canvas
from reportlab.platypus import (BaseDocTemplate, Flowable, Frame, KeepTogether, PageTemplate,
                                Paragraph, Spacer, Table, TableStyle)

FD = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fonts")
_REG = False


def _register():
    global _REG
    if _REG:
        return
    pdfmetrics.registerFont(TTFont("NKSerif", os.path.join(FD, "PTSerif-Regular.ttf")))
    pdfmetrics.registerFont(TTFont("NKSerifB", os.path.join(FD, "PTSerif-Bold.ttf")))
    pdfmetrics.registerFont(TTFont("NKSerifI", os.path.join(FD, "PTSerif-Italic.ttf")))
    for w in (300, 400, 500, 600):
        pdfmetrics.registerFont(TTFont(f"M{w}", os.path.join(FD, f"Montserrat-{w}.ttf")))
    registerFontFamily("NKSerif", normal="NKSerif", bold="NKSerifB", italic="NKSerifI", boldItalic="NKSerifB")
    registerFontFamily("M400", normal="M400", bold="M600", italic="M400", boldItalic="M600")
    _REG = True


INK = colors.HexColor("#1A1A1A")
GOLD = colors.HexColor("#A6844A")
IVORY = colors.HexColor("#F7F2E8")
GREY = colors.HexColor("#6A6660")
RULE = colors.HexColor("#DCD5C8")

PW, PH = A4
LM, RM = 28 * mm, 16 * mm
W = PW - LM - RM

EXTRA_PRICE = lambda n: 99 if n == 0 else (149 if n <= 2 else 199)


def _styles(fs=9.8):
    S = lambda **k: ParagraphStyle("x", **k)
    return {
        "body": S(fontName="NKSerif", fontSize=fs, leading=fs * 1.42, textColor=INK, alignment=TA_JUSTIFY, spaceAfter=5),
        "bodyL": S(fontName="NKSerif", fontSize=fs, leading=fs * 1.42, textColor=INK, spaceAfter=3),
        "small": S(fontName="M400", fontSize=7.9, leading=11, textColor=GREY),
        "title": S(fontName="M500", fontSize=18.5, leading=24, textColor=INK),
        "kicker": S(fontName="M600", fontSize=7.6, leading=10, textColor=GOLD),
        "sub": S(fontName="M600", fontSize=8.8, leading=12.5, textColor=INK, spaceBefore=3, spaceAfter=2),
        "cell": S(fontName="M400", fontSize=8.5, leading=12, textColor=INK),
        "cellb": S(fontName="M600", fontSize=8.5, leading=12, textColor=INK),
        "cellh": S(fontName="M600", fontSize=7.4, leading=10, textColor=GOLD),
        "quote": S(fontName="NKSerifI", fontSize=fs, leading=fs * 1.42, textColor=INK),
        "answer": S(fontName="NKSerif", fontSize=10.5, leading=15, textColor=INK),
        "label": S(fontName="M600", fontSize=7.2, leading=10, textColor=GOLD),
    }


def draw_nk(c, cx, cy, size, color):
    """Векторний логотип NK."""
    k = size / 800.0
    X = lambda x: cx + (x - 600) * k
    Y = lambda y: cy - (y - 585) * k
    c.saveState(); c.setStrokeColor(color); c.setLineWidth(max(22 * k, 0.55))
    r = 385 * k
    c.arc(cx - r, cy - r, cx + r, cy + r, startAng=-33, extent=128)
    c.arc(cx - r, cy - r, cx + r, cy + r, startAng=101, extent=219)
    for a, b, d, e in [(530, 213, 530, 245), (890, 843, 870, 862), (368, 440, 368, 760), (368, 440, 566, 760),
                       (566, 760, 566, 208), (566, 208, 592, 208), (626, 440, 626, 760), (626, 612, 850, 440),
                       (645, 596, 905, 802)]:
        c.line(X(a), Y(b), X(d), Y(e))
    c.restoreState()


class Section(Flowable):
    def __init__(self, num, text):
        super().__init__(); self.num, self.text = num, text

    def wrap(self, aw, ah):
        self.w = aw; return aw, 10 * mm

    def draw(self):
        c = self.canv
        r = 3.9 * mm; cx, cy = r + 0.3 * mm, 3.9 * mm
        c.setStrokeColor(GOLD); c.setLineWidth(0.7)
        c.arc(cx - r, cy - r, cx + r, cy + r, startAng=100, extent=320)
        c.line(cx + 0.6 * mm, cy + r + 0.9 * mm, cx + 0.6 * mm, cy + r - 1.6 * mm)
        c.setFillColor(GOLD); c.setFont("M400", 10); c.drawCentredString(cx, cy - 1.25 * mm, str(self.num))
        x = 2 * r + 4 * mm; t = self.text.upper(); tw = 0
        c.setFillColor(INK); c.setFont("M600", 9.6)
        for ch in t:
            c.drawString(x + tw, cy - 1.2 * mm, ch); tw += pdfmetrics.stringWidth(ch, "M600", 9.6) + 0.9
        c.setStrokeColor(RULE); c.setLineWidth(0.45); c.line(x + tw + 3 * mm, cy, self.w, cy)


def _box(items, bg=None, bar=None, pad=10, border=None):
    t = Table([[items]], colWidths=[W])
    s = [("LEFTPADDING", (0, 0), (-1, -1), pad + (2 if bar else 0)), ("RIGHTPADDING", (0, 0), (-1, -1), pad),
         ("TOPPADDING", (0, 0), (-1, -1), pad - 3), ("BOTTOMPADDING", (0, 0), (-1, -1), pad - 1)]
    if bg: s.append(("BACKGROUND", (0, 0), (-1, -1), bg))
    if bar: s.append(("LINEBEFORE", (0, 0), (0, -1), 2.2, bar))
    if border: s.append(("BOX", (0, 0), (-1, -1), 0.6, border))
    t.setStyle(TableStyle(s)); return t


def _table(data, widths):
    t = Table(data, colWidths=widths, repeatRows=1)
    t.setStyle(TableStyle([("LINEBELOW", (0, 0), (-1, 0), 1.1, GOLD), ("LINEABOVE", (0, 0), (-1, 0), 0.5, RULE),
                           ("LINEBELOW", (0, 1), (-1, -1), 0.4, RULE), ("VALIGN", (0, 0), (-1, -1), "TOP"),
                           ("TOPPADDING", (0, 0), (-1, -1), 3.6), ("BOTTOMPADDING", (0, 0), (-1, -1), 3.6),
                           ("LEFTPADDING", (0, 0), (-1, -1), 5), ("RIGHTPADDING", (0, 0), (-1, -1), 5)]))
    return t


def _make_canvas(buyer, date, code):
    class NumberedCanvas(rl_canvas.Canvas):
        def __init__(self, *a, **k):
            super().__init__(*a, **k); self._saved = []

        def showPage(self):
            self._saved.append(dict(self.__dict__)); self._startPage()

        def save(self):
            n = len(self._saved)
            for stt in self._saved:
                self.__dict__.update(stt); self._furniture(n); super().showPage()
            super().save()

        def _furniture(self, total):
            c = self; w, h = A4
            c.saveState(); c.setFillColor(colors.black); c.setFillAlpha(0.035); c.setFont("M600", 24)
            c.translate(w / 2 + 6 * mm, h / 2); c.rotate(38)
            for dy in (-250, 0, 250): c.drawCentredString(0, dy, f"{buyer} · {date}")
            c.restoreState()
            top = h - 13 * mm
            draw_nk(c, LM + 4.8 * mm, top + 0.3 * mm, 11.5 * mm, GOLD)
            c.setFillColor(INK); c.setFont("M600", 8.2); x = LM + 12 * mm
            for ch in "БУХГАЛТЕРСЬКІ ЛАЙФХАКИ":
                c.drawString(x, top + 1.9 * mm, ch); x += pdfmetrics.stringWidth(ch, "M600", 8.2) + 1.1
            c.setFillColor(GREY); c.setFont("M400", 7.2)
            c.drawString(LM + 12 * mm, top - 1.4 * mm, "авторські матеріали для бухгалтерів")
            c.setFillColor(INK); c.setFont("M600", 8.2); c.drawRightString(w - RM, top + 2.1 * mm, f"№ {code}")
            c.setFillColor(GREY); c.setFont("M400", 7.2); c.drawRightString(w - RM, top - 1.4 * mm, f"редакція від {date}")
            c.setStrokeColor(GOLD); c.setLineWidth(0.6); c.line(LM, top - 5.6 * mm, w - RM, top - 5.6 * mm)
            c.setLineWidth(0.4); c.line(LM, 16 * mm, w - RM, 16 * mm)
            c.setFillColor(GREY); c.setFont("M400", 6.9)
            c.drawString(LM, 12 * mm, f"Примірник для: {buyer} · згенеровано {date}")
            c.setFillColor(INK); c.setFont("M600", 7.2)
            c.drawRightString(w - RM, 12 * mm, f"{code} · стор. {self._pageNumber} з {total}")
            c.setFillColor(GREY); c.setFont("M400", 5.9)
            c.drawString(LM, 8.5 * mm, "© Бухгалтерські лайфхаки. Об'єкт авторського права (ст. 8, 15 Закону України «Про авторське право і суміжні права»).")
            c.drawString(LM, 5.8 * mm, "Розповсюдження та перепродаж без згоди правовласника заборонені.")
            c.setStrokeColor(RULE); c.setLineWidth(0.4)
            for y in (h / 2 + 40 * mm, h / 2 - 40 * mm): c.circle(11 * mm, y, 1.6 * mm)
    return NumberedCanvas


def _story(g, st, gap, glue, content_date):
    P = lambda t, s="body": Paragraph(t, st[s])
    price = g.get("price") or EXTRA_PRICE(len(g.get("extras", [])))

    def block(b):
        t = b[0]
        if t == "p": return [P(b[1])]
        if t == "pl": return [P(b[1], "bodyL")]
        if t == "small": return [P(b[1], "small")]
        if t == "sub": return [P("§ " + b[1], "sub")]
        if t == "quote": return [_box([P("«" + b[1] + "»", "quote"), P(b[2], "small")], bar=RULE, pad=8), Spacer(1, 4)]
        if t == "list": return [P("—  " + x, "bodyL") for x in b[1]]
        if t == "table":
            data = [[P(h, "cellh") for h in b[1]]]
            for r in b[2]:
                data.append([P(c, "cellb" if (i == 0 and len(b) > 4 and b[4]) else "cell") for i, c in enumerate(r)])
            return [_table(data, [W * x for x in b[3]]), Spacer(1, 4)]
        if t == "steps":
            sd = [[P(f"<font name='M300' color='#A6844A' size='13'>{i}</font>", "cell"), P(x, "bodyL")] for i, x in enumerate(b[1], 1)]
            tt = Table(sd, colWidths=[8 * mm, W - 8 * mm])
            tt.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP"), ("LEFTPADDING", (0, 0), (-1, -1), 0),
                                    ("TOPPADDING", (0, 0), (-1, -1), 1), ("BOTTOMPADDING", (0, 0), (-1, -1), 2)]))
            return [tt]
        if t == "calc":
            calc = Table([[P(a, "cell"), P(x, "cell"), P(f"<b>{c}</b>", "cellb")] for a, x, c in b[3]],
                         colWidths=[W * 0.50, W * 0.24, W * 0.18])
            ls = [("LEFTPADDING", (0, 0), (-1, -1), 0), ("TOPPADDING", (0, 0), (-1, -1), 3.5), ("BOTTOMPADDING", (0, 0), (-1, -1), 3.5)]
            if len(b[3]) > 1: ls.append(("LINEBELOW", (0, 0), (-1, -2), 0.4, RULE))
            calc.setStyle(TableStyle(ls))
            items = [P(b[1], "label"), Spacer(1, 3), P(b[2], "bodyL"), Spacer(1, 2), calc]
            if b[4]: items += [Spacer(1, 5), P(b[4], "bodyL")]
            return [_box(items, border=GOLD, pad=11), Spacer(1, 6)]
        if t == "exbox":
            return [_box([P(b[1], "label"), Spacer(1, 3)] + [P(x, "bodyL") for x in b[2]], border=GOLD, pad=11), Spacer(1, 6)]
        return []

    s = [P(g["kicker"], "kicker"), Spacer(1, 4), P(g["title"], "title"), Spacer(1, 8)]
    card = Table([[P(f"<b>Для кого</b><br/>{g['who']}", "small"), P(f"<b>Норми</b><br/>{g['norms']}", "small"),
                   P(f"<b>Формат</b><br/>{price} грн", "small"), P(f"<b>Актуально на</b><br/>{content_date}", "small")]],
                 colWidths=[W * 0.22, W * 0.40, W * 0.18, W * 0.20])
    card.setStyle(TableStyle([("LINEABOVE", (0, 0), (-1, 0), 0.4, RULE), ("LINEBELOW", (0, 0), (-1, 0), 0.4, RULE),
                              ("LINEAFTER", (0, 0), (2, 0), 0.4, RULE), ("VALIGN", (0, 0), (-1, -1), "TOP"),
                              ("LEFTPADDING", (0, 0), (0, 0), 0), ("LEFTPADDING", (1, 0), (-1, 0), 7),
                              ("TOPPADDING", (0, 0), (-1, -1), 5), ("BOTTOMPADDING", (0, 0), (-1, -1), 6)]))
    s += [card, Spacer(1, 8), Section(1, "Ситуація"), P(g["situation"]), Spacer(1, 5), Section(2, "Коротка відповідь"),
          _box([P("КОРОТКО", "label"), Spacer(1, 2), P(g["answer"], "answer")], bg=IVORY, bar=GOLD, pad=11), Spacer(1, 7)]
    n = 3
    for title, blocks in g["sections"]:
        flow, pend = [], []
        for b in blocks:
            r = block(b)
            if b[0] == "sub": pend += r; continue
            if pend: flow.append(KeepTogether(pend + r[:1])); flow += r[1:]; pend = []
            else: flow += r
        flow += pend
        first = flow[0]._content if isinstance(flow[0], KeepTogether) else [flow[0]]
        s.append(KeepTogether([Section(n, title)] + list(first))); s += flow[1:]; s.append(Spacer(1, gap)); n += 1
    src = [Section(n, "Джерела")] + [P(f"{i}.  {x}", "bodyL") for i, x in enumerate(g["sources"], 1)]
    mark = Table([[P("СЛУЖБОВА ВІДМІТКА", "label"), "", ""],
                  [P("Застосовано до (ПН № / дата):", "small"), P("Відповідальна особа:", "small"), P("Дата / підпис:", "small")],
                  ["", "", ""]], colWidths=[W * 0.40, W * 0.34, W * 0.26], rowHeights=[None, None, 6 * mm])
    mark.setStyle(TableStyle([("BOX", (0, 0), (-1, -1), 0.5, RULE), ("SPAN", (0, 0), (-1, 0)),
                              ("LINEBELOW", (0, 2), (-1, 2), 0.4, RULE), ("LINEAFTER", (0, 1), (1, 2), 0.4, RULE),
                              ("LEFTPADDING", (0, 0), (-1, -1), 7), ("TOPPADDING", (0, 0), (-1, -1), 4)]))
    if glue:
        s.append(KeepTogether(src + [Spacer(1, 6), mark]))
    else:
        s.append(KeepTogether(src)); s.append(Spacer(1, 6)); s.append(KeepTogether([mark]))
    return s


def _render(g, buyer, date, content_date, gap, fs, glue):
    buf = BytesIO()
    doc = BaseDocTemplate(buf, pagesize=A4, leftMargin=LM, rightMargin=RM, topMargin=24 * mm, bottomMargin=21 * mm,
                          title=f"{g['code']} · {g['title'][:60]}", author="Бухгалтерські лайфхаки",
                          subject=f"Примірник для {buyer}")
    doc.addPageTemplates([PageTemplate(frames=[Frame(LM, 21 * mm, W, PH - 45 * mm, leftPadding=0, rightPadding=0,
                                                     topPadding=0, bottomPadding=0)])])
    doc.build(_story(g, _styles(fs), gap, glue, content_date), canvasmaker=_make_canvas(buyer, date, g["code"]))
    return buf


def generate_nk_pdf(g: dict, telegram_id: int, content_date: str = None):
    """Персоналізований PDF. Повертає (BytesIO, order_ref)."""
    _register()
    date = datetime.now().strftime("%d.%m.%Y")
    content_date = content_date or g.get("content_date") or date
    order_ref = hashlib.sha1(f"{g['code']}:{telegram_id}:{date}".encode()).hexdigest()[:8]
    buyer = f"TG ID {telegram_id}"
    # 2 проходи максимум: звичайний, а якщо остання сторінка — лише службова відмітка, щільніший
    buf = _render(g, buyer, date, content_date, 6, 9.8, False)
    try:
        from pypdf import PdfReader  # необов'язково
        r = PdfReader(BytesIO(buf.getvalue()))
        last = r.pages[-1].extract_text() or ""
        if "СЛУЖБОВА ВІДМІТКА" in last and "ДЖЕРЕЛА" not in last.upper() and len(last) < 900:
            buf = _render(g, buyer, date, content_date, 3, 9.5, False)
    except Exception:
        pass
    buf.seek(0)
    return buf, order_ref


def build_nk_filename(g: dict, order_ref: str) -> str:
    return f"{g['code']}_{order_ref}.pdf"
