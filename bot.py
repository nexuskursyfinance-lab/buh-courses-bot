"""
Головний файл Telegram-бота «Бухгалтерські лайфхаки» (v2)
Фреймворк: aiogram 3.x

Що нового у v2:
- каталог «Гарячих питань» з data/*.json (імпорт автоматично при старті);
- меню показує лише теми й підтеми, де є питання, з лічильниками;
- список питань з ПОВНИМИ назвами й цінами, кнопки з номерами ГП;
- картка питання: ситуація + що всередині + ціна (безкоштовний тизер);
- пошук: просто напишіть слово в чат (або /gp 01);
- deep link: t.me/<бот>?start=gp01 відкриває картку ГП-01;
- PDF у фірмовому стилі NK (pdf_nk.py) з водяним знаком покупця;
- черга публікацій у канал: /queue (лише ADMIN_ID), превʼю і кнопки.

Запуск: python bot.py
"""
import asyncio
import html
import json
import logging
import os
import re
import uuid
from datetime import datetime, timedelta, timezone

from aiogram import Bot, Dispatcher, F
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.filters import CommandStart, Command, CommandObject
from aiogram.types import (
    Message, CallbackQuery,
    InlineKeyboardMarkup, InlineKeyboardButton, BufferedInputFile,
    FSInputFile, InputMediaPhoto,
)
from aiogram.utils.keyboard import InlineKeyboardBuilder

from dotenv import load_dotenv

load_dotenv()

from database import (
    init_db, upsert_user,
    get_catalog_topics, get_catalog_subtopics, get_questions_by_subtopic, get_subtopic,
    get_question_by_id, get_question_by_code, question_content, search_questions,
    create_payment, confirm_payment, get_pending_payments, check_rate_limit, get_stats,
    has_purchased, get_user_purchases,
    list_posts, get_post, update_post, due_posts, busy_slots,
)
from liqpay_helper import generate_payment_url, check_payment_status
from pdf_nk import generate_nk_pdf, build_nk_filename

try:  # старий генератор — лише для питань, куплених до v2
    from pdf_generator import generate_question_pdf, build_pdf_filename
except Exception:  # pragma: no cover
    generate_question_pdf = build_pdf_filename = None

try:
    from zoneinfo import ZoneInfo
    KYIV = ZoneInfo("Europe/Kyiv")
except Exception:  # pragma: no cover
    KYIV = timezone(timedelta(hours=3))

# --- Налаштування ---
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
)
log = logging.getLogger(__name__)

BOT_TOKEN = os.getenv("BOT_TOKEN")
if not BOT_TOKEN:
    raise RuntimeError("❌ BOT_TOKEN не знайдено у змінних середовища!")

bot = Bot(token=BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
dp = Dispatcher()

ADMIN_ID = int(os.getenv("ADMIN_ID", "0"))
CHANNEL_ID = os.getenv("CHANNEL_ID", "").strip()          # @назва_каналу або -100…
POST_SLOTS = [s.strip() for s in os.getenv("POST_SLOTS", "10:00,18:30").split(",") if s.strip()]
QUESTIONS_PER_PAGE = 8
MEDIA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "media")

VIBER_CHANNEL_URL = os.getenv("VIBER_CHANNEL_URL", "https://invite.viber.com/?g2=AQBPWuCs%2BE1%2F%2F1c%2BLvcR94IxNaSXZizpgqc8p5lIdsJRCTBZeW8%2FmItyOp5Api1y")
VIBER_CHAT_URL = os.getenv("VIBER_CHAT_URL", "https://invite.viber.com/?g2=AQBvkk%2FKfZwBJlc%2BL7l9DtVJhNxDayFpxdr237eG1w2Rrl3RJ8hRNomscFPzMBUV")

OFFER_URL = os.getenv(
    "OFFER_URL",
    "https://docs.google.com/document/d/1R28gdhIqzg1-DjVdcVWJ6bhzDH5aUbbEn6rfPBT8Whs/view",
)

EXTRA_NAMES = {
    "scen": "нестандартні ситуації",
    "dps": "позиція ДПС і судів",
    "appeal": "як оскаржити",
    "multi": "кілька прикладів з цифрами",
    "cp": "що буде в контрагента",
}

STATUS_UA = {
    "draft": "📝 чернетка", "hold": "⏸ відкладено", "scheduled": "🗓 заплановано",
    "published": "✅ опубліковано", "error": "⚠️ помилка",
}

_BOT_USERNAME = None


async def bot_username():
    global _BOT_USERNAME
    if not _BOT_USERNAME:
        _BOT_USERNAME = (await bot.get_me()).username
    return _BOT_USERNAME


def esc(s) -> str:
    return html.escape(str(s or ""), quote=False)


