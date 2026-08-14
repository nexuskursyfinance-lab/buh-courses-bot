"""
Генератор персоналізованих PDF-файлів для «Гарячих питань».

Навіщо цей файл існує:
Кожне гаряче питання в базі даних — один спільний текст, однаковий
для всіх покупців. Але клієнту видається не цей "сирий" текст,
а PDF, згенерований у момент видачі САМЕ для нього — з водяним
знаком, прихованим ідентифікатором і метаданими, які вказують
на конкретного покупця (Telegram ID + унікальний код доступу).

Це не робить копіювання неможливим (це в принципі неможливо для
жодного цифрового файлу), але робить джерело перепродажу
відстежуваним — якщо файл десь "спливе", видно, чий саме він.

Текст питання в базі містить HTML-розмітку (<b>...</b>, <a href="...">...</a>)
для показу в Telegram — цей модуль коректно перетворює її на справжнє
форматування в PDF (жирний текст, посилання як "текст (URL)"), а не
показує теги буквально. Емодзі, які не вміє малювати кириличний
шрифт (DejaVu Sans не містить кольорових emoji-гліфів), акуратно
прибираються, щоб замість них не з'являлись порожні "квадратики".

Використання з bot.py:
    from pdf_generator import generate_question_pdf, build_pdf_filename
    pdf_buffer, order_ref = generate_question_pdf(question_dict, telegram_id)
    filename = build_pdf_filename(question_dict, order_ref)
    # pdf_buffer — це BytesIO, готовий для message.answer_document(...)
"""

import os
import re
import uuid
import logging
from datetime import datetime
from io import BytesIO

from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.lib.colors import HexColor, Color
from reportlab.pdfgen import canvas
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.lib.utils import simpleSplit

log = logging.getLogger(__name__)

# =====================================================================
# ШРИФТИ З ПІДТРИМКОЮ КИРИЛИЦІ
# =====================================================================
# Стандартні вбудовані шрифти reportlab (Helvetica тощо) НЕ вміють
# показувати кирилицю. Тому в комплекті йде підпапка fonts/ з двома
# безкоштовними TTF-файлами (DejaVu Sans, вільна ліцензія).
# НЕ видаляйте й не перейменовуйте папку fonts/ поруч із цим файлом.

FONTS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fonts")
_FONT_REGULAR_PATH = os.path.join(FONTS_DIR, "DejaVuSans.ttf")
_FONT_BOLD_PATH = os.path.join(FONTS_DIR, "DejaVuSans-Bold.ttf")

FONT_REGULAR = "Helvetica"
FONT_BOLD = "Helvetica-Bold"

if os.path.exists(_FONT_REGULAR_PATH) and os.path.exists(_FONT_BOLD_PATH):
    pdfmetrics.registerFont(TTFont("DejaVuSans", _FONT_REGULAR_PATH))
    pdfmetrics.registerFont(TTFont("DejaVuSans-Bold", _FONT_BOLD_PATH))
    FONT_REGULAR = "DejaVuSans"
    FONT_BOLD = "DejaVuSans-Bold"
else:
    log.warning(
        "Кириличні шрифти не знайдено у %s — тексти кирилицею в PDF "
        "можуть не відобразитись коректно. Перевірте, що папка fonts/ "
        "з DejaVuSans.ttf і DejaVuSans-Bold.ttf лежить поруч із pdf_generator.py",
        FONTS_DIR,
    )

# =====================================================================
# ВІЗУАЛЬНІ КОНСТАНТИ
# =====================================================================

PAGE_W, PAGE_H = A4
MARGIN = 20 * mm
NAVY = HexColor("#1F3864")
GREY = HexColor("#5A5A5A")
INK = HexColor("#1A1A1A")
# Водяний знак: ОДИН великий, дуже світлий напис по центру сторінки —
# як у серйозних платних PDF-продуктів (не "рябить" плитками по всій
# сторінці). Ідентифікатор покупця лишається присутнім і читабельним
# при потребі перевірки, але не заважає читати чи друкувати основний текст.
WATERMARK_COLOR = Color(0.5, 0.5, 0.5, alpha=0.055)
WATERMARK_FONT_SIZE = 30

LEGAL_NOTICE = (
    "Матеріал є об'єктом авторського права (ст. 8, 15 Закону України "
    "«Про авторське право і суміжні права»). Розповсюдження, перепродаж "
    "і публікація без письмової згоди правовласника заборонені та тягнуть "
    "відповідальність згідно з чинним законодавством України та умовами "
    "Договору публічної оферти."
)

# =====================================================================
# ОЧИЩЕННЯ ТЕКСТУ: HTML-розмітка → справжнє форматування, емодзі — геть
# =====================================================================

_A_TAG = re.compile(r'<a\s+href="([^"]*)">(.*?)</a>', re.IGNORECASE | re.DOTALL)
# Прибираємо всі теги, ОКРІМ <b> і </b> — їх обробляємо окремо як жирний текст
_UNKNOWN_TAG = re.compile(r'<(?!/?b\b)[^>]+>', re.IGNORECASE)
_BOLD_SPLIT = re.compile(r'(<b>|</b>)', re.IGNORECASE)

