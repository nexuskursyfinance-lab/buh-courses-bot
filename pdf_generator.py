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

ВІЗУАЛЬНИЙ СТИЛЬ (затверджено на зразку "Зразок шаблону публікації"):
  - Офіційний роз'яснювальний реєстр: кирилична гарнітура,
    вирівнювання по ширині, пронумеровані заголовки розділів ВЕЛИКИМИ
    ЛІТЕРАМИ (1. КОМУ ЦЕ АКТУАЛЬНО, 2. СУТЬ ПИТАННЯ, ...).
  - Назва питання — 20pt, з відступом мінімум у три рядки перед
    початком першого розділу (чітке відокремлення заголовка від тексту).
  - Жоден пронумерований пункт (крок) не розривається між сторінками:
    якщо пункт цілком не вміщується до кінця сторінки, він переноситься
    на наступну повністю — разом зі своїм номером, а не окремо.
  - Так само не залишається "сирітський" заголовок розділу в самому
    низу сторінки без жодного рядка тексту під ним.

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

log = logging.getLogger(__name__)

# =====================================================================
# ШРИФТИ З ПІДТРИМКОЮ КИРИЛИЦІ
# =====================================================================
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
INK = HexColor("#1A1A1A")
GREY = HexColor("#5A5A5A")

BODY_SIZE = 11
BODY_LEADING = 15.5
HEADING_SIZE = 12
HEADING_LEADING = 16
TITLE_SIZE = 20
TITLE_LEADING = 24
BRAND_SIZE = 9.5

# Водяний знак: ОДИН великий, дуже світлий напис по центру сторінки —
# як у серйозних платних PDF-продуктів (не "рябить" плитками по всій
# сторінці). Ідентифікатор покупця лишається присутнім і читабельним
# при потребі перевірки, але не заважає читанню чи друку.
WATERMARK_COLOR = Color(0.5, 0.5, 0.5, alpha=0.055)
WATERMARK_FONT_SIZE = 30

LEGAL_NOTICE = (
    "Матеріал є об'єктом авторського права (ст. 8, 15 Закону України "
    "«Про авторське право і суміжні права»). Розповсюдження, перепродаж "
    "і публікація без письмової згоди правовласника заборонені та тягнуть "
    "відповідальність згідно з чинним законодавством України та умовами "
    "Договору публічної оферти."
)

SECTION_TITLES = [
    "КОМУ ЦЕ АКТУАЛЬНО",
    "СУТЬ ПИТАННЯ",
    "ЯК ДІЯТИ ПРАВИЛЬНО",
    "ПРИКЛАД З ПРАКТИКИ",
    "ТИПОВІ ПОМИЛКИ",
    "КОРОТКИЙ ЧЕКЛИСТ",
    "ДЖЕРЕЛО / НОРМАТИВНА БАЗА",
]

# =====================================================================
# ОЧИЩЕННЯ ТЕКСТУ: HTML-розмітка → справжнє форматування, емодзі — геть
# =====================================================================

_A_TAG = re.compile(r'<a\s+href="([^"]*)">(.*?)</a>', re.IGNORECASE | re.DOTALL)
_UNKNOWN_TAG = re.compile(r'<(?!/?b\b)[^>]+>', re.IGNORECASE)
_BOLD_SPLIT = re.compile(r'(<b>|</b>)', re.IGNORECASE)

_EMOJI_PATTERN = re.compile(
    "["
    "\U0001F300-\U0001FAFF"
    "\U00002600-\U000027BF"
    "\U0001F1E6-\U0001F1FF"
    "\U00002B00-\U00002BFF"
    "\uFE0F"
    "\u200D"
    "]+"
)


def _clean_text(text: str) -> str:
    text = text or ""
    text = _A_TAG.sub(lambda m: f"{m.group(2)} ({m.group(1)})", text)
    text = _UNKNOWN_TAG.sub("", text)
    text = _EMOJI_PATTERN.sub("", text)
    return text.strip()


def _parse_bold_segments(line: str):
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
    tokens = []
    for text, bold in segments:
        for part in re.split(r"(\s+)", text):
            if part:
                tokens.append((part, bold))
    return tokens


