"""
database.py
Схема бази даних проєкту "Бухгалтерські курси / Гарячі питання"

Ця версія СУМІСНА з існуючим bot.py (з функціями upsert_user, get_all_topics,
has_purchased, get_stats тощо) і водночас зберігає гнучку структуру тем,
яку ми вже наповнили 27 імпортованими питаннями.

При першому запуску після оновлення файл сам домиграє існуючу базу:
- додасть emoji для тем;
- створить підтему "Усі питання" для кожної теми;
- прив'яже вже імпортовані питання до цієї підтеми;
- підготує таблицю rate_limits для захисту від спаму.

Нічого не видаляється і не перезаписується — тільки додається.

Запуск: python database.py
"""

import os
import sqlite3
from datetime import datetime, timedelta

DATABASE_URL = os.environ.get("DATABASE_URL")
USE_POSTGRES = bool(DATABASE_URL)

if USE_POSTGRES:
    import psycopg2
    import psycopg2.extras


def get_connection():
    if USE_POSTGRES:
        conn = psycopg2.connect(DATABASE_URL, sslmode="require")
        conn.cursor_factory = psycopg2.extras.RealDictCursor
        return conn
    else:
        conn = sqlite3.connect("bukhkursy.db")
        conn.row_factory = sqlite3.Row
        return conn


def _rows_to_dicts(rows):
    return [dict(r) for r in rows]


def _row_to_dict(row):
    return dict(row) if row is not None else None


# ──────────────────────────────────────────────────────────────────────────
# Базова схема (як і раніше — CREATE TABLE IF NOT EXISTS, безпечно повторно)
# ──────────────────────────────────────────────────────────────────────────

SCHEMA_SQLITE = """
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    telegram_id INTEGER UNIQUE NOT NULL,
    username TEXT,
    full_name TEXT,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS topics (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    slug TEXT UNIQUE NOT NULL,
    sort_order INTEGER DEFAULT 100,
    is_active INTEGER DEFAULT 1,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS subtopics (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    topic_id INTEGER NOT NULL REFERENCES topics(id),
    name TEXT NOT NULL,
    slug TEXT NOT NULL,
    sort_order INTEGER DEFAULT 100,
    is_active INTEGER DEFAULT 1,
    UNIQUE(topic_id, slug)
);

CREATE TABLE IF NOT EXISTS questions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    topic_id INTEGER NOT NULL REFERENCES topics(id),
    subtopic_id INTEGER REFERENCES subtopics(id),
    slug TEXT UNIQUE NOT NULL,
    title TEXT NOT NULL,
    block_audience TEXT NOT NULL,
    block_problem TEXT NOT NULL,
    block_solution TEXT NOT NULL,
    block_example TEXT NOT NULL,
    block_mistakes TEXT NOT NULL,
    block_checklist TEXT NOT NULL,
    block_sources TEXT NOT NULL,
    price INTEGER NOT NULL DEFAULT 99,
    is_active INTEGER DEFAULT 1,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS tags (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT UNIQUE NOT NULL,
    slug TEXT UNIQUE NOT NULL
);

CREATE TABLE IF NOT EXISTS question_tags (
    question_id INTEGER NOT NULL REFERENCES questions(id),
    tag_id INTEGER NOT NULL REFERENCES tags(id),
    PRIMARY KEY (question_id, tag_id)
);

CREATE TABLE IF NOT EXISTS payments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    order_id TEXT UNIQUE NOT NULL,
    user_id INTEGER NOT NULL REFERENCES users(telegram_id),
    item_type TEXT NOT NULL DEFAULT 'question',
    item_id INTEGER NOT NULL,
    amount REAL NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',
    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
    paid_at TEXT
);

CREATE TABLE IF NOT EXISTS rate_limits (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    telegram_id INTEGER NOT NULL,
    action TEXT NOT NULL,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP
);
"""

SCHEMA_POSTGRES = SCHEMA_SQLITE.replace(
    "INTEGER PRIMARY KEY AUTOINCREMENT", "SERIAL PRIMARY KEY"
).replace(
    "TEXT DEFAULT CURRENT_TIMESTAMP", "TIMESTAMP DEFAULT CURRENT_TIMESTAMP"
)


def init_db():
    conn = get_connection()
    cur = conn.cursor()
    schema = SCHEMA_POSTGRES if USE_POSTGRES else SCHEMA_SQLITE
    if USE_POSTGRES:
        cur.execute(schema)
    else:
        cur.executescript(schema)
    conn.commit()
    conn.close()
    migrate()
    seed_default_topics()
    ensure_default_subtopics()


# ──────────────────────────────────────────────────────────────────────────
# Домиграція існуючої бази (безпечно повторюваний виклик)
# ──────────────────────────────────────────────────────────────────────────