def is_admin(user_id: int) -> bool:
    return bool(ADMIN_ID) and user_id == ADMIN_ID


def tg_safe(text: str) -> str:
    """Екранує текст для HTML Telegram, зберігаючи лише <b>…</b>."""
    t = esc(text or "")
    return t.replace("&lt;b&gt;", "<b>").replace("&lt;/b&gt;", "</b>")


def first_sentence(text: str, limit: int = 220) -> str:
    plain = re.sub(r"<[^>]+>", "", text or "")
    m = re.match(r"(.+?[.!?])(\s|$)", plain)
    s = m.group(1) if m else plain
    return s if len(s) <= limit else s[: limit - 1].rstrip() + "…"


def gp_num(code: str) -> str:
    m = re.search(r"(\d+)", code or "")
    return "%02d" % int(m.group(1)) if m else ""


# =====================================================================
# КЛАВІАТУРИ
# =====================================================================

def kb_main_menu() -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    for t in get_catalog_topics():
        b.button(text=f"{t['emoji']} {t['title']} · {t['cnt']}", callback_data=f"topic:{t['id']}")
    b.button(text="🔎 Пошук за словом", callback_data="search")
    b.button(text="📦 Мої покупки", callback_data="mine")
    b.button(text="📲 Ми у Viber", callback_data="viber")
    b.adjust(1)
    return b.as_markup()


def kb_topic(topic_id: int) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    for s in get_catalog_subtopics(topic_id):
        b.button(text=f"📂 {s['title']} · {s['cnt']}", callback_data=f"sub:{s['id']}:0")
    b.button(text="⬅️ До тем", callback_data="menu")
    b.adjust(1)
    return b.as_markup()


def question_lines(qs, telegram_id):
    lines = []
    for q in qs:
        mark = "✅" if has_purchased(telegram_id, "question", q["id"]) else f"{q['price']} грн"
        lines.append(f"<b>{q['code']}</b> · {mark}\n{esc(q['title'])}")
    return "\n\n".join(lines)


def kb_question_buttons(qs, telegram_id, extra_rows=()):
    b = InlineKeyboardBuilder()
    for q in qs:
        bought = has_purchased(telegram_id, "question", q["id"])
        b.button(text=("✅ " if bought else "") + q["code"], callback_data=f"q:{q['id']}")
    b.adjust(4)
    for row in extra_rows:
        b.row(*row)
    return b.as_markup()


def subtopic_screen(subtopic_id: int, telegram_id: int, page: int = 0):
    sub = get_subtopic(subtopic_id)
    qs = get_questions_by_subtopic(subtopic_id)
    if not sub or not qs:
        return None, None
    pages = (len(qs) - 1) // QUESTIONS_PER_PAGE + 1
    page = max(0, min(page, pages - 1))
    chunk = qs[page * QUESTIONS_PER_PAGE:(page + 1) * QUESTIONS_PER_PAGE]
    note = f" (стор. {page + 1}/{pages})" if pages > 1 else ""
    text = (f"{sub['emoji']} {esc(sub['topic_title'])} → <b>{esc(sub['title'])}</b>{note}\n\n"
            f"{question_lines(chunk, telegram_id)}\n\n"
            "👇 Натисніть номер, щоб відкрити опис питання.")
    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton(text="⬅️", callback_data=f"sub:{subtopic_id}:{page - 1}"))
    if page < pages - 1:
        nav.append(InlineKeyboardButton(text="➡️", callback_data=f"sub:{subtopic_id}:{page + 1}"))
    rows = []
    if nav:
        rows.append(nav)
    rows.append([InlineKeyboardButton(text="⬅️ До підтем", callback_data=f"topic:{sub['topic_id']}"),
                 InlineKeyboardButton(text="🏠 Меню", callback_data="menu")])
    return text, kb_question_buttons(chunk, telegram_id, rows)


