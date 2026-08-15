"""
Головний файл Telegram-бота «Бухгалтерські лайфхаки»
Фреймворк: aiogram 3.x
Модель продажу: кожне гаряче питання продається окремо за власною ціною.
Видача матеріалу: персоналізований PDF з водяним знаком (pdf_generator.py).
Запуск: python bot.py
"""
import asyncio
import logging
import os
import re
import uuid
from io import BytesIO

from aiogram import Bot, Dispatcher, F
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.filters import CommandStart, Command
from aiogram.types import (
    Message, CallbackQuery,
    InlineKeyboardMarkup, InlineKeyboardButton, BufferedInputFile,
)
from aiogram.utils.keyboard import InlineKeyboardBuilder

from dotenv import load_dotenv

# Завантажуємо .env до запуску
load_dotenv()

from database import (
    init_db, upsert_user, get_user,
    get_all_topics, get_subtopics, get_questions_by_topic, get_question_by_id,
    create_payment, confirm_payment, get_payment_by_order,
    check_rate_limit, get_stats,
    has_purchased, get_user_purchases,
)
from liqpay_helper import generate_payment_url
from pdf_generator import generate_question_pdf, build_pdf_filename

# --- Налаштування ---
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
)
log = logging.getLogger(__name__)

BOT_TOKEN = os.getenv("BOT_TOKEN")
if not BOT_TOKEN:
    raise RuntimeError("❌ BOT_TOKEN не знайдено у .env файлі!")

bot = Bot(token=BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
dp  = Dispatcher()

# Ціна тепер індивідуальна для кожного питання (беремо з q['price']) —
# більше немає єдиної фіксованої суми на всі питання.
# ID адміністратора — отримай свій через @userinfobot у Telegram
ADMIN_ID = int(os.getenv("ADMIN_ID", "0"))

# Скільки питань показувати на одному екрані списку (пагінація)
QUESTIONS_PER_PAGE = 6

# Посилання на публічну оферту — показуємо перед кожною оплатою
OFFER_URL = os.getenv(
    "OFFER_URL",
    "https://docs.google.com/document/d/1R28gdhIqzg1-DjVdcVWJ6bhzDH5aUbbEn6rfPBT8Whs/view",
)

# =====================================================================
# КЛАВІАТУРИ
# =====================================================================

def kb_main_menu() -> InlineKeyboardMarkup:
    """Головне меню: список тем.
    Продажу «всього пакету одразу» немає — кожне питання купується окремо."""
    builder = InlineKeyboardBuilder()
    topics = get_all_topics()
    for t in topics:
        builder.button(
            text=f"{t['emoji']} {t['title']}",
            callback_data=f"topic:{t['id']}"
        )
    builder.adjust(1)
    return builder.as_markup()


def kb_topic(topic_id: int) -> InlineKeyboardMarkup:
    """Меню конкретної теми: підтеми + назад"""
    builder = InlineKeyboardBuilder()
    subtopics = get_subtopics(topic_id)
    for s in subtopics:
        builder.button(
            text=f"📂 {s['title']}",
            callback_data=f"subtopic:{topic_id}:{s['id']}:0"
        )
    builder.button(text="⬅️ Назад до тем", callback_data="menu")
    builder.adjust(1)
    return builder.as_markup()


def kb_question_list(topic_id: int, subtopic_id: int, telegram_id: int, page: int = 0) -> InlineKeyboardMarkup:
    """Список питань підтеми з пагінацією (по QUESTIONS_PER_PAGE штук на екран).
    Куплені питання позначені ✅, некуплені — 🔒 з ціною."""
    builder = InlineKeyboardBuilder()
    all_questions = get_questions_by_topic(topic_id)
    qs = [q for q in all_questions if q["subtopic_id"] == subtopic_id]

    total = len(qs)
    start = page * QUESTIONS_PER_PAGE
    end = start + QUESTIONS_PER_PAGE
    page_qs = qs[start:end]

    for q in page_qs:
        short = q["question"]
        if len(short) > 45:
            short = short[:42] + "…"
        if has_purchased(telegram_id, "question", q["id"]):
            builder.button(text=f"✅ {short}", callback_data=f"showq:{q['id']}")
        else:
            builder.button(text=f"🔒 {short} — {q['price']} грн", callback_data=f"buyq:{q['id']}")
    builder.adjust(1)

    # Рядок навігації сторінками — показуємо, лише якщо є куди гортати
    nav_row = []
    if page > 0:
        nav_row.append(InlineKeyboardButton(
            text="⬅️ Назад", callback_data=f"qpage:{topic_id}:{subtopic_id}:{page-1}"
        ))
    if end < total:
        nav_row.append(InlineKeyboardButton(
            text="Далі ➡️", callback_data=f"qpage:{topic_id}:{subtopic_id}:{page+1}"
        ))
    if nav_row:
        builder.row(*nav_row)

    builder.row(InlineKeyboardButton(text="⬅️ Назад до теми", callback_data=f"topic:{topic_id}"))
    builder.row(InlineKeyboardButton(text="🏠 Головне меню",  callback_data="menu"))
    return builder.as_markup()


def kb_after_answer(topic_id: int, subtopic_id: int) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="⬅️ Назад до питань", callback_data=f"subtopic:{topic_id}:{subtopic_id}")
    builder.button(text="🏠 Головне меню",    callback_data="menu")
    builder.adjust(1)
    return builder.as_markup()