def _safe_add_column(cur, table, column_def):
    """Додає колонку, якщо вона ще не існує. Ігнорує помилку 'вже є'."""
    try:
        cur.execute(f"ALTER TABLE {table} ADD COLUMN {column_def}")
    except Exception:
        pass  # колонка вже існує — це нормально


EMOJI_MAP = {
    "Податки та звітність": "📊",
    "Зарплата та ЄСВ": "👩‍💼",
    "ФОП: реєстрація та статус": "🧾",
    "Розрахунки, платежі, банк": "🏦",
    "Первинка та господарські операції": "📦",
    "Інше": "📁",
}


def migrate():
    conn = get_connection()
    cur = conn.cursor()

    # 1. emoji для тем
    _safe_add_column(cur, "topics", "emoji TEXT")
    conn.commit()

    cur.execute("SELECT id, name, emoji FROM topics")
    for row in cur.fetchall():
        row = dict(row)
        if not row.get("emoji"):
            emoji = EMOJI_MAP.get(row["name"], "📁")
            placeholder = "%s" if USE_POSTGRES else "?"
            cur.execute(
                f"UPDATE topics SET emoji = {placeholder} WHERE id = {placeholder}",
                (emoji, row["id"]),
            )
    conn.commit()

    # 2. payments: item_type / item_id (для старих БД, де могли бути тільки question_id)
    _safe_add_column(cur, "payments", "item_type TEXT DEFAULT 'question'")
    _safe_add_column(cur, "payments", "item_id INTEGER")
    conn.commit()
    try:
        cur.execute("SELECT id, question_id, item_id FROM payments")
        for row in cur.fetchall():
            row = dict(row)
            if row.get("item_id") is None and row.get("question_id") is not None:
                placeholder = "%s" if USE_POSTGRES else "?"
                cur.execute(
                    f"UPDATE payments SET item_id = {placeholder}, item_type = 'question' WHERE id = {placeholder}",
                    (row["question_id"], row["id"]),
                )
        conn.commit()
    except Exception:
        pass  # старої колонки question_id могло й не бути — це ок

    conn.close()


def ensure_default_subtopics():
    """
    Для кожної теми гарантує наявність хоча б однієї підтеми ("Усі питання").
    Питання без підтеми (subtopic_id IS NULL) прив'язуються до неї автоматично.
    Це дозволяє bot.py одразу показувати меню тема → підтема → питання,
    навіть якщо ви ще не ділили питання на детальніші підтеми вручну.
    """
    conn = get_connection()
    cur = conn.cursor()
    placeholder = "%s" if USE_POSTGRES else "?"

    cur.execute("SELECT id, name FROM topics WHERE is_active = 1")
    topics = _rows_to_dicts(cur.fetchall())

    for topic in topics:
        cur.execute(
            f"SELECT id FROM subtopics WHERE topic_id = {placeholder} AND slug = 'usi-pytannia'",
            (topic["id"],),
        )
        existing = cur.fetchone()
        if existing:
            default_subtopic_id = dict(existing)["id"]
        else:
            cur.execute(
                f"INSERT INTO subtopics (topic_id, name, slug, sort_order) "
                f"VALUES ({placeholder}, {placeholder}, 'usi-pytannia', 0)",
                (topic["id"], "Усі питання"),
            )
            conn.commit()
            cur.execute(
                f"SELECT id FROM subtopics WHERE topic_id = {placeholder} AND slug = 'usi-pytannia'",
                (topic["id"],),
            )
            default_subtopic_id = dict(cur.fetchone())["id"]

        # Прив'язуємо "сирітські" питання (subtopic_id IS NULL) цієї теми
        cur.execute(
            f"UPDATE questions SET subtopic_id = {placeholder} "
            f"WHERE topic_id = {placeholder} AND subtopic_id IS NULL",
            (default_subtopic_id, topic["id"]),
        )
        conn.commit()

    conn.close()


DEFAULT_TOPICS = [
    ("Податки та звітність", "podatky-zvitnist", 10),
    ("Зарплата та ЄСВ", "zarplata-esv", 20),
    ("ФОП: реєстрація та статус", "fop-reyestratsiya", 30),
    ("Розрахунки, платежі, банк", "rozrahunky-bank", 40),
    ("Первинка та господарські операції", "pervynka-hospoperatsii", 50),
    ("Інше", "inshe", 999),
]