def question_card(q: dict, telegram_id: int):
    g = question_content(q) or {}
    bought = has_purchased(telegram_id, "question", q["id"])
    inside = [s[0] for s in g.get("sections", [])]
    extras = [EXTRA_NAMES[e] for e in g.get("extras", []) if e in EXTRA_NAMES]
    parts = [f"<b>{q.get('code') or ''} · {esc(q['title'])}</b>"]
    if g.get("who"):
        parts.append(f"👤 Для кого: {esc(g['who'])}")
    if g.get("situation"):
        parts.append(f"<b>Ситуація</b>\n{tg_safe(g['situation'])}")
    if g.get("answer"):
        parts.append(f"<b>Коротко</b>\n{esc(first_sentence(g['answer']))} <i>Повна відповідь — у PDF.</i>")
    if inside:
        body = "\n".join(f"▫️ {esc(x)}" for x in inside + ["Джерела з посиланнями на пункти"])
        parts.append(f"<b>Що всередині</b>\n{body}")
    if extras:
        parts.append("➕ Додатково: " + ", ".join(extras))
    if g.get("norms"):
        parts.append(f"📎 {esc(g['norms'])}")
    parts.append("✅ Ви вже купили це питання." if bought else
                 f"💳 Ціна: <b>{q['price']} грн</b> · PDF з вашим ID, можна друкувати й підшивати.")
    text = "\n\n".join(parts)
    if len(text) > 4000:
        text = text[:3990] + "…"

    b = InlineKeyboardBuilder()
    if bought:
        b.button(text="📄 Отримати PDF", callback_data=f"showq:{q['id']}")
    else:
        b.button(text=f"💳 Купити за {q['price']} грн", callback_data=f"buyq:{q['id']}")
    if q.get("subtopic_id"):
        b.button(text="⬅️ До списку", callback_data=f"sub:{q['subtopic_id']}:0")
    b.button(text="🏠 Меню", callback_data="menu")
    b.adjust(1, 2)
    return text, b.as_markup()


def kb_buy_question(pay_url: str, question_id: int, price) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    b.button(text=f"💳 Перейти до оплати ({price} грн)", url=pay_url)
    b.button(text="✅ Я вже оплатив(ла)", callback_data=f"checkq:{question_id}")
    b.button(text="⬅️ Назад", callback_data=f"q:{question_id}")
    b.adjust(1)
    return b.as_markup()


# =====================================================================
# ВИДАЧА PDF
# =====================================================================

async def send_question_pdf(message: Message, q: dict, telegram_id: int):
    """Персоналізований PDF. Якщо генерація впала — матеріал усе одно
    надсилаємо текстом: покупець не лишається без оплаченої відповіді."""
    back = InlineKeyboardBuilder()
    back.button(text="🏠 Меню", callback_data="menu")
    try:
        g = question_content(q)
        if g:
            buf, ref = generate_nk_pdf(g, telegram_id)
            filename = build_nk_filename(g, ref)
        else:
            buf, ref = generate_question_pdf(q, telegram_id)
            filename = build_pdf_filename(q, ref)
        await message.answer_document(
            BufferedInputFile(buf.read(), filename=filename),
            caption=f"✅ <b>{esc(q.get('code') or '')} · {esc(q['title'])}</b>\n\n"
                    "Ваш персоналізований матеріал 👆",
            reply_markup=back.as_markup(),
        )
        log.info(f"PDF надіслано: question_id={q['id']}, user={telegram_id}")
    except Exception as e:
        log.exception(f"PDF не згенеровано для question_id={q['id']}, user={telegram_id}: {e}")
        g = question_content(q) or {}
        text = f"<b>{esc(q['title'])}</b>\n\n{tg_safe(g.get('answer') or q.get('answer', ''))}"
        await message.answer(text[:4000] + "\n\n⚠️ PDF тимчасово недоступний — напишіть нам, надішлемо вручну.",
                             reply_markup=back.as_markup())


# =====================================================================
# /start (з deep link), /menu, /gp, /mystatus, /info, /help
# =====================================================================

WELCOME = (
    "👋 Привіт, <b>{name}</b>!\n\n"
    "Це <b>Бухгалтерські лайфхаки</b> — «Гарячі питання» з першоджерелами:\n"
    "▫️ норма з цитатою і пунктом кодексу\n"
    "▫️ покроково, як діяти\n"
    "▫️ приклад з цифрами\n"
    "▫️ типові помилки — наслідки — як виправити\n\n"
    "Кожне питання — окремий PDF, який не соромно підшити до нормативки. "
    "Ціна — 99, 149 або 199 грн залежно від обсягу.\n\n"
    "Оберіть тему або просто напишіть слово для пошуку (наприклад: <i>штраф</i>, <i>Etsy</i>, <i>аванс</i>) 👇"
)


@dp.message(CommandStart())
async def cmd_start(message: Message, command: CommandObject):
    user = message.from_user
    upsert_user(telegram_id=user.id, username=user.username, full_name=user.full_name)
    arg = (command.args or "").strip().lower()
    log.info(f"/start {user.id} (@{user.username}) arg={arg!r}")
    if arg.startswith("gp"):
        q = get_question_by_code(arg)
        if q:
            text, kb = question_card(q, user.id)
            await message.answer(text, reply_markup=kb, disable_web_page_preview=True)
            return
    await message.answer(WELCOME.format(name=esc(user.first_name or "")), reply_markup=kb_main_menu())