def kb_buy_question(pay_url: str, question_id: int, price) -> InlineKeyboardMarkup:
    """Кнопки на екрані покупки конкретного питання"""
    builder = InlineKeyboardBuilder()
    builder.button(text=f"💳 Перейти до оплати ({price} грн)", url=pay_url)
    builder.button(text="✅ Я вже оплатив(ла)", callback_data=f"checkq:{question_id}")
    builder.button(text="⬅️ Назад", callback_data=f"backtoq:{question_id}")
    builder.adjust(1)
    return builder.as_markup()


# =====================================================================
# ВИДАЧА МАТЕРІАЛУ: персоналізований PDF (захист від перепродажу)
# =====================================================================

async def send_question_pdf(
    message: Message,
    q: dict,
    telegram_id: int,
    topic_id: int,
    subtopic_id: int,
):
    """
    Генерує персоналізований PDF (водяний знак з Telegram ID покупця,
    прихований ідентифікатор у футері, метадані файлу) і надсилає його
    як документ у чат.

    Підстраховка: якщо генерація PDF з якоїсь причини не вдалась —
    клієнт УСЕ ОДНО отримує оплачений матеріал, просто текстом.
    Клієнт ніколи не повинен лишитися без оплаченої відповіді
    через технічну помилку генератора PDF.
    """
    try:
        pdf_buffer, order_ref = generate_question_pdf(q, telegram_id)
        filename = build_pdf_filename(q, order_ref)
        document = BufferedInputFile(pdf_buffer.read(), filename=filename)
        await message.answer_document(
            document,
            caption=(
                f"✅ <b>{q['question']}</b>\n\n"
                "Ваш персоналізований матеріал у файлі вище 👆"
            ),
            reply_markup=kb_after_answer(topic_id, subtopic_id),
        )
        log.info(f"PDF надіслано: question_id={q['id']}, user={telegram_id}")
    except Exception as e:
        log.error(
            f"Не вдалося згенерувати/надіслати PDF для question_id={q['id']}, "
            f"user={telegram_id}: {e}. Надсилаю текстом як підстраховку."
        )
        await message.answer(
            f"<b>❓ {q['question']}</b>\n\n✅ {q['answer']}",
            reply_markup=kb_after_answer(topic_id, subtopic_id),
        )


# =====================================================================
# HANDLERS: /start, /menu, /mystatus
# =====================================================================

@dp.message(CommandStart())
async def cmd_start(message: Message):
    """Обробник команди /start — реєстрація + привітання"""
    user = message.from_user

    upsert_user(
        telegram_id=user.id,
        username=user.username,
        full_name=user.full_name,
    )

    log.info(f"Новий старт: {user.id} (@{user.username})")

    await message.answer(
        f"👋 Привіт, <b>{user.first_name}</b>!\n\n"
        "Ласкаво просимо до <b>Бухгалтерських лайфхаків</b> 🧾\n\n"
        "Тут ти знайдеш <b>практичні гарячі питання</b> з бухобліку:\n"
        "• 💰 Податки та штрафи\n"
        "• 👩‍💼 Зарплата та кадри\n"
        "• 🏦 ЄСВ та звітність\n\n"
        f"💡 Обери тему нижче. Кожне питання має свою ціну (99–199 грн залежно від складності) — "
        "платиш тільки за те, що реально потрібно 👇",
        reply_markup=kb_main_menu(),
    )


@dp.message(Command("menu"))
async def cmd_menu(message: Message):
    """Команда /menu — показує головне меню"""
    await message.answer("📚 <b>Оберіть тему:</b>", reply_markup=kb_main_menu())