def seed_default_topics():
    conn = get_connection()
    cur = conn.cursor()
    for name, slug, sort_order in DEFAULT_TOPICS:
        if USE_POSTGRES:
            cur.execute(
                """INSERT INTO topics (name, slug, sort_order, emoji)
                   VALUES (%s, %s, %s, %s) ON CONFLICT (slug) DO NOTHING""",
                (name, slug, sort_order, EMOJI_MAP.get(name, "📁")),
            )
        else:
            cur.execute(
                """INSERT OR IGNORE INTO topics (name, slug, sort_order, emoji)
                   VALUES (?, ?, ?, ?)""",
                (name, slug, sort_order, EMOJI_MAP.get(name, "📁")),
            )
    conn.commit()
    conn.close()


# ──────────────────────────────────────────────────────────────────────────
# Формування "answer" з 7 блоків для показу в боті (bot.py очікує q['answer'])
# ──────────────────────────────────────────────────────────────────────────

def _format_answer(q):
    return (
        f"👤 <b>Кому це актуально:</b>\n{q['block_audience']}\n\n"
        f"⚠️ <b>Суть проблеми:</b>\n{q['block_problem']}\n\n"
        f"✅ <b>Як діяти правильно:</b>\n{q['block_solution']}\n\n"
        f"📋 <b>Приклад з практики:</b>\n{q['block_example']}\n\n"
        f"❌ <b>Типові помилки:</b>\n{q['block_mistakes']}\n\n"
        f"📌 <b>Короткий чеклист:</b>\n{q['block_checklist']}\n\n"
        f"📎 <b>Джерело / нормативна база:</b>\n{q['block_sources']}"
    )


def _enrich_question(q):
    """Додає q['question'] і q['answer'] — поля, які очікує bot.py,
    не втрачаючи оригінальні block_* поля."""
    q = dict(q)
    q["question"] = q["title"]
    q["answer"] = _format_answer(q)
    return q


# ──────────────────────────────────────────────────────────────────────────
# Користувачі
# ──────────────────────────────────────────────────────────────────────────

def upsert_user(telegram_id, username, full_name):
    conn = get_connection()
    cur = conn.cursor()
    if USE_POSTGRES:
        cur.execute(
            """INSERT INTO users (telegram_id, username, full_name)
               VALUES (%s, %s, %s)
               ON CONFLICT (telegram_id) DO UPDATE
               SET username = EXCLUDED.username, full_name = EXCLUDED.full_name""",
            (telegram_id, username, full_name),
        )
    else:
        cur.execute(
            """INSERT INTO users (telegram_id, username, full_name)
               VALUES (?, ?, ?)
               ON CONFLICT(telegram_id) DO UPDATE
               SET username = excluded.username, full_name = excluded.full_name""",
            (telegram_id, username, full_name),
        )
    conn.commit()
    conn.close()


def get_user(telegram_id):
    conn = get_connection()
    cur = conn.cursor()
    placeholder = "%s" if USE_POSTGRES else "?"
    cur.execute(f"SELECT * FROM users WHERE telegram_id = {placeholder}", (telegram_id,))
    row = cur.fetchone()
    conn.close()
    return _row_to_dict(row)


# ──────────────────────────────────────────────────────────────────────────
# Теми, підтеми, питання (сумісні з bot.py)
# ──────────────────────────────────────────────────────────────────────────

def get_all_topics():
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        "SELECT id, name AS title, emoji, slug FROM topics "
        "WHERE is_active = 1 ORDER BY sort_order, name"
    )
    rows = _rows_to_dicts(cur.fetchall())
    conn.close()
    return rows


def get_subtopics(topic_id):
    conn = get_connection()
    cur = conn.cursor()
    placeholder = "%s" if USE_POSTGRES else "?"
    cur.execute(
        f"SELECT id, name AS title, slug FROM subtopics "
        f"WHERE topic_id = {placeholder} AND is_active = 1 ORDER BY sort_order, name",
        (topic_id,),
    )
    rows = _rows_to_dicts(cur.fetchall())
    conn.close()
    return rows


def get_questions_by_topic(topic_id):
    conn = get_connection()
    cur = conn.cursor()
    placeholder = "%s" if USE_POSTGRES else "?"
    cur.execute(
        f"SELECT * FROM questions WHERE topic_id = {placeholder} AND is_active = 1 ORDER BY title",
        (topic_id,),
    )
    rows = _rows_to_dicts(cur.fetchall())
    conn.close()
    return [_enrich_question(r) for r in rows]


def get_question_by_id(question_id):
    conn = get_connection()
    cur = conn.cursor()
    placeholder = "%s" if USE_POSTGRES else "?"
    cur.execute(f"SELECT * FROM questions WHERE id = {placeholder}", (question_id,))
    row = cur.fetchone()
    conn.close()
    if row is None:
        return None
    return _enrich_question(dict(row))


# ──────────────────────────────────────────────────────────────────────────
# Платежі та покупки
# ──────────────────────────────────────────────────────────────────────────