# Основні блоки Unicode, де живуть emoji — DejaVu Sans їх не містить,
# тому такі символи прибираються, щоб не показувались "квадратиками".
_EMOJI_PATTERN = re.compile(
    "["
    "\U0001F300-\U0001FAFF"  # символи, піктограми (у т.ч. 👤📋❌📌📎🔥💰🏦💡)
    "\U00002600-\U000027BF"  # інші символи й дінгбати (у т.ч. ⚠️✅)
    "\U0001F1E6-\U0001F1FF"  # прапори
    "\U00002B00-\U00002BFF"  # додаткові символи і стрілки
    "\uFE0F"                  # варіаційний селектор (робить символ кольоровим emoji)
    "\u200D"                  # zero-width joiner (склеює складові emoji)
    "]+"
)


def _clean_text(text: str) -> str:
    """Перетворює <a href="URL">текст</a> на 'текст (URL)', прибирає емодзі
    та будь-які HTML-теги, окрім <b>/</b> (їх обробляє _parse_bold_segments)."""
    text = text or ""
    text = _A_TAG.sub(lambda m: f"{m.group(2)} ({m.group(1)})", text)
    text = _UNKNOWN_TAG.sub("", text)
    text = _EMOJI_PATTERN.sub("", text)
    return text


def _parse_bold_segments(line: str):
    """Розбиває рядок на сегменти [(текст, жирний?), ...] за тегами <b>/</b>."""
    segments = []
    bold = False
    for part in _BOLD_SPLIT.split(line):
        if part == "<b>":
            bold = True
        elif part == "</b>":
            bold = False
        elif part:
            segments.append((part, bold))
    return segments or [("", False)]


def _tokenize_segments(segments):
    """[(текст, жирний?), ...] → [(слово_або_пробіл, жирний?), ...]"""
    tokens = []
    for text, bold in segments:
        for part in re.split(r"(\s+)", text):
            if part:
                tokens.append((part, bold))
    return tokens


def _wrap_tokens(tokens, max_width, font_size):
    """Розбиває токени на рядки за шириною сторінки, з урахуванням жирного шрифту."""
    lines, current, current_width = [], [], 0.0
    for word, bold in tokens:
        font = FONT_BOLD if bold else FONT_REGULAR
        w = pdfmetrics.stringWidth(word, font, font_size)
        if word.isspace():
            if current_width + w > max_width and current:
                lines.append(current)
                current, current_width = [], 0.0
                continue
            current.append((word, bold))
            current_width += w
            continue
        if current_width + w > max_width and current:
            lines.append(current)
            current, current_width = [], 0.0
        current.append((word, bold))
        current_width += w
    if current:
        lines.append(current)
    return lines


def _draw_line(c: canvas.Canvas, line_tokens, x: float, y: float, font_size: float):
    cx = x
    for word, bold in line_tokens:
        font = FONT_BOLD if bold else FONT_REGULAR
        c.setFont(font, font_size)
        c.drawString(cx, y, word)
        cx += pdfmetrics.stringWidth(word, font, font_size)


def _safe_filename(text: str, max_len: int = 40) -> str:
    """Прибирає символи, небезпечні для імені файлу, і обрізає довжину."""
    text = re.sub(r'[\\/*?:"<>|]', "", text or "")
    text = text.strip().replace(" ", "_")
    return text[:max_len] if text else "gp"


# =====================================================================
# МАЛЮВАННЯ СТОРІНКИ
# =====================================================================

def _draw_watermark(c: canvas.Canvas, text: str):
    """Малює ОДИН великий, дуже світлий діагональний водяний знак по центру
    сторінки — ідентифікатор покупця лишається присутнім, але не заважає
    читанню чи друку (на відміну від "рябих" плиткових watermark)."""
    c.saveState()
    c.setFont(FONT_BOLD, WATERMARK_FONT_SIZE)
    c.setFillColor(WATERMARK_COLOR)
    c.translate(PAGE_W / 2, PAGE_H / 2)
    c.rotate(40)
    c.drawCentredString(0, 0, text)
    c.restoreState()


def _draw_footer(c: canvas.Canvas, footer_text: str, page_num: int):
    """Дрібний прихований ідентифікатор і copyright у футері кожної сторінки."""
    c.saveState()
    c.setFont(FONT_REGULAR, 6.5)
    c.setFillColor(GREY)
    c.drawString(MARGIN, 10 * mm, f"{footer_text} · стор. {page_num}")
    c.restoreState()


def _draw_header(c: canvas.Canvas, title: str):
    """Фірмовий верхній банер із заголовком питання (без emoji — не всі
    символи є в кириличному шрифті, замість них лишається чистий текст)."""
    c.saveState()
    c.setFillColor(NAVY)
    c.rect(0, PAGE_H - 28 * mm, PAGE_W, 28 * mm, fill=1, stroke=0)
    c.setFillColor(HexColor("#FFFFFF"))
    c.setFont(FONT_BOLD, 10)
    c.drawString(MARGIN, PAGE_H - 11 * mm, "ГАРЯЧЕ ПИТАННЯ · Бухгалтерські лайфхаки")
    c.setFont(FONT_BOLD, 13)
    clean_title = _clean_text(title)
    lines = simpleSplit(clean_title, FONT_BOLD, 13, PAGE_W - 2 * MARGIN)
    y = PAGE_H - 19 * mm
    for line in lines[:2]:
        c.drawString(MARGIN, y, line)
        y -= 6 * mm
    c.restoreState()