@dp.message(Command("mystatus"))
async def cmd_status(message: Message):
    """Команда /mystatus — скільки питань уже куплено"""
    purchases = get_user_purchases(message.from_user.id)
    if purchases:
        await message.answer(
            f"✅ У вас куплено <b>{len(purchases)}</b> гарячих питань.\n"
            "Оберіть тему через /menu, щоб переглянути їх або купити нові."
        )
    else:
        await message.answer(
            "❌ Ви ще нічого не купували.\n"
            "💳 Ціна кожного гарячого питання вказана поруч з ним — обирайте тему через /menu"
        )


# =====================================================================
# HANDLERS: /info, /help (інформація про компанію — вимога LiqPay)
# =====================================================================

@dp.message(Command("info"))
async def cmd_info(message: Message):
    """Команда /info — інформація про компанію (вимога LiqPay для активації прийому оплат)"""
    await message.answer(
        "ℹ️ <b>Про нас</b>\n\n"
        "🤖 <b>Бухгалтерські лайфхаки</b> — практичні відповіді на бухгалтерські питання без води.\n\n"
        "👤 Виконавець: ФОП Кирушок Наталія Юріївна\n"
        "📍 Рівненська обл., м. Березне\n"
        "🆔 ЄДРПОУ/РНОКПП: 2834418688\n"
        "📧 Email: nexus.kursy.finance@gmail.com\n"
        "📞 Телефон: +38 (098) 409-22-09\n\n"
        "💼 <b>Що ми пропонуємо:</b>\n"
        "• «Гарячі питання» — короткі практичні відповіді по темах: податки, зарплата, ЄСВ, ФОП, "
        "розрахунки, первинка\n"
        "• Вартість одного питання: 99–199 грн залежно від складності теми\n"
        "• Оплата — карткою через LiqPay, доступ надається одразу після оплати\n\n"
        f"📄 <a href=\"{OFFER_URL}\">Договір публічної оферти</a>\n\n"
        "Команди бота:\n"
        "/start — головне меню\n"
        "/info — інформація про компанію\n"
        "/help — довідка з користування",
        disable_web_page_preview=True,
    )


@dp.message(Command("help"))
async def cmd_help(message: Message):
    """Команда /help — довідка з користування (вимога LiqPay для активації прийому оплат)"""
    await message.answer(
        "🆘 <b>Довідка</b>\n\n"
        "<b>Як користуватись ботом:</b>\n"
        "1️⃣ Натисніть /start — з'явиться меню тем\n"
        "2️⃣ Оберіть тему, що вас цікавить\n"
        "3️⃣ Оберіть конкретне гаряче питання — побачите ціну\n"
        "4️⃣ Натисніть «Перейти до оплати» і оплатіть карткою через LiqPay\n"
        "5️⃣ Після оплати відповідь відкриється автоматично у форматі PDF\n\n"
        "<b>Команди:</b>\n"
        "/start — головне меню та список тем\n"
        "/info — інформація про компанію та контакти\n"
        "/help — ця довідка\n\n"
        "Питання чи проблеми з оплатою?\n"
        "📧 nexus.kursy.finance@gmail.com\n"
        "📞 +38 (098) 409-22-09"
    )


# =====================================================================
# CALLBACKS: навігація
# =====================================================================

@dp.callback_query(F.data == "menu")
async def cb_menu(call: CallbackQuery):
    await call.message.edit_text(
        "📚 <b>Оберіть тему:</b>",
        reply_markup=kb_main_menu()
    )
    await call.answer()


@dp.callback_query(F.data.startswith("topic:"))
async def cb_topic(call: CallbackQuery):
    """Відкриває меню конкретної теми — перегляд тем і підтем безкоштовний"""
    topic_id = int(call.data.split(":")[1])

    topics = {t["id"]: t for t in get_all_topics()}
    topic  = topics.get(topic_id)
    if not topic:
        await call.answer("Тему не знайдено", show_alert=True)
        return

    await call.message.edit_text(
        f"{topic['emoji']} <b>{topic['title']}</b>\n\n"
        "Оберіть підтему:",
        reply_markup=kb_topic(topic_id)
    )
    await call.answer()


