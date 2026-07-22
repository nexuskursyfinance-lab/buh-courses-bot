"""
Головний файл Telegram-бота «Бухгалтерські курси»
Фреймворк: aiogram 3.x
Запуск: python bot.py
"""
import asyncio
import logging
import os
import uuid
from io import BytesIO

from aiogram import Bot, Dispatcher, F
from aiogram.enums import ParseMode
from aiogram.filters import CommandStart, Command
from aiogram.types import (
    Message, CallbackQuery,
    InlineKeyboardMarkup, InlineKeyboardButton,
    BufferedInputFile,
)
from aiogram.utils.keyboard import InlineKeyboardBuilder

from dotenv import load_dotenv

# Завантажуємо .env до запуску
load_dotenv()

from database import (
    init_db, upsert_user, get_user,
    get_all_topics, get_subtopics, get_questions_by_topic,
    create_payment, confirm_payment, get_payment_by_order,
    check_rate_limit, get_stats,
)
from liqpay_helper import generate_payment_url
from pdf_generator import generate_pdf

# --- Налаштування ---
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
)
log = logging.getLogger(__name__)

BOT_TOKEN = os.getenv("BOT_TOKEN")
if not BOT_TOKEN:
    raise RuntimeError("❌ BOT_TOKEN не знайдено у .env файлі!")

bot = Bot(token=BOT_TOKEN, parse_mode=ParseMode.HTML)
dp  = Dispatcher()

PRICE_TEXT = "99 грн"
# ID адміністратора — отримай свій через @userinfobot у Telegram
ADMIN_ID   = int(os.getenv("ADMIN_ID", "0"))
# Ліміт: скільки разів на годину користувач може запитати PDF
PDF_RATE_LIMIT = int(os.getenv("PDF_RATE_LIMIT", "10"))

# =====================================================================
# КЛАВІАТУРИ
# =====================================================================

def kb_main_menu() -> InlineKeyboardMarkup:
    """Головне меню: список тем + кнопка купити"""
    builder = InlineKeyboardBuilder()
    topics = get_all_topics()
    for t in topics:
        builder.button(
            text=f"{t['emoji']} {t['title']}",
            callback_data=f"topic:{t['id']}"
        )
    builder.button(text=f"💳 Купити всі теми за {PRICE_TEXT}", callback_data="buy")
    builder.adjust(1)  # по одній кнопці в рядку
    return builder.as_markup()


def kb_topic(topic_id: int) -> InlineKeyboardMarkup:
    """Меню конкретної теми: підтеми + назад"""
    builder = InlineKeyboardBuilder()
    subtopics = get_subtopics(topic_id)
    for s in subtopics:
        builder.button(
            text=f"📂 {s['title']}",
            callback_data=f"subtopic:{topic_id}:{s['id']}"
        )
    # Кнопка «отримати всі питання теми одним PDF»
    builder.button(
        text="📥 Отримати всі питання теми (PDF)",
        callback_data=f"pdf:{topic_id}"
    )
    builder.button(text="⬅️ Назад до тем", callback_data="menu")
    builder.adjust(1)
    return builder.as_markup()


def kb_buy() -> InlineKeyboardMarkup:
    """Кнопки на екрані покупки"""
    builder = InlineKeyboardBuilder()
    builder.button(text=f"💳 Оплатити {PRICE_TEXT}", callback_data="confirm_buy")
    builder.button(text="⬅️ Назад", callback_data="menu")
    builder.adjust(1)
    return builder.as_markup()


def kb_after_buy(pay_url: str) -> InlineKeyboardMarkup:
    """Кнопка-посилання на оплату LiqPay"""
    builder = InlineKeyboardBuilder()
    builder.button(text=f"💳 Перейти до оплати ({PRICE_TEXT})", url=pay_url)
    builder.button(text="✅ Я вже оплатив(ла)", callback_data="check_payment")
    builder.button(text="⬅️ Назад", callback_data="menu")
    builder.adjust(1)
    return builder.as_markup()


# =====================================================================
# HANDLERS: /start та /help
# =====================================================================