def create_payment(telegram_id, order_id, amount, item_type, item_id):
    conn = get_connection()
    cur = conn.cursor()
    placeholder = "%s" if USE_POSTGRES else "?"
    cur.execute(
        f"""INSERT INTO payments (order_id, user_id, item_type, item_id, amount, status)
            VALUES ({placeholder}, {placeholder}, {placeholder}, {placeholder}, {placeholder}, 'pending')""",
        (order_id, telegram_id, item_type, item_id, amount),
    )
    conn.commit()
    conn.close()


def confirm_payment(order_id):
    conn = get_connection()
    cur = conn.cursor()
    placeholder = "%s" if USE_POSTGRES else "?"
    cur.execute(
        f"""UPDATE payments SET status = 'success', paid_at = {placeholder}
            WHERE order_id = {placeholder}""",
        (datetime.now().isoformat(), order_id),
    )
    conn.commit()
    conn.close()


def get_payment_by_order(order_id):
    conn = get_connection()
    cur = conn.cursor()
    placeholder = "%s" if USE_POSTGRES else "?"
    cur.execute(f"SELECT * FROM payments WHERE order_id = {placeholder}", (order_id,))
    row = cur.fetchone()
    conn.close()
    return _row_to_dict(row)


def has_purchased(telegram_id, item_type, item_id):
    conn = get_connection()
    cur = conn.cursor()
    placeholder = "%s" if USE_POSTGRES else "?"
    cur.execute(
        f"""SELECT 1 FROM payments
            WHERE user_id = {placeholder} AND item_type = {placeholder}
            AND item_id = {placeholder} AND status = 'success' LIMIT 1""",
        (telegram_id, item_type, item_id),
    )
    row = cur.fetchone()
    conn.close()
    return row is not None


def get_user_purchases(telegram_id):
    conn = get_connection()
    cur = conn.cursor()
    placeholder = "%s" if USE_POSTGRES else "?"
    cur.execute(
        f"""SELECT * FROM payments WHERE user_id = {placeholder} AND status = 'success'
            ORDER BY paid_at DESC""",
        (telegram_id,),
    )
    rows = _rows_to_dicts(cur.fetchall())
    conn.close()
    return rows


# ──────────────────────────────────────────────────────────────────────────
# Rate limiting (захист від спаму кнопкою "оплатити")
# ──────────────────────────────────────────────────────────────────────────

def check_rate_limit(telegram_id, action, limit, window_minutes=10):
    """Повертає True, якщо дію можна виконати (ліміт не перевищено),
    і одразу логує спробу. limit — максимум дій за window_minutes хвилин."""
    conn = get_connection()
    cur = conn.cursor()
    placeholder = "%s" if USE_POSTGRES else "?"

    since = (datetime.now() - timedelta(minutes=window_minutes)).isoformat()
    cur.execute(
        f"""SELECT COUNT(*) AS cnt FROM rate_limits
            WHERE telegram_id = {placeholder} AND action = {placeholder}
            AND created_at >= {placeholder}""",
        (telegram_id, action, since),
    )
    count = dict(cur.fetchone())["cnt"]

    if count >= limit:
        conn.close()
        return False

    cur.execute(
        f"INSERT INTO rate_limits (telegram_id, action) VALUES ({placeholder}, {placeholder})",
        (telegram_id, action),
    )
    conn.commit()
    conn.close()
    return True


# ──────────────────────────────────────────────────────────────────────────
# Статистика для /stats
# ──────────────────────────────────────────────────────────────────────────

def get_stats():
    conn = get_connection()
    cur = conn.cursor()

    cur.execute("SELECT COUNT(*) AS cnt FROM users")
    users = dict(cur.fetchone())["cnt"]

    cur.execute("SELECT COUNT(*) AS cnt FROM payments WHERE status = 'success'")
    sales = dict(cur.fetchone())["cnt"]

    cur.execute("SELECT COALESCE(SUM(amount), 0) AS total FROM payments WHERE status = 'success'")
    revenue = dict(cur.fetchone())["total"] or 0

    cur.execute("SELECT COUNT(*) AS cnt FROM payments WHERE status = 'pending'")
    pending = dict(cur.fetchone())["cnt"]

    conn.close()
    return {"users": users, "sales": sales, "revenue": float(revenue), "pending": pending}


if __name__ == "__main__":
    init_db()
    print("База даних ініціалізована й домигрована.")
    print("Додано: emoji для тем, підтема 'Усі питання' для кожної теми,")
    print("прив'язка існуючих питань, таблиця rate_limits.")
    print("Тепер bot.py повинен коректно працювати з наявними 27 питаннями.")