@dp.callback_query(F.data.startswith("subtopic:"))
async def cb_subtopic(call: CallbackQuery):
    """Показує список питань підтеми — куплені відкриті, некуплені з цінником.
    callback_data: subtopic:{topic_id}:{subtopic_id}:{page}"""
    parts = call.data.split(":")
    topic_id    = int(parts[1])
    subtopic_id = int(parts[2])
    page        = int(parts[3]) if len(parts) > 3 else 0

    all_questions = get_questions_by_topic(topic_id)
    qs = [q for q in all_questions if q["subtopic_id"] == subtopic_id]

    if not qs:
        await call.answer("Питань у цій підтемі ще немає.", show_alert=True)
        return

    subtopics = get_subtopics(topic_id)
    sub_map   = {s["id"]: s["title"] for s in subtopics}
    sub_title = sub_map.get(subtopic_id, "Підтема")

    total = len(qs)
    pages_count = (total - 1) // QUESTIONS_PER_PAGE + 1
    page_note = f" (стор. {page + 1}/{pages_count})" if pages_count > 1 else ""

    await call.message.edit_text(
        f"📂 <b>{sub_title}</b>{page_note}\n\n"
        f"👇 Обери питання. Ціна вказана біля кожного, куплені відкриваються одразу.",
        reply_markup=kb_question_list(topic_id, subtopic_id, call.from_user.id, page)
    )
    await call.answer()


@dp.callback_query(F.data.startswith("qpage:"))
async def cb_question_page(call: CallbackQuery):
    """Гортання сторінок списку питань підтеми (кнопки ⬅️ Назад / Далі ➡️).
    callback_data: qpage:{topic_id}:{subtopic_id}:{page}"""
    _, topic_id_str, subtopic_id_str, page_str = call.data.split(":")
    topic_id    = int(topic_id_str)
    subtopic_id = int(subtopic_id_str)
    page        = int(page_str)

    all_questions = get_questions_by_topic(topic_id)
    qs = [q for q in all_questions if q["subtopic_id"] == subtopic_id]

    subtopics = get_subtopics(topic_id)
    sub_map   = {s["id"]: s["title"] for s in subtopics}
    sub_title = sub_map.get(subtopic_id, "Підтема")

    total = len(qs)
    pages_count = (total - 1) // QUESTIONS_PER_PAGE + 1
    page_note = f" (стор. {page + 1}/{pages_count})" if pages_count > 1 else ""

    await call.message.edit_text(
        f"📂 <b>{sub_title}</b>{page_note}\n\n"
        f"👇 Обери питання. Ціна вказана біля кожного, куплені відкриваються одразу.",
        reply_markup=kb_question_list(topic_id, subtopic_id, call.from_user.id, page)
    )
    await call.answer()


# =====================================================================
# CALLBACKS: перегляд купленого питання (видача PDF)
# =====================================================================

@dp.callback_query(F.data.startswith("showq:"))
async def cb_show_question(call: CallbackQuery):
    """Надсилає персоналізований PDF з питанням+відповіддю, якщо воно вже куплене.

    Примітка: раніше відповідь показувалась текстом прямо в цьому ж
    повідомленні (edit_text). Тепер PDF надсилається НОВИМ повідомленням-
    документом — редагувати повідомлення у файл Telegram не дозволяє."""
    question_id = int(call.data.split(":")[1])

    if not has_purchased(call.from_user.id, "question", question_id):
        await call.answer("🔒 Це питання ще не оплачено!", show_alert=True)
        return

    q = get_question_by_id(question_id)
    if not q:
        await call.answer("Питання не знайдено", show_alert=True)
        return

    await call.answer()
    await send_question_pdf(
        call.message, q, call.from_user.id, q["topic_id"], q["subtopic_id"]
    )


# =====================================================================
# CALLBACKS: покупка окремого питання
# =====================================================================