@dp.message(CommandStart())
async def cmd_start(message: Message):
    """Обробник команди /start — реєстрація + привітання"""
    user = message.from_user

    # Зберігаємо або оновлюємо користувача в БД
    upsert_user(
        telegram_id=user.id,
        username=user.username,
        full_name=user.full_name,
    )

    log.info(f"Новий старт: {user.id} (@{user.username})")

    await message.answer(
        f"👋 Привіт, <b>{user.first_name}</b>!\n\n"
        "Ласкаво просимо до <b>Бухгалтерських курсів</b> 📚\n\n"
        "Тут ти знайдеш <b>100+ практичних питань</b> з бухобліку:\n"
        "• 💰 Податки та штрафи\n"
        "• 👩‍💼 Зарплата та кадри\n"
        "• 🏦 ЄСВ та звітність\n\n"
        "💡 Вибери тему нижче або <b>купи повний пакет</b> за 99 грн 👇",
        reply_markup=kb_main_menu(),
    )


@dp.message(Command("menu"))
async def cmd_menu(message: Message):
    """Команда /menu — показує головне меню"""
    await message.answer("📚 <b>Оберіть тему:</b>", reply_markup=kb_main_menu())


@dp.message(Command("mystatus"))
async def cmd_status(message: Message):
    """Команда /mystatus — перевірити чи є доступ"""
    user = get_user(message.from_user.id)
    if user and user["has_access"]:
        await message.answer(
            "✅ У вас є <b>повний доступ</b> до всіх матеріалів!\n"
            "Оберіть тему через /menu"
        )
    else:
        await message.answer(
            "❌ Доступу ще немає.\n"
            f"💳 Придбайте матеріали за <b>{PRICE_TEXT}</b> через /menu → Купити"
        )


# =====================================================================
# CALLBACKS: навігація
# =====================================================================

@dp.callback_query(F.data == "menu")
async def cb_menu(call: CallbackQuery):
    """Повернення до головного меню"""
    await call.message.edit_text(
        "📚 <b>Оберіть тему:</b>",
        reply_markup=kb_main_menu()
    )
    await call.answer()


@dp.callback_query(F.data.startswith("topic:"))
async def cb_topic(call: CallbackQuery):
    """Відкриває меню конкретної теми"""
    topic_id = int(call.data.split(":")[1])

    # Перевіряємо доступ
    user = get_user(call.from_user.id)
    if not user or not user["has_access"]:
        await call.answer(
            "🔒 Ця функція доступна після придбання!\nНатисни «Купити» в меню.",
            show_alert=True
        )
        return

    topics = {t["id"]: t for t in get_all_topics()}
    topic  = topics.get(topic_id)
    if not topic:
        await call.answer("Тему не знайдено", show_alert=True)
        return

    await call.message.edit_text(
        f"{topic['emoji']} <b>{topic['title']}</b>\n\n"
        "Оберіть підтему або завантажте всі питання одразу:",
        reply_markup=kb_topic(topic_id)
    )
    await call.answer()


@dp.callback_query(F.data.startswith("subtopic:"))
async def cb_subtopic(call: CallbackQuery):
    """Показує питання обраної підтеми"""
    _, topic_id_str, subtopic_id_str = call.data.split(":")
    topic_id    = int(topic_id_str)
    subtopic_id = int(subtopic_id_str)

    user = get_user(call.from_user.id)
    if not user or not user["has_access"]:
        await call.answer("🔒 Потрібна оплата!", show_alert=True)
        return

    all_questions = get_questions_by_topic(topic_id)
    # Фільтруємо за підтемою
    qs = [q for q in all_questions if q["subtopic_id"] == subtopic_id]

    if not qs:
        await call.answer("Питань у цій підтемі ще немає.", show_alert=True)
        return

    # Формуємо текстову відповідь
    subtopics = get_subtopics(topic_id)
    sub_map   = {s["id"]: s["title"] for s in subtopics}
    sub_title = sub_map.get(subtopic_id, "Підтема")

    text = f"📂 <b>{sub_title}</b>\n\n"
    for i, q in enumerate(qs, 1):
        text += f"<b>❓ {i}. {q['question']}</b>\n"
        text += f"✅ {q['answer']}\n\n"
        text += "─" * 30 + "\n\n"

    builder = InlineKeyboardBuilder()
    builder.button(text="⬅️ Назад до теми", callback_data=f"topic:{topic_id}")
    builder.button(text="🏠 Головне меню",  callback_data="menu")
    builder.adjust(1)

    await call.message.edit_text(text, reply_markup=builder.as_markup())
    await call.answer()


# =====================================================================
# CALLBACKS: PDF завантаження
# =====================================================================