@dp.message(Command("menu"))
async def cmd_menu(message: Message):
    await message.answer("📚 <b>Оберіть тему:</b>", reply_markup=kb_main_menu())


@dp.message(Command("gp"))
async def cmd_gp(message: Message, command: CommandObject):
    q = get_question_by_code(command.args or "")
    if not q:
        await message.answer("Напишіть номер питання, наприклад: <code>/gp 01</code>")
        return
    text, kb = question_card(q, message.from_user.id)
    await message.answer(text, reply_markup=kb)


async def purchases_screen(telegram_id: int):
    ids = []
    for p in get_user_purchases(telegram_id):
        if p.get("item_type") == "question" and p.get("item_id") not in ids:
            ids.append(p["item_id"])
    qs = [q for q in (get_question_by_id(i) for i in ids) if q]
    if not qs:
        return ("Ви ще нічого не купували.\nОберіть тему або напишіть слово для пошуку 👇", kb_main_menu())
    b = InlineKeyboardBuilder()
    for q in qs:
        label = q.get("code") or f"#{q['id']}"
        b.button(text=f"📄 {label}", callback_data=f"showq:{q['id']}")
    b.adjust(4)
    b.row(InlineKeyboardButton(text="🏠 Меню", callback_data="menu"))
    lines = "\n".join(f"<b>{esc(q.get('code') or '')}</b> {esc(q['title'])}" for q in qs)
    return f"📦 <b>Ваші покупки ({len(qs)})</b>\n\n{lines}\n\nНатисніть номер — надішлемо PDF ще раз.", b.as_markup()


@dp.message(Command("mystatus"))
async def cmd_status(message: Message):
    text, kb = await purchases_screen(message.from_user.id)
    await message.answer(text, reply_markup=kb)


@dp.message(Command("info"))
async def cmd_info(message: Message):
    """Інформація про виконавця (вимога LiqPay) — дані не змінювати без потреби."""
    await message.answer(
        "ℹ️ <b>Про нас</b>\n\n"
        "🤖 <b>Бухгалтерські лайфхаки</b> — практичні відповіді на бухгалтерські питання без води.\n\n"
        "👤 Виконавець: ФОП Кирушок Наталія Юріївна\n"
        "📍 Рівненська обл., м. Березне\n"
        "🆔 ЄДРПОУ/РНОКПП: 2834418688\n"
        "📧 Email: nexus.kursy.finance@gmail.com\n"
        "📞 Телефон: +38 (098) 409-22-09\n\n"
        "💼 <b>Що ми пропонуємо:</b>\n"
        "• «Гарячі питання» — практичні розбори з нормами, прикладами та помилками: ПДВ, ФОП, бухоблік\n"
        "• Вартість одного питання: 99–199 грн залежно від обсягу\n"
        "• Оплата — карткою через LiqPay, PDF надсилається одразу після оплати\n\n"
        f"📄 <a href=\"{OFFER_URL}\">Договір публічної оферти</a>\n\n"
        "Команди бота:\n"
        "/start — головне меню\n"
        "/info — інформація про компанію\n"
        "/help — довідка з користування",
        disable_web_page_preview=True,
    )


@dp.message(Command("help"))
async def cmd_help(message: Message):
    await message.answer(
        "🆘 <b>Довідка</b>\n\n"
        "<b>Як користуватись ботом:</b>\n"
        "1️⃣ /start — меню тем\n"
        "2️⃣ Оберіть тему і підтему — побачите список питань з цінами\n"
        "3️⃣ Натисніть номер питання — відкриється опис і що всередині\n"
        "4️⃣ «Купити» → оплата карткою через LiqPay\n"
        "5️⃣ Після оплати бот надішле PDF\n\n"
        "🔎 Щоб знайти питання, просто напишіть слово в чат: <i>штраф</i>, <i>аванс</i>, <i>Etsy</i>.\n"
        "Відкрити питання за номером: <code>/gp 01</code>\n\n"
        "<b>Команди:</b>\n"
        "/start — головне меню\n"
        "/mystatus — мої покупки\n"
        "/info — інформація про компанію\n"
        "/viber — ми у Viber\n"
        "/help — ця довідка\n\n"
        "Питання чи проблеми з оплатою?\n"
        "📧 nexus.kursy.finance@gmail.com\n"
        "📞 +38 (098) 409-22-09"
    )


# =====================================================================
# НАВІГАЦІЯ
# =====================================================================

async def safe_edit(call: CallbackQuery, text: str, kb):
    """edit_text, а якщо повідомлення — документ/фото, то нове повідомлення."""
    try:
        await call.message.edit_text(text, reply_markup=kb, disable_web_page_preview=True)
    except Exception:
        await call.message.answer(text, reply_markup=kb, disable_web_page_preview=True)