def _wrap_tokens(tokens, max_width, font_size):
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


def _line_content_width(line_tokens, font_size):
    total = 0.0
    for word, bold in line_tokens:
        font = FONT_BOLD if bold else FONT_REGULAR
        total += pdfmetrics.stringWidth(word, font, font_size)
    return total


def _draw_line(c, line_tokens, x, y, font_size, max_width=None, justify=False):
    """Малює один рядок. Якщо justify=True — розтягує пробіли так, щоб
    рядок займав рівно max_width (як у друкованих офіційних документах);
    останній рядок абзацу так ніколи не розтягується."""
    if justify and max_width:
        natural = _line_content_width(line_tokens, font_size)
        gaps = sum(1 for w, _ in line_tokens if w.isspace())
        extra_per_gap = (max_width - natural) / gaps if gaps > 0 else 0
    else:
        extra_per_gap = 0
    cx = x
    for word, bold in line_tokens:
        font = FONT_BOLD if bold else FONT_REGULAR
        c.setFont(font, font_size)
        w = pdfmetrics.stringWidth(word, font, font_size)
        if word.isspace():
            cx += w + extra_per_gap
        else:
            c.drawString(cx, y, word)
            cx += w


def _safe_filename(text: str, max_len: int = 40) -> str:
    text = re.sub(r'[\\/*?:"<>|]', "", text or "")
    text = text.strip().replace(" ", "_")
    return text[:max_len] if text else "gp"


# =====================================================================
# СТРУКТУРА ДОКУМЕНТА: перетворюємо 7 полів бази в список "блоків"
# =====================================================================

def _split_lines(text):
    return [ln.strip() for ln in (text or "").split("\n") if ln.strip()]


# Деякі питання (особливо перші 27, імпортовані ще старим шляхом) мають
# власну нумерацію/маркер УЖЕ вписаними в сам текст рядка ("1.  Текст",
# "•  Текст"). Якщо цього не прибрати, наш код додає ще один шар
# нумерації зверху, і виходить задвоєння на кшталт "1) 1. Текст"
# або "–  •  Текст". Тому перед тим, як застосувати ВЛАСНУ нумерацію/
# маркер, завжди прибираємо будь-який такий "успадкований" префікс.
_LEADING_MARKER = re.compile(
    r'^(?:\d+[\.\)]\s+|[•●○\u2022\-\u2013\u2014]\s+|\u2610\s*)+'
)


def _strip_leading_marker(text):
    return _LEADING_MARKER.sub("", text or "").strip()


def _build_blocks(question: dict):
    """Перетворює 7 полів гарячого питання в лінійний список блоків
    для рендерингу: заголовки розділів, абзаци, пронумеровані пункти,
    марковані пункти (–) і чеклист (☐) — саме так, як у затвердженому
    зразку офіційного роз'яснення."""
    blocks = []

    def heading(i):
        blocks.append({"type": "heading", "num": i, "title": SECTION_TITLES[i - 1]})

    heading(1)
    for para in _split_lines(question.get("block_audience", "")):
        blocks.append({"type": "paragraph", "text": para})

    heading(2)
    for para in _split_lines(question.get("block_problem", "")):
        blocks.append({"type": "paragraph", "text": para})

    heading(3)
    for i, step in enumerate(_split_lines(question.get("block_solution", "")), start=1):
        blocks.append({"type": "numbered", "n": i, "text": _strip_leading_marker(step)})

    heading(4)
    for para in _split_lines(question.get("block_example", "")):
        blocks.append({"type": "paragraph", "text": para})

    heading(5)
    for m in _split_lines(question.get("block_mistakes", "")):
        blocks.append({"type": "bullet", "text": _strip_leading_marker(m)})

    heading(6)
    for ch in _split_lines(question.get("block_checklist", "")):
        blocks.append({"type": "checklist", "text": _strip_leading_marker(ch)})

    heading(7)
    for s in _split_lines(question.get("block_sources", "")):
        blocks.append({"type": "bullet", "text": _strip_leading_marker(s)})

    blocks.append({"type": "legal"})
    return blocks


# =====================================================================
# РОЗМІТКА Й МАЛЮВАННЯ
# =====================================================================