@dp.callback_query(F.data.startswith("pdf:"))
async def cb_pdf(call: CallbackQuery):
    """Генерує та надсилає PDF з усіма питаннями теми"""
    topic_id = int(call.data.split(":")[1])

    user = get_user(call.from_user.id)
    if not user or not user["has_access"]:
        await call.answer("🔒 Потрібна оплата!", show_alert=True)
        return

    # Перевірка rate limit (max 10 PDF на годину)
    if not check_rate_limit(call.from_user.id, "pdf", PDF_RATE_LIMIT):
        await call.answer(
            f"⏳ Забагато запитів. Зачекайте годину та спробуйте знову.",
            show_alert=True
        )
        return

    await call.answer("⏳ Генерую PDF, зачекайте...")
    await call.message.answer("⏳ Готую ваш PDF...")

    topics = {t["id"]: t for t in get_all_topics()}
    topic  = topics.get(topic_id)
    qs     = get_questions_by_topic(topic_id)

    if not qs:
        await call.message.answer("На жаль, питань для цієї теми ще немає.")
        return

    # Генеруємо PDF
    pdf_buffer = generate_pdf(topic["title"] if topic else "Питання", qs)
    filename   = f"buh_questions_{topic_id}.pdf"

    await call.message.answer_document(
        document=BufferedInputFile(pdf_buffer.read(), filename=filename),
        caption=(
            f"✅ <b>{topic['title']}</b>\n"
            f"📄 {len(qs)} питань з відповідями\n\n"
            "Зберігай файл — він твій назавжди! 🎉"
        )
    )


# =====================================================================
# CALLBACKS: покупка
# =====================================================================

@dp.callback_query(F.data == "buy")
async def cb_buy(call: CallbackQuery):
    """Показує екран покупки"""
    user = get_user(call.from_user.id)

    # Якщо вже оплатив — одразу надаємо доступ до тем
    if user and user["has_access"]:
        await call.answer("✅ У вас вже є доступ!", show_alert=True)
        await call.message.edit_text(
            "✅ У вас вже є <b>повний доступ</b>!\nОберіть тему:",
            reply_markup=kb_main_menu()
        )
        return

    await call.message.edit_text(
        "💳 <b>Придбати повний пакет</b>\n\n"
        "📦 Що ти отримаєш:\n"
        "• 100+ питань з детальними відповідями\n"
        "• 3 теми: Податки, Зарплата, ЄСВ\n"
        "• PDF-файли для кожної теми\n"
        "• Оновлення матеріалів безкоштовно\n\n"
        f"💰 <b>Ціна: {PRICE_TEXT}</b> (одноразово)\n\n"
        "Натисни «Оплатити» — тебе перенаправить на сторінку оплати LiqPay 👇",
        reply_markup=kb_buy()
    )
    await call.answer()


@dp.callback_query(F.data == "confirm_buy")
async def cb_confirm_buy(call: CallbackQuery):
    """Створює замовлення та надсилає посилання на оплату"""
    telegram_id = call.from_user.id

    # Генеруємо унікальний order_id
    order_id = f"buh_{telegram_id}_{uuid.uuid4().hex[:8]}"

    # Зберігаємо очікуваний платіж у БД
    create_payment(telegram_id=telegram_id, order_id=order_id, amount=99.00)

    # Отримуємо URL оплати від LiqPay
    pay_url = generate_payment_url(order_id=order_id, telegram_id=telegram_id)

    log.info(f"Створено замовлення {order_id} для user {telegram_id}")

    await call.message.edit_text(
        f"💳 <b>Оплата {PRICE_TEXT}</b>\n\n"
        "1️⃣ Натисни кнопку нижче\n"
        "2️⃣ Оплати карткою на сайті LiqPay\n"
        "3️⃣ Поверніться сюди — доступ відкриється автоматично\n\n"
        "⚡ Після оплати доступ відкривається <b>миттєво</b>!",
        reply_markup=kb_after_buy(pay_url)
    )
    await call.answer()


@dp.callback_query(F.data == "check_payment")
async def cb_check_payment(call: CallbackQuery):
    """
    Ручна перевірка оплати.
    У продакшені платіж підтверджується автоматично через webhook.
    """
    user = get_user(call.from_user.id)
    if user and user["has_access"]:
        await call.answer("✅ Оплату підтверджено!", show_alert=True)
        await call.message.edit_text(
            "🎉 <b>Дякуємо за покупку!</b>\n\n"
            "Тепер у тебе є доступ до всіх матеріалів.\n"
            "Оберіть тему нижче 👇",
            reply_markup=kb_main_menu()
        )
    else:
        await call.answer(
            "❌ Оплату ще не знайдено.\n\n"
            "Якщо ти щойно оплатив(ла), зачекай 1-2 хвилини та спробуй ще раз.\n"
            "Або напиши нам: @support_username",
            show_alert=True
        )


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