@dp.callback_query(F.data == "menu")
async def cb_menu(call: CallbackQuery):
    await safe_edit(call, "📚 <b>Оберіть тему:</b>", kb_main_menu())
    await call.answer()


@dp.callback_query(F.data == "mine")
async def cb_mine(call: CallbackQuery):
    text, kb = await purchases_screen(call.from_user.id)
    await safe_edit(call, text, kb)
    await call.answer()


def kb_viber() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📢 Viber-канал", url=VIBER_CHANNEL_URL)],
        [InlineKeyboardButton(text="💬 Viber-спільнота · чат", url=VIBER_CHAT_URL)],
        [InlineKeyboardButton(text="🏠 Меню", callback_data="menu")],
    ])


VIBER_TEXT = ("📲 <b>Бухгалтерські лайфхаки у Viber</b>\n\n"
              "📢 <b>Канал</b> — ті самі гарячі питання з нормою і прикладом.\n"
              "💬 <b>Спільнота</b> — обговорюємо ПДВ, ФОП і звітність разом.\n\n"
              "Повні розбори (PDF) — тут, у боті.")


@dp.callback_query(F.data == "viber")
async def cb_viber(call: CallbackQuery):
    await safe_edit(call, VIBER_TEXT, kb_viber())
    await call.answer()


@dp.message(Command("viber"))
async def cmd_viber(message: Message):
    await message.answer(VIBER_TEXT, reply_markup=kb_viber(), disable_web_page_preview=True)


@dp.callback_query(F.data == "search")
async def cb_search(call: CallbackQuery):
    await call.message.answer("🔎 Напишіть одне-два слова, наприклад: <i>штраф ПН</i>, <i>аванс</i>, "
                              "<i>Payoneer</i>, <i>дисконтування</i>.")
    await call.answer()


@dp.callback_query(F.data.startswith("topic:"))
async def cb_topic(call: CallbackQuery):
    topic_id = int(call.data.split(":")[1])
    topic = {t["id"]: t for t in get_catalog_topics()}.get(topic_id)
    if not topic:
        await call.answer("Тему не знайдено", show_alert=True)
        return
    await safe_edit(call, f"{topic['emoji']} <b>{esc(topic['title'])}</b>\n\nОберіть підтему:",
                    kb_topic(topic_id))
    await call.answer()


@dp.callback_query(F.data.startswith("sub:"))
async def cb_subtopic(call: CallbackQuery):
    _, sid, page = call.data.split(":")
    text, kb = subtopic_screen(int(sid), call.from_user.id, int(page))
    if not text:
        await call.answer("Питань у цій підтемі ще немає.", show_alert=True)
        return
    await safe_edit(call, text, kb)
    await call.answer()


# Сумісність зі старими кнопками в історії чатів
@dp.callback_query(F.data.startswith("subtopic:") | F.data.startswith("qpage:"))
async def cb_old_nav(call: CallbackQuery):
    await safe_edit(call, "Каталог оновлено 🙌\n\n📚 <b>Оберіть тему:</b>", kb_main_menu())
    await call.answer()


@dp.callback_query(F.data.startswith("q:") | F.data.startswith("backtoq:"))
async def cb_question(call: CallbackQuery):
    q = get_question_by_id(int(call.data.split(":")[1]))
    if not q or (not q.get("is_active") and not has_purchased(call.from_user.id, "question", q["id"])):
        await call.answer("Питання не знайдено", show_alert=True)
        return
    text, kb = question_card(q, call.from_user.id)
    await safe_edit(call, text, kb)
    await call.answer()


# =====================================================================
# ПОКУПКА І ВИДАЧА
# =====================================================================

@dp.callback_query(F.data.startswith("showq:"))
async def cb_show_question(call: CallbackQuery):
    question_id = int(call.data.split(":")[1])
    if not has_purchased(call.from_user.id, "question", question_id):
        await call.answer("🔒 Це питання ще не оплачено!", show_alert=True)
        return
    q = get_question_by_id(question_id)
    if not q:
        await call.answer("Питання не знайдено", show_alert=True)
        return
    if not check_rate_limit(call.from_user.id, "pdf", 15):
        await call.answer("⏳ Забагато запитів. Спробуйте за кілька хвилин.", show_alert=True)
        return
    await call.answer("Готую PDF…")
    await send_question_pdf(call.message, q, call.from_user.id)