def _draw_watermark(c, text):
    c.saveState()
    c.setFont(FONT_BOLD, WATERMARK_FONT_SIZE)
    c.setFillColor(WATERMARK_COLOR)
    c.translate(PAGE_W / 2, PAGE_H / 2)
    c.rotate(40)
    c.drawCentredString(0, 0, text)
    c.restoreState()


def _draw_footer(c, brand_line, page_num, total_pages, hidden_line):
    """Видима брендована частина футера (як у зразку) + дрібний прихований
    рядок з order_id під нею (шар відстеження, майже непомітний)."""
    c.saveState()
    c.setFont(FONT_REGULAR, 8.5)
    c.setFillColor(GREY)
    c.drawString(MARGIN, 13 * mm, brand_line)
    page_label = f"Стор. {page_num} з {total_pages}"
    c.drawRightString(PAGE_W - MARGIN, 13 * mm, page_label)
    c.setFont(FONT_REGULAR, 6)
    c.drawString(MARGIN, 9 * mm, hidden_line)
    c.restoreState()


def _draw_title(c, title, y, max_width, draw=True):
    """Назва питання — 20pt, жирний, одразу зверху сторінки (без брендованого
    рядка над нею — документ має "починатись з назви питання"). Повертає y
    ПІСЛЯ обов'язкового відступу в 3 рядки перед першим розділом."""
    clean = _clean_text(title)
    tokens = _tokenize_segments([(clean, True)])
    lines = _wrap_tokens(tokens, max_width, TITLE_SIZE)
    for line in lines:
        if draw:
            c.setFillColor(INK)
            c.setFont(FONT_BOLD, TITLE_SIZE)
            cx = MARGIN
            for word, bold in line:
                c.drawString(cx, y, word)
                cx += pdfmetrics.stringWidth(word, FONT_BOLD, TITLE_SIZE)
        y -= TITLE_LEADING
    # мінімум три рядки відступу перед розділом 1
    y -= BODY_LEADING * 3
    return y


# --- вимірювання висоти блоку (для контролю "сиріт") -------------------

def _measure_paragraph(text, font_size, max_width):
    segments = _parse_bold_segments(_clean_text(text))
    tokens = _tokenize_segments(segments)
    return _wrap_tokens(tokens, max_width, font_size)


def _measure_numbered(n, text, max_width, indent):
    label = f"{n})  "
    content_width = max_width - indent
    segments = _parse_bold_segments(_clean_text(text))
    tokens = _tokenize_segments(segments)
    lines = _wrap_tokens(tokens, content_width, BODY_SIZE)
    return label, lines


def _measure_bullet(text, max_width, indent):
    content_width = max_width - indent
    segments = _parse_bold_segments(_clean_text(text))
    tokens = _tokenize_segments(segments)
    return _wrap_tokens(tokens, content_width, BODY_SIZE)


def _block_height(block, max_width):
    """Скільки вертикального місця займе блок — потрібно, щоб вирішити,
    чи влізе заголовок розділу разом з тим, що йде одразу за ним."""
    btype = block["type"]
    if btype == "paragraph":
        lines = _measure_paragraph(block["text"], BODY_SIZE, max_width)
        return len(lines) * BODY_LEADING + BODY_LEADING * 0.35
    if btype == "numbered":
        _, lines = _measure_numbered(block["n"], block["text"], max_width, 16)
        return len(lines) * BODY_LEADING + BODY_LEADING * 0.3
    if btype in ("bullet", "checklist"):
        lines = _measure_bullet(block["text"], max_width, 14)
        return len(lines) * BODY_LEADING + BODY_LEADING * 0.3
    return 0


# --- основний рендер-цикл ----------------------------------------------

