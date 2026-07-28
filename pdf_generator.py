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

Використання з bot.py:
    from pdf_generator import generate_question_pdf
    pdf_buffer = generate_question_pdf(question_dict, telegram_id)
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
WATERMARK_COLOR = Color(0.55, 0.55, 0.55, alpha=0.20)

LEGAL_NOTICE = (
    "Матеріал є об'єктом авторського права (ст. 8, 15 Закону України "
    "«Про авторське право і суміжні права»). Розповсюдження, перепродаж "
    "і публікація без письмової згоди правовласника заборонені та тягнуть "
    "відповідальність згідно з чинним законодавством України та умовами "
    "Договору публічної оферти."
)


def _safe_filename(text: str, max_len: int = 40) -> str:
    """Прибирає символи, небезпечні для імені файлу, і обрізає довжину."""
    text = re.sub(r'[\\/*?:"<>|]', "", text or "")
    text = text.strip().replace(" ", "_")
    return text[:max_len] if text else "gp"


def _draw_watermark(c: canvas.Canvas, text: str):
    """Малює напівпрозорий діагональний водяний знак по всій сторінці."""
    c.saveState()
    c.setFont(FONT_BOLD, 12)
    c.setFillColor(WATERMARK_COLOR)
    c.translate(PAGE_W / 2, PAGE_H / 2)
    c.rotate(40)
    step = 65
    for y in range(-int(PAGE_H), int(PAGE_H), step):
        c.drawCentredString(0, y, text)
    c.restoreState()


def _draw_footer(c: canvas.Canvas, footer_text: str, page_num: int):
    """Дрібний прихований ідентифікатор і copyright у футері кожної сторінки."""
    c.saveState()
    c.setFont(FONT_REGULAR, 6.5)
    c.setFillColor(GREY)
    c.drawString(MARGIN, 10 * mm, f"{footer_text} · стор. {page_num}")
    c.restoreState()


def _draw_header(c: canvas.Canvas, title: str):
    """Фірмовий верхній банер зі значком і заголовком питання."""
    c.saveState()
    c.setFillColor(NAVY)
    c.rect(0, PAGE_H - 28 * mm, PAGE_W, 28 * mm, fill=1, stroke=0)
    c.setFillColor(HexColor("#FFFFFF"))
    c.setFont(FONT_BOLD, 10)
    c.drawString(MARGIN, PAGE_H - 11 * mm, "🔥 ГАРЯЧЕ ПИТАННЯ · Бухгалтерські лайфхаки")
    c.setFont(FONT_BOLD, 13)
    lines = simpleSplit(title or "", FONT_BOLD, 13, PAGE_W - 2 * MARGIN)
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
    """Пише основний текст, автоматично переносячи на нові сторінки
    (з повторним водяним знаком і футером на кожній новій сторінці)."""
    max_width = PAGE_W - 2 * MARGIN
    font_size = 10.5
    leading = 14
    y = start_y
    page_num = 1

    c.setFont(FONT_REGULAR, font_size)
    c.setFillColor(INK)

    for para in (text or "").split("\n"):
        if not para.strip():
            y -= leading
            continue
        for line in simpleSplit(para, FONT_REGULAR, font_size, max_width):
            if y < MARGIN + 15 * mm:
                _draw_footer(c, footer_text, page_num)
                c.showPage()
                page_num += 1
                _draw_watermark(c, watermark_text)
                c.setFont(FONT_REGULAR, font_size)
                c.setFillColor(INK)
                y = PAGE_H - MARGIN
            c.drawString(MARGIN, y, line)
            y -= leading
        y -= leading * 0.4

    _draw_footer(c, footer_text, page_num)
    return page_num


def generate_question_pdf(question: dict, telegram_id: int, extra_ref: str = "") -> BytesIO:
    """
    Генерує ПЕРСОНАЛІЗОВАНИЙ PDF для конкретного покупця.

    question    — словник з ключами 'question' (заголовок) і 'answer' (текст
                  відповіді) — ті самі поля, які bot.py вже використовує.
    telegram_id — Telegram ID покупця; вшивається у watermark і footer.
    extra_ref   — необов'язковий додатковий код (наприклад order_id), якщо
                  він відомий на момент виклику; якщо ні — генерується
                  власний унікальний код доступу.

    Повертає BytesIO з готовим PDF (курсор уже на позиції 0) —
    прямо для message.answer_document(...) в aiogram.

    ВАЖЛИВО: кожен виклик створює НОВИЙ файл із новим watermark, навіть
    якщо викликати для того самого question_id і того самого telegram_id
    двічі — це нормально й додатково ускладнює зіставлення "злитих" копій.
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
    c.setAuthor("ФОП Кирушок Наталія Юріївна")
    c.setTitle(question.get("question", "Гаряче питання"))
    c.setSubject("Бухгалтерські лайфхаки — Гаряче питання")
    c.setCreator("Бухгалтерські лайфхаки")

    _draw_watermark(c, watermark_text)
    _draw_header(c, question.get("question", ""))

    body_text = question.get("answer", "")
    body_text += "\n\n" + "—" * 40 + "\n" + LEGAL_NOTICE

    _wrap_and_draw_body(
        c, body_text, watermark_text, footer_text, start_y=PAGE_H - 34 * mm
    )

    c.save()
    buf.seek(0)
    return buf


def build_pdf_filename(question: dict) -> str:
    """Формує ім'я файлу у форматі ГП_{id}_{коротка_назва}.pdf"""
    qid = question.get("id", "0")
    short = _safe_filename(question.get("question", ""))
    return f"ГП_{qid}_{short}.pdf"