@dp.callback_query(F.data.startswith("buyq:"))
async def cb_buy_question(call: CallbackQuery):
    question_id = int(call.data.split(":")[1])
    telegram_id = call.from_user.id
    if has_purchased(telegram_id, "question", question_id):
        await call.answer("✅ Це питання вже куплено!", show_alert=True)
        return
    q = get_question_by_id(question_id)
    if not q or not q.get("is_active"):
        await call.answer("Питання не знайдено", show_alert=True)
        return
    if not check_rate_limit(telegram_id, "buy_attempt", 20):
        await call.answer("⏳ Забагато спроб. Зачекайте трохи.", show_alert=True)
        return

    # користувач міг прийти зі старої кнопки, не натискаючи /start
    upsert_user(telegram_id=telegram_id, username=call.from_user.username, full_name=call.from_user.full_name)
    order_id = f"q{question_id}_{telegram_id}_{uuid.uuid4().hex[:8]}"
    price = q["price"]
    create_payment(telegram_id=telegram_id, order_id=order_id, amount=price,
                   item_type="question", item_id=question_id)
    code = q.get("code") or f"#{question_id}"
    title = q["title"] if len(q["title"]) <= 90 else q["title"][:89] + "…"
    pay_url = generate_payment_url(order_id=order_id, telegram_id=telegram_id, amount=price,
                                   description=f"Гаряче питання {code}: {title}")
    log.info(f"Замовлення {order_id}: user {telegram_id}, {code}, {price} грн")
    await safe_edit(
        call,
        f"💳 <b>Оплата {esc(code)} ({price} грн)</b>\n\n"
        f"{esc(q['title'])}\n\n"
        "1️⃣ Натисніть кнопку нижче й оплатіть карткою через LiqPay\n"
        "2️⃣ Поверніться сюди — бот надішле PDF автоматично\n\n"
        f"📄 Оплачуючи, ви погоджуєтесь з <a href=\"{OFFER_URL}\">умовами договору оферти</a>.",
        kb_buy_question(pay_url, question_id, price),
    )
    await call.answer()


async def sync_payments_from_liqpay(telegram_id: int, question_id: int) -> bool:
    """Якщо callback від LiqPay не дійшов — питаємо статус платежу в LiqPay напряму."""
    for p in get_pending_payments(telegram_id, "question", question_id):
        resp = await asyncio.to_thread(check_payment_status, p["order_id"])
        status = (resp or {}).get("status")
        log.info(f"LiqPay status {p['order_id']}: {status}")
        if status in ("success", "sandbox"):
            confirm_payment(p["order_id"], json.dumps(resp, ensure_ascii=False), paid_amount=resp.get("amount"))
            return True
    return False


@dp.callback_query(F.data.startswith("checkq:"))
async def cb_check_question_payment(call: CallbackQuery):
    question_id = int(call.data.split(":")[1])
    if not has_purchased(call.from_user.id, "question", question_id):
        await sync_payments_from_liqpay(call.from_user.id, question_id)
    if has_purchased(call.from_user.id, "question", question_id):
        q = get_question_by_id(question_id)
        await call.answer("✅ Оплату підтверджено!")
        await send_question_pdf(call.message, q, call.from_user.id)
    else:
        await call.answer("❌ Оплату ще не знайдено.\n\nЯкщо ви щойно оплатили, зачекайте 1–2 хвилини "
                          "та натисніть ще раз.", show_alert=True)


# =====================================================================
# АДМІН: /stats і черга публікацій /queue
# =====================================================================

@dp.message(Command("stats"))
async def cmd_stats(message: Message):
    if not is_admin(message.from_user.id):
        await message.answer("⛔ Немає доступу.")
        return
    s = get_stats()
    await message.answer(
        "📊 <b>Статистика</b>\n\n"
        f"👥 Користувачів у боті: <b>{s['users']}</b>\n"
        f"✅ Успішних продажів: <b>{s['sales']}</b>\n"
        f"💰 Загальний дохід: <b>{s['revenue']:.2f} грн</b>\n"
        f"⏳ Очікують оплати: <b>{s['pending']}</b>\n\n"
        f"📈 Конверсія: <b>{(s['sales'] / max(s['users'], 1) * 100):.1f}%</b>"
    )


def utc_now_str():
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M")


def kyiv_str(utc_str):
    if not utc_str:
        return ""
    dt = datetime.strptime(utc_str, "%Y-%m-%d %H:%M").replace(tzinfo=timezone.utc)
    return dt.astimezone(KYIV).strftime("%d.%m %H:%M")


def next_free_slot():
    """Найближчий вільний слот з POST_SLOTS (за Києвом) → рядок UTC."""
    busy = busy_slots()
    now = datetime.now(KYIV)
    day = now.date()
    for _ in range(60):
        for s in POST_SLOTS:
            h, m = (int(x) for x in s.split(":"))
            local = datetime(day.year, day.month, day.day, h, m, tzinfo=KYIV)
            if local <= now + timedelta(minutes=5):
                continue
            utc = local.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M")
            if utc not in busy:
                return utc
        day += timedelta(days=1)
    return None