def _render(blocks, question_title, c, watermark_text, brand_footer, hidden_footer,
            total_pages_hint=None, draw=True):
    """Проходить усі блоки й або малює їх (draw=True, потрібен реальний
    canvas), або лише рахує кількість сторінок (draw=False — перший,
    "сухий" прохід, щоб дізнатись total_pages для футера "Стор. X з Y").

    Повертає фактичну кількість сторінок."""
    max_width = PAGE_W - 2 * MARGIN
    page_num = 1
    y = PAGE_H - MARGIN

    def new_page(first=False):
        nonlocal page_num, y
        if not first:
            if draw:
                _draw_footer(c, brand_footer, page_num, total_pages_hint or page_num, hidden_footer)
                c.showPage()
            page_num += 1
        if draw:
            _draw_watermark(c, watermark_text)
        y = PAGE_H - MARGIN
        if first:
            y = _draw_title(c, question_title, y, max_width, draw=draw)

    new_page(first=True)
    bottom_limit = MARGIN + 16 * mm

    usable_page_h = PAGE_H - MARGIN - bottom_limit

    for idx, block in enumerate(blocks):
        btype = block["type"]

        if btype == "heading":
            heading_h = HEADING_LEADING + 6
            # ПОСИЛЕНИЙ захист від "сирітського" заголовка: заголовок має
            # лишатись на сторінці РАЗОМ з усім блоком, що йде одразу за
            # ним (наприклад, розділ 4 не сміє розірватися на "заголовок
            # + 2 рядки" — інакше бухгалтер, якого відволікли, гортаючи
            # назад, губить зв'язок між назвою пункту й текстом).
            next_h = _block_height(blocks[idx + 1], max_width) if idx + 1 < len(blocks) else 0
            combined = heading_h + next_h
            if combined <= usable_page_h:
                # Заголовок + весь наступний блок повністю влазять в одну
                # сторінку загалом — вимагаємо їх РАЗОМ, без компромісів.
                if y - combined < bottom_limit:
                    new_page()
            else:
                # Наступний блок сам по собі довший за сторінку (рідкісний
                # випадок) — не можемо уникнути розриву десь усередині нього,
                # але заголовку однаково лишаємо суттєвий запас тексту під ним.
                if y - heading_h - BODY_LEADING * 4 < bottom_limit:
                    new_page()
            if draw:
                c.setFillColor(INK)
                c.setFont(FONT_BOLD, HEADING_SIZE)
                c.drawString(MARGIN, y, f"{block['num']}. {block['title']}")
            y -= heading_h

        elif btype == "paragraph":
            lines = _measure_paragraph(block["text"], BODY_SIZE, max_width)
            for i, line in enumerate(lines):
                if y - BODY_LEADING < bottom_limit:
                    new_page()
                if draw:
                    c.setFillColor(INK)
                    is_last = (i == len(lines) - 1)
                    _draw_line(c, line, MARGIN, y, BODY_SIZE, max_width, justify=not is_last)
                y -= BODY_LEADING
            y -= BODY_LEADING * 0.35

        elif btype == "numbered":
            indent = 16
            label, lines = _measure_numbered(block["n"], block["text"], max_width, indent)
            item_h = len(lines) * BODY_LEADING
            # АТОМАРНІСТЬ: якщо весь пункт не влазить до низу сторінки —
            # переносимо його цілком (з номером) на наступну сторінку.
            if item_h <= usable_page_h and y - item_h < bottom_limit:
                new_page()
            for i, line in enumerate(lines):
                if y - BODY_LEADING < bottom_limit:
                    new_page()
                if draw:
                    c.setFillColor(INK)
                    if i == 0:
                        c.setFont(FONT_BOLD, BODY_SIZE)
                        c.drawString(MARGIN, y, label)
                    is_last = (i == len(lines) - 1)
                    _draw_line(c, line, MARGIN + indent, y, BODY_SIZE, max_width - indent, justify=not is_last)
                y -= BODY_LEADING
            y -= BODY_LEADING * 0.3

        elif btype in ("bullet", "checklist"):
            marker = "\u2610" if btype == "checklist" else "\u2013"
            indent = 14
            lines = _measure_bullet(block["text"], max_width, indent)
            item_h = len(lines) * BODY_LEADING
            if item_h <= usable_page_h and y - item_h < bottom_limit:
                new_page()
            for i, line in enumerate(lines):
                if y - BODY_LEADING < bottom_limit:
                    new_page()
                if draw:
                    c.setFillColor(INK)
                    if i == 0:
                        c.setFont(FONT_BOLD, BODY_SIZE)
                        c.drawString(MARGIN, y, marker)
                    _draw_line(c, line, MARGIN + indent, y, BODY_SIZE)
                y -= BODY_LEADING
            y -= BODY_LEADING * 0.3

        elif btype == "legal":
            legal_leading = 11.5
            lines = _measure_paragraph(LEGAL_NOTICE, 8.5, max_width)
            # Прив'язуємо примітку до НИЗУ сторінки з чітким, завжди
            # однаковим відступом від футера — а не одразу після
            # попереднього контенту (інакше або зливається з футером,
            # або "висить" по центру з випадковим проміжком).
            footer_top_y = 13 * mm
            gap_above_footer = 10 * mm
            anchor_bottom_y = footer_top_y + gap_above_footer  # y останнього рядка примітки
            start_y = anchor_bottom_y + (len(lines) - 1) * legal_leading

            if y < start_y + legal_leading:
                # На поточній сторінці вже недостатньо місця до фіксованої
                # позиції — переносимо примітку цілком на нову сторінку.
                new_page()

            draw_y = start_y
            if draw:
                c.setFillColor(GREY)
                for line in lines:
                    _draw_line(c, line, MARGIN, draw_y, 8.5)
                    draw_y -= legal_leading
            y = anchor_bottom_y - legal_leading

    if draw:
        _draw_footer(c, brand_footer, page_num, total_pages_hint or page_num, hidden_footer)

    return page_num