def _wrap_and_draw_body(
    c: canvas.Canvas,
    text: str,
    watermark_text: str,
    footer_text: str,
    start_y: float,
) -> int:
    """Пише основний текст (із правильним жирним форматуванням замість
    буквальних <b> тегів), автоматично переносячи на нові сторінки."""
    max_width = PAGE_W - 2 * MARGIN
    font_size = 10.5
    leading = 15
    y = start_y
    page_num = 1

    c.setFillColor(INK)

    for para in (text or "").split("\n"):
        if not para.strip():
            y -= leading
            continue

        segments = _parse_bold_segments(para)
        tokens = _tokenize_segments(segments)
        wrapped_lines = _wrap_tokens(tokens, max_width, font_size)

        for line_tokens in wrapped_lines:
            if y < MARGIN + 15 * mm:
                _draw_footer(c, footer_text, page_num)
                c.showPage()
                page_num += 1
                _draw_watermark(c, watermark_text)
                c.setFillColor(INK)
                y = PAGE_H - MARGIN
            _draw_line(c, line_tokens, MARGIN, y, font_size)
            y -= leading
        y -= leading * 0.4

    _draw_footer(c, footer_text, page_num)
    return page_num


# =====================================================================
# ГОЛОВНА ФУНКЦІЯ
# =====================================================================

def generate_question_pdf(question: dict, telegram_id: int, extra_ref: str = "") -> tuple[BytesIO, str]:
    """
    Генерує ПЕРСОНАЛІЗОВАНИЙ PDF для конкретного покупця.

    question    — словник з ключами 'question' (заголовок) і 'answer' (текст
                  відповіді, може містити HTML-розмітку <b>/<a> — обробляється
                  автоматично) — ті самі поля, які bot.py вже використовує.
    telegram_id — Telegram ID покупця; вшивається у watermark і footer.
    extra_ref   — необов'язковий додатковий код (наприклад order_id), якщо
                  він відомий на момент виклику; якщо ні — генерується
                  власний унікальний код доступу.

    Повертає (buf, order_ref):
      buf       — BytesIO з готовим PDF (курсор уже на позиції 0), прямо
                  для message.answer_document(...) в aiogram.
      order_ref — фактично використаний унікальний код покупки; передайте
                  його в build_pdf_filename(), щоб ім'я файлу теж було
                  унікальним для цього покупця (а не однаковим для всіх).
    """
    order_ref = extra_ref or f"TG{telegram_id}-{uuid.uuid4().hex[:8]}"
    date_str = datetime.now().strftime("%d.%m.%Y %H:%M")

    watermark_text = f"TG ID {telegram_id} · {order_ref} · {date_str}"
    footer_text = (
        f"Документ згенеровано для: TG ID {telegram_id}, {order_ref}, {date_str} · "
        f"© Бухгалтерські лайфхаки · ФОП Кирушок Н.Ю."
    )

    buf = BytesIO()
    c = canvas.Canvas(buf, pagesize=A4)

    # --- Метадані файлу (шар захисту №4) ---
    # PDF-стандарт не має окремого поля "Copyright" — за конвенцією його
    # вписують у Keywords, це коректно читається в усіх переглядачах PDF
    # (Файл → Властивості → Ключові слова).
    c.setAuthor("ФОП Кирушок Наталія Юріївна")
    c.setTitle(_clean_text(question.get("question", "Гаряче питання")))
    c.setSubject("Бухгалтерські лайфхаки — Гаряче питання")
    c.setCreator("Бухгалтерські лайфхаки")
    c.setKeywords(f"© {datetime.now().year} Бухгалтерські лайфхаки. Всі права захищені. {order_ref}")

    _draw_watermark(c, watermark_text)
    _draw_header(c, question.get("question", ""))

    body_text = _clean_text(question.get("answer", ""))
    body_text += "\n\n" + "—" * 40 + "\n" + LEGAL_NOTICE

    _wrap_and_draw_body(
        c, body_text, watermark_text, footer_text, start_y=PAGE_H - 34 * mm
    )

    c.save()
    buf.seek(0)
    return buf, order_ref


def build_pdf_filename(question: dict, order_ref: str) -> str:
    """Формує ім'я файлу у форматі ГП_{id}_{коротка_назва}_{order_ref}.pdf.

    order_ref обов'язковий (друге значення, яке повертає generate_question_pdf) —
    без нього різні покупці одного й того ж питання отримували б файли
    з однаковим ім'ям, хоч і різним вмістом усередині."""
    qid = question.get("id", "0")
    short = _safe_filename(_clean_text(question.get("question", "")))
    safe_ref = re.sub(r'[\\/*?:"<>|]', "", order_ref or "")
    return f"ГП_{qid}_{short}_{safe_ref}.pdf"