async def post_caption(p: dict) -> str:
    uname = await bot_username()
    num = gp_num(p.get("gp_code"))
    link = f'<a href="https://t.me/{uname}?start=gp{num}">відкрити в боті</a>'
    text = esc(p["text"])
    if "[посилання]" in text:
        text = text.replace("[посилання]", link)
    else:
        text += f"\n\n👉 {link}"
    return text


async def send_post(chat_id, p: dict):
    """Надсилає пост (альбом карток + підпис). Повертає message_id першого повідомлення."""
    caption = await post_caption(p)
    media_files = [os.path.join(MEDIA_DIR, m) for m in json.loads(p.get("media") or "[]")]
    media_files = [m for m in media_files if os.path.exists(m)][:10]
    plain_len = len(re.sub(r"<[^>]+>", "", caption))
    if not media_files:
        msg = await bot.send_message(chat_id, caption, disable_web_page_preview=True)
        return msg.message_id
    if plain_len <= 1024:
        group = [InputMediaPhoto(media=FSInputFile(m), caption=caption if i == 0 else None,
                                 parse_mode=ParseMode.HTML if i == 0 else None)
                 for i, m in enumerate(media_files)]
        msgs = await bot.send_media_group(chat_id, group)
        return msgs[0].message_id
    msgs = await bot.send_media_group(chat_id, [InputMediaPhoto(media=FSInputFile(m)) for m in media_files])
    await bot.send_message(chat_id, caption, disable_web_page_preview=True)
    return msgs[0].message_id


def queue_screen():
    posts = list_posts()
    b = InlineKeyboardBuilder()
    lines = []
    for p in posts:
        when = f" · {kyiv_str(p['scheduled_at'])}" if p["status"] == "scheduled" else ""
        lines.append(f"<b>{esc(p['gp_code'] or p['post_key'])}</b> — {STATUS_UA.get(p['status'], p['status'])}{when}")
        if p["status"] != "published":
            b.button(text=f"👁 {p['gp_code'] or p['id']}", callback_data=f"pv:{p['id']}")
    b.adjust(4)
    b.row(InlineKeyboardButton(text="🔄 Оновити", callback_data="queue"))
    ch = esc(CHANNEL_ID) if CHANNEL_ID else "⚠️ не задано (змінна CHANNEL_ID)"
    head = (f"🗂 <b>Черга публікацій</b>\nКанал: {ch}\nСлоти (Київ): {', '.join(POST_SLOTS)}\n\n")
    return head + ("\n".join(lines) or "Постів немає."), b.as_markup()


def kb_post(p: dict):
    b = InlineKeyboardBuilder()
    if p["status"] != "published":
        b.button(text="🚀 Опублікувати зараз", callback_data=f"pub:{p['id']}")
        if p["status"] == "scheduled":
            b.button(text="❌ Зняти з розкладу", callback_data=f"unsch:{p['id']}")
        else:
            b.button(text="🗓 У найближчий слот", callback_data=f"sch:{p['id']}")
        if p["status"] != "hold":
            b.button(text="⏸ Відкласти", callback_data=f"hold:{p['id']}")
    b.button(text="↩️ До черги", callback_data="queue")
    b.adjust(1)
    return b.as_markup()


def post_status_text(p):
    extra = ""
    if p["status"] == "scheduled":
        extra = f" на {kyiv_str(p['scheduled_at'])} (Київ)"
    if p["status"] == "published":
        extra = f" {kyiv_str(p.get('published_at'))}"
    note = ""
    if p["status"] == "hold":
        note = ("\n\n⏸ Пост не піде за розкладом, доки ви не натиснете «Опублікувати» або «У слот»."
                "\nГП-06 і ГП-09 відкладені навмисно: спершу перевірте пункти «перевірити» з протоколу.")
    return f"<b>{esc(p['gp_code'] or '')}</b> — {STATUS_UA.get(p['status'], p['status'])}{extra}{note}"


@dp.message(Command("queue"))
async def cmd_queue(message: Message):
    if not is_admin(message.from_user.id):
        return
    text, kb = queue_screen()
    await message.answer(text, reply_markup=kb)


@dp.callback_query(F.data == "queue")
async def cb_queue(call: CallbackQuery):
    if not is_admin(call.from_user.id):
        await call.answer()
        return
    text, kb = queue_screen()
    await safe_edit(call, text, kb)
    await call.answer()