@dp.callback_query(F.data.startswith("buyq:"))
async def cb_buy_question(call: CallbackQuery):
    """Показує екран покупки конкретного питання"""
    question_id = int(call.data.split(":")[1])
    telegram_id = call.from_user.id

    if has_purchased(telegram_id, "question", question_id):
        await call.answer("✅ Це питання вже куплено!", show_alert=True)
        return

    q = get_question_by_id(question_id)
    if not q:
        await call.answer("Питання не знайдено", show_alert=True)
        return

    if not check_rate_limit(telegram_id, "buy_attempt", 20):
        await call.answer("⏳ Забагато спроб. Зачекайте трохи.", show_alert=True)
        return

    # Генеруємо унікальний order_id саме для цього питання
    order_id = f"q{question_id}_{telegram_id}_{uuid.uuid4().hex[:8]}"
    price = q["price"]

    create_payment(
        telegram_id=telegram_id,
        order_id=order_id,
        amount=price,
        item_type="question",
        item_id=question_id,
    )

    short_desc = q["question"]
    if len(short_desc) > 60:
        short_desc = short_desc[:57] + "…"

    pay_url = generate_payment_url(
        order_id=order_id,
        telegram_id=telegram_id,
        amount=price,
        description=f"Гаряче питання: {short_desc}",
    )

    log.info(f"Створено замовлення {order_id} для user {telegram_id}, question {question_id}, ціна {price} грн")

    await call.message.edit_text(
        f"💳 <b>Оплата питання ({price} грн)</b>\n\n"
        f"❓ {q['question']}\n\n"
        "1️⃣ Натисни кнопку нижче\n"
        "2️⃣ Оплати карткою на сайті LiqPay\n"
        "3️⃣ Поверніться сюди — доступ відкриється автоматично\n\n"
        "⚡ Після оплати ти одразу отримаєш <b>персоналізований PDF</b> з відповіддю!\n\n"
        f"📄 Оплачуючи, ви погоджуєтесь з <a href=\"{OFFER_URL}\">умовами договору оферти</a>.",
        reply_markup=kb_buy_question(pay_url, question_id, price),
        disable_web_page_preview=True,
    )
    await call.answer()


@dp.callback_query(F.data.startswith("checkq:"))
async def cb_check_question_payment(call: CallbackQuery):
    """Ручна перевірка оплати конкретного питання.
    У продакшені доступ відкривається автоматично через webhook LiqPay —
    ця кнопка потрібна на випадок, якщо людина повернулась раніше, ніж прийшов webhook.

    Після підтвердження оплати надсилає персоналізований PDF (замість тексту)."""
    question_id = int(call.data.split(":")[1])

    if has_purchased(call.from_user.id, "question", question_id):
        q = get_question_by_id(question_id)
        await call.answer("✅ Оплату підтверджено!", show_alert=True)
        await send_question_pdf(
            call.message, q, call.from_user.id, q["topic_id"], q["subtopic_id"]
        )
    else:
        await call.answer(
            "❌ Оплату ще не знайдено.\n\n"
            "Якщо ти щойно оплатив(ла), зачекай 1-2 хвилини та спробуй ще раз.",
            show_alert=True
        )


@dp.callback_query(F.data.startswith("backtoq:"))
async def cb_back_to_question(call: CallbackQuery):
    """Повернення з екрану оплати назад до списку питань підтеми"""
    question_id = int(call.data.split(":")[1])
    q = get_question_by_id(question_id)
    if not q:
        await call.message.edit_text("📚 <b>Оберіть тему:</b>", reply_markup=kb_main_menu())
        await call.answer()
        return

    all_questions = get_questions_by_topic(q["topic_id"])
    qs = [x for x in all_questions if x["subtopic_id"] == q["subtopic_id"]]
    subtopics = get_subtopics(q["topic_id"])
    sub_map   = {s["id"]: s["title"] for s in subtopics}
    sub_title = sub_map.get(q["subtopic_id"], "Підтема")

    await call.message.edit_text(
        f"📂 <b>{sub_title}</b>\n\n"
        f"👇 Обери питання. Ціна вказана біля кожного, куплені відкриваються одразу.",
        reply_markup=kb_question_list(q["topic_id"], q["subtopic_id"], call.from_user.id, page=0)
    )
    await call.answer()


# =====================================================================
# АДМІН: /stats
# =====================================================================

@dp.message(Command("stats"))
async def cmd_stats(message: Message):
    """Адмін-команда /stats — статистика продажів (тільки для ADMIN_ID)"""
    if ADMIN_ID and message.from_user.id != ADMIN_ID:
        await message.answer("⛔ Немає доступу.")
        return

    s = get_stats()
    await message.answer(
        "📊 <b>Статистика</b>\n\n"
        f"👥 Користувачів у боті: <b>{s['users']}</b>\n"
        f"✅ Успішних продажів: <b>{s['sales']}</b>\n"
        f"💰 Загальний дохід: <b>{s['revenue']:.2f} грн</b>\n"
        f"⏳ Очікують оплати: <b>{s['pending']}</b>\n\n"
        f"📈 Конверсія: <b>{(s['sales']/max(s['users'],1)*100):.1f}%</b>"
    )


# =====================================================================
# ЗАПУСК
# =====================================================================

async def main():
    """Ініціалізація БД та запуск polling"""
    init_db()
    log.info("🤖 Бот запущено, очікую повідомлення...")
    await dp.start_polling(bot, skip_updates=True)


if __name__ == "__main__":
    asyncio.run(main())