# =====================================================================
# ГОЛОВНА ФУНКЦІЯ
# =====================================================================

def generate_question_pdf(question: dict, telegram_id: int, extra_ref: str = "") -> tuple[BytesIO, str]:
    """
    Генерує ПЕРСОНАЛІЗОВАНИЙ PDF для конкретного покупця у затвердженому
    офіційному стилі (роз'яснення: нумеровані розділи ВЕЛИКИМИ ЛІТЕРАМИ,
    вирівнювання по ширині, назва 20pt з відступом, пункти не рвуться
    між сторінками).

    Повертає (buf, order_ref) — buf для message.answer_document(...),
    order_ref передайте в build_pdf_filename() для унікального імені файлу.
    """
    order_ref = extra_ref or f"TG{telegram_id}-{uuid.uuid4().hex[:8]}"
    date_str = datetime.now().strftime("%d.%m.%Y")
    datetime_str = datetime.now().strftime("%d.%m.%Y %H:%M")

    watermark_text = f"TG ID {telegram_id} · {order_ref} · {date_str}"
    brand_footer = f"Бухгалтерські лайфхаки · {date_str} · № ГП-{str(question.get('id', '0')).zfill(3)}"
    hidden_footer = f"Документ згенеровано для: TG ID {telegram_id}, {order_ref}, {datetime_str} · © Бухгалтерські лайфхаки"

    title = question.get("question", "Гаряче питання")
    blocks = _build_blocks(question)

    # Прохід 1 ("сухий"): тільки порахувати, скільки сторінок вийде —
    # потрібно для коректного "Стор. X з Y" у футері (Y відомий заздалегідь).
    total_pages = _render(blocks, title, None, watermark_text, brand_footer, hidden_footer, draw=False)

    # Прохід 2: реальне малювання з уже відомою кількістю сторінок.
    buf = BytesIO()
    c = canvas.Canvas(buf, pagesize=A4)

    c.setAuthor("ФОП Кирушок Наталія Юріївна")
    c.setTitle(_clean_text(title))
    c.setSubject("Бухгалтерські лайфхаки — Гаряче питання")
    c.setCreator("Бухгалтерські лайфхаки")
    c.setKeywords(f"© {datetime.now().year} Бухгалтерські лайфхаки. Всі права захищені. {order_ref}")

    _render(blocks, title, c, watermark_text, brand_footer, hidden_footer,
            total_pages_hint=total_pages, draw=True)

    c.save()
    buf.seek(0)
    return buf, order_ref


def build_pdf_filename(question: dict, order_ref: str) -> str:
    qid = question.get("id", "0")
    short = _safe_filename(_clean_text(question.get("question", "")))
    safe_ref = re.sub(r'[\\/*?:"<>|]', "", order_ref or "")
    return f"ГП_{qid}_{short}_{safe_ref}.pdf"