@dp.callback_query(F.data.startswith("pv:"))
async def cb_preview(call: CallbackQuery):
    if not is_admin(call.from_user.id):
        await call.answer()
        return
    p = get_post(int(call.data.split(":")[1]))
    if not p:
        await call.answer("Пост не знайдено", show_alert=True)
        return
    await call.answer("Надсилаю превʼю…")
    try:
        await send_post(call.from_user.id, p)
    except Exception as e:
        log.exception("preview failed")
        await call.message.answer(f"⚠️ Превʼю не вдалося: {esc(str(e))}")
    await call.message.answer("👆 Так пост виглядатиме в каналі.\n\n" + post_status_text(p), reply_markup=kb_post(p))


async def publish(p: dict):
    if not CHANNEL_ID:
        raise RuntimeError("CHANNEL_ID не задано")
    mid = await send_post(CHANNEL_ID, p)
    update_post(p["id"], status="published", published_at=utc_now_str(), message_id=str(mid))
    log.info(f"Опубліковано пост {p['post_key']} → {CHANNEL_ID} (msg {mid})")


@dp.callback_query(F.data.startswith(("pub:", "sch:", "unsch:", "hold:")))
async def cb_post_action(call: CallbackQuery):
    if not is_admin(call.from_user.id):
        await call.answer()
        return
    action, pid = call.data.split(":")
    p = get_post(int(pid))
    if not p:
        await call.answer("Пост не знайдено", show_alert=True)
        return
    if p["status"] == "published":
        await call.answer("Цей пост уже опубліковано.", show_alert=True)
        return
    if action == "pub":
        try:
            await publish(p)
            await call.answer("✅ Опубліковано")
        except Exception as e:
            log.exception("publish failed")
            await call.answer(f"⚠️ {e}"[:190], show_alert=True)
            return
    elif action == "sch":
        slot = next_free_slot()
        update_post(p["id"], status="scheduled", scheduled_at=slot)
        await call.answer(f"🗓 Заплановано на {kyiv_str(slot)}")
    elif action == "unsch":
        update_post(p["id"], status="draft", scheduled_at=None)
        await call.answer("Знято з розкладу")
    elif action == "hold":
        update_post(p["id"], status="hold", scheduled_at=None)
        await call.answer("⏸ Відкладено")
    p = get_post(p["id"])
    await safe_edit(call, post_status_text(p), kb_post(p))


async def scheduler_loop():
    """Раз на хвилину публікує заплановані пости, час яких настав."""
    while True:
        try:
            if CHANNEL_ID:
                for p in due_posts(utc_now_str()):
                    try:
                        await publish(p)
                        if ADMIN_ID:
                            await bot.send_message(ADMIN_ID, f"✅ За розкладом опубліковано: <b>{esc(p['gp_code'] or '')}</b>")
                    except Exception as e:
                        log.exception("scheduled publish failed")
                        update_post(p["id"], status="error")
                        if ADMIN_ID:
                            await bot.send_message(ADMIN_ID, f"⚠️ Не вдалося опублікувати {esc(p['gp_code'] or '')}: "
                                                             f"{esc(str(e))[:300]}\nВідкрийте /queue")
        except Exception:
            log.exception("scheduler tick failed")
        await asyncio.sleep(60)


# =====================================================================
# ПОШУК: будь-який текст (не команда) — пошуковий запит
# =====================================================================

@dp.message(F.text & ~F.text.startswith("/"))
async def text_search(message: Message):
    query = message.text.strip()
    q_direct = get_question_by_code(query) if re.fullmatch(r"(?i)\s*(гп|gp)?[\s-]*\d{1,3}\s*", query) else None
    if q_direct:
        text, kb = question_card(q_direct, message.from_user.id)
        await message.answer(text, reply_markup=kb)
        return
    if not check_rate_limit(message.from_user.id, "search", 30, window_minutes=5):
        return
    found = search_questions(query, limit=8)
    if not found:
        await message.answer(
            f"🔎 За запитом «{esc(query[:50])}» нічого не знайшлося.\n"
            "Спробуйте інше слово (<i>штраф</i>, <i>аванс</i>, <i>ФОП</i>) або оберіть тему:",
            reply_markup=kb_main_menu())
        return
    rows = [[InlineKeyboardButton(text="🏠 Меню", callback_data="menu")]]
    await message.answer(
        f"🔎 Знайдено за запитом «{esc(query[:50])}»:\n\n{question_lines(found, message.from_user.id)}",
        reply_markup=kb_question_buttons(found, message.from_user.id, rows))


# =====================================================================
# ЗАПУСК
# =====================================================================

async def main():
    init_db()
    asyncio.create_task(scheduler_loop())
    log.info("🤖 Бот v2 запущено, очікую повідомлення…")
    await dp.start_polling(bot, skip_updates=True)


if __name__ == "__main__":
    asyncio.run(main())
