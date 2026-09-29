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
    # v2: старі сідери (seed_default_topics / ensure_default_subtopics) більше
    # НЕ викликаються — вони щоразу створювали підтеми «Усі питання».
    # Каталог тепер береться з data/catalog.json + data/gp_content.json.
    sync_catalog()


# ──────────────────────────────────────────────────────────────────────────
# Домиграція існуючої бази (безпечно повторюваний виклик)
# ──────────────────────────────────────────────────────────────────────────

def _safe_add_column(cur, table, column_def):
    """Додає колонку, якщо вона ще не існує. Ігнорує помилку 'вже є'."""
    if USE_POSTGRES:
        # IF NOT EXISTS — щоб помилка не «ламала» транзакцію Postgres
        cur.execute(f"ALTER TABLE {table} ADD COLUMN IF NOT EXISTS {column_def}")
        return
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
        conn.rollback()  # старої колонки question_id могло й не бути — це ок

    # 3. v2: нові поля питань, сирий payload оплати, службові таблиці
    _safe_add_column(cur, "questions", "code TEXT")
    _safe_add_column(cur, "questions", "content_json TEXT")
    _safe_add_column(cur, "questions", "search_text TEXT")
    _safe_add_column(cur, "questions", "sort_order INTEGER DEFAULT 100")
    _safe_add_column(cur, "payments", "raw_json TEXT")
    conn.commit()
    pk = "SERIAL PRIMARY KEY" if USE_POSTGRES else "INTEGER PRIMARY KEY AUTOINCREMENT"
    cur.execute(f"""CREATE TABLE IF NOT EXISTS posts (
        id {pk},
        post_key TEXT UNIQUE NOT NULL,
        gp_code TEXT,
        text TEXT NOT NULL,
        media TEXT,
        status TEXT NOT NULL DEFAULT 'draft',
        scheduled_at TEXT,
        published_at TEXT,
        message_id TEXT,
        created_at TEXT
    )""")
    cur.execute("""CREATE TABLE IF NOT EXISTS meta (
        k TEXT PRIMARY KEY,
        v TEXT
    )""")
    conn.commit()
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


def confirm_payment(order_id, raw_json=None, paid_amount=None):
    """Позначає платіж успішним. Повертає telegram_id покупця, якщо платіж
    саме зараз перейшов з 'pending' у 'success', інакше None
    (не знайдено / вже оброблено / сума менша за ціну)."""
    conn = get_connection()
    cur = conn.cursor()
    ph = "%s" if USE_POSTGRES else "?"
    cur.execute(f"SELECT user_id, amount, status FROM payments WHERE order_id = {ph}", (order_id,))
    row = cur.fetchone()
    if row is None:
        conn.close()
        return None
    row = dict(row)
    if row["status"] == "success":
        conn.close()
        return None
    if paid_amount is not None:
        try:
            if float(paid_amount) + 0.001 < float(row["amount"]):
                conn.close()
                return None
        except (TypeError, ValueError):
            pass
    cur.execute(
        f"""UPDATE payments SET status = 'success', paid_at = {ph}, raw_json = {ph}
            WHERE order_id = {ph} AND status <> 'success'""",
        (datetime.now().isoformat(), raw_json, order_id),
    )
    conn.commit()
    conn.close()
    return row["user_id"]


def get_pending_payments(telegram_id, item_type, item_id, limit=5):
    """Незавершені (pending) платежі користувача за конкретний товар, найновіші першими."""
    conn = get_connection()
    cur = conn.cursor()
    p = "%s" if USE_POSTGRES else "?"
    cur.execute(
        f"""SELECT order_id, amount FROM payments
            WHERE user_id = {p} AND item_type = {p} AND item_id = {p} AND status = 'pending'
            ORDER BY id DESC LIMIT {int(limit)}""",
        (telegram_id, item_type, item_id),
    )
    rows = _rows_to_dicts(cur.fetchall())
    conn.close()
    return rows


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


# ──────────────────────────────────────────────────────────────────────────
# v2: каталог «Гарячих питань» з data/*.json (автоімпорт при старті)
# ──────────────────────────────────────────────────────────────────────────

import hashlib
import html as _html
import json
import re

DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")


def _ph():
    return "%s" if USE_POSTGRES else "?"


def meta_get(key):
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(f"SELECT v FROM meta WHERE k = {_ph()}", (key,))
    row = cur.fetchone()
    conn.close()
    return dict(row)["v"] if row else None


def meta_set(key, value):
    conn = get_connection()
    cur = conn.cursor()
    p = _ph()
    if USE_POSTGRES:
        cur.execute(f"INSERT INTO meta (k, v) VALUES ({p}, {p}) "
                    f"ON CONFLICT (k) DO UPDATE SET v = EXCLUDED.v", (key, value))
    else:
        cur.execute(f"INSERT OR REPLACE INTO meta (k, v) VALUES ({p}, {p})", (key, value))
    conn.commit()
    conn.close()


def _strip_tags(t):
    return re.sub(r"<[^>]+>", " ", t or "")


def _search_blob(g):
    parts = [g.get("code", ""), g.get("title", ""), g.get("kicker", ""), g.get("who", ""),
             g.get("norms", ""), g.get("situation", ""), g.get("answer", "")]
    def walk(x):
        if isinstance(x, str):
            parts.append(x)
        elif isinstance(x, (list, tuple)):
            for y in x:
                walk(y)
    walk(g.get("sections", []))
    walk(g.get("sources", []))
    return re.sub(r"\s+", " ", _strip_tags(" ".join(parts))).lower()


def _upsert(cur, table, where, values):
    """Оновлює рядок за where (dict) або вставляє новий. Повертає id."""
    p = _ph()
    wsql = " AND ".join(f"{k} = {p}" for k in where)
    cur.execute(f"SELECT id FROM {table} WHERE {wsql}", tuple(where.values()))
    row = cur.fetchone()
    if row:
        rid = dict(row)["id"]
        if values:
            sset = ", ".join(f"{k} = {p}" for k in values)
            cur.execute(f"UPDATE {table} SET {sset} WHERE id = {p}", tuple(values.values()) + (rid,))
        return rid
    allv = {**where, **values}
    cols = ", ".join(allv)
    qs = ", ".join([p] * len(allv))
    if USE_POSTGRES:
        cur.execute(f"INSERT INTO {table} ({cols}) VALUES ({qs}) RETURNING id", tuple(allv.values()))
        return dict(cur.fetchone())["id"]
    cur.execute(f"INSERT INTO {table} ({cols}) VALUES ({qs})", tuple(allv.values()))
    return cur.lastrowid


def sync_catalog(force=False):
    """Імпортує теми/підтеми/питання з data/catalog.json і data/gp_content.json,
    а пости — з data/posts.json. Виконується лише коли файли змінились
    (порівнюємо хеш). Старі питання без коду (імпорт до v2) деактивуються,
    але НЕ видаляються: покупки й платежі лишаються недоторканими."""
    files = ["catalog.json", "gp_content.json", "posts.json"]
    paths = [os.path.join(DATA_DIR, f) for f in files]
    if not all(os.path.exists(x) for x in paths[:2]):
        return False
    h = hashlib.sha1()
    for x in paths:
        if os.path.exists(x):
            h.update(open(x, "rb").read())
    digest = h.hexdigest()
    if not force and meta_get("catalog_hash") == digest:
        return False

    catalog = json.load(open(paths[0], encoding="utf-8"))
    items = json.load(open(paths[1], encoding="utf-8"))
    conn = get_connection()
    cur = conn.cursor()
    p = _ph()

    # 1) старе дерево — вимикаємо (питання без коду, теми/підтеми не з каталогу)
    cur.execute("UPDATE questions SET is_active = 0 WHERE code IS NULL")
    new_topic_slugs = [t["slug"] for t in catalog["topics"]]
    cur.execute(f"UPDATE topics SET is_active = 0 WHERE slug NOT IN ({', '.join([p]*len(new_topic_slugs))})",
                tuple(new_topic_slugs))

    # 2) теми і підтеми
    sub_ids, topic_ids = {}, {}
    for t in catalog["topics"]:
        tid = _upsert(cur, "topics", {"slug": t["slug"]},
                      {"name": t["name"], "emoji": t.get("emoji", "📁"),
                       "sort_order": t.get("sort", 100), "is_active": 1})
        topic_ids[t["slug"]] = tid
        cur.execute(f"UPDATE subtopics SET is_active = 0 WHERE topic_id = {p}", (tid,))
        for s in t["subtopics"]:
            sid = _upsert(cur, "subtopics", {"topic_id": tid, "slug": s["slug"]},
                          {"name": s["name"], "sort_order": s.get("sort", 100), "is_active": 1})
            sub_ids[(t["slug"], s["slug"])] = sid

    # 3) питання
    for g in items:
        tid = topic_ids[g["topic"]]
        sid = sub_ids[(g["topic"], g["subtopic"])]
        num = int(re.sub(r"\D", "", g["code"]) or 0)
        _upsert(cur, "questions", {"code": g["code"]}, {
            "topic_id": tid, "subtopic_id": sid,
            "slug": "gp-%02d" % num,
            "title": g["title"],
            "block_audience": g.get("who", ""),
            "block_problem": _strip_tags(g.get("situation", "")),
            "block_solution": _strip_tags(g.get("answer", "")),
            "block_example": "", "block_mistakes": "", "block_checklist": "",
            "block_sources": "; ".join(g.get("sources", [])),
            "price": int(g["price"]),
            "is_active": 1 if g.get("active", True) else 0,
            "content_json": json.dumps(g, ensure_ascii=False),
            "search_text": _search_blob(g),
            "sort_order": num,
        })

    # 4) пости: додаємо нові, текст оновлюємо лише в чернетках (статус не чіпаємо)
    if os.path.exists(paths[2]):
        posts = json.load(open(paths[2], encoding="utf-8"))["posts"]
        now = datetime.utcnow().strftime("%Y-%m-%d %H:%M")
        for x in posts:
            cur.execute(f"SELECT id, status FROM posts WHERE post_key = {p}", (x["key"],))
            row = cur.fetchone()
            media = json.dumps(x.get("media", []))
            if row is None:
                cur.execute(
                    f"INSERT INTO posts (post_key, gp_code, text, media, status, created_at) "
                    f"VALUES ({p}, {p}, {p}, {p}, {p}, {p})",
                    (x["key"], x["gp_code"], x["text"], media,
                     "hold" if x.get("hold") else "draft", now))
            elif dict(row)["status"] in ("draft", "hold"):
                cur.execute(f"UPDATE posts SET text = {p}, media = {p} WHERE id = {p}",
                            (x["text"], media, dict(row)["id"]))

    conn.commit()
    conn.close()
    meta_set("catalog_hash", digest)
    return True


# ---- читання каталогу -------------------------------------------------------

def get_catalog_topics():
    """Активні теми, у яких є хоча б одне активне питання (з лічильником)."""
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        "SELECT t.id, t.name AS title, t.emoji, t.slug, COUNT(q.id) AS cnt "
        "FROM topics t JOIN questions q ON q.topic_id = t.id AND q.is_active = 1 "
        "WHERE t.is_active = 1 GROUP BY t.id, t.name, t.emoji, t.slug, t.sort_order "
        "ORDER BY t.sort_order, t.name")
    rows = _rows_to_dicts(cur.fetchall())
    conn.close()
    return rows


def get_catalog_subtopics(topic_id):
    conn = get_connection()
    cur = conn.cursor()
    p = _ph()
    cur.execute(
        f"SELECT s.id, s.name AS title, s.slug, COUNT(q.id) AS cnt "
        f"FROM subtopics s JOIN questions q ON q.subtopic_id = s.id AND q.is_active = 1 "
        f"WHERE s.topic_id = {p} AND s.is_active = 1 "
        f"GROUP BY s.id, s.name, s.slug, s.sort_order ORDER BY s.sort_order, s.name",
        (topic_id,))
    rows = _rows_to_dicts(cur.fetchall())
    conn.close()
    return rows


def get_questions_by_subtopic(subtopic_id):
    conn = get_connection()
    cur = conn.cursor()
    p = _ph()
    cur.execute(
        f"SELECT id, code, title, price, topic_id, subtopic_id FROM questions "
        f"WHERE subtopic_id = {p} AND is_active = 1 ORDER BY sort_order, code",
        (subtopic_id,))
    rows = _rows_to_dicts(cur.fetchall())
    conn.close()
    return rows


def get_subtopic(subtopic_id):
    conn = get_connection()
    cur = conn.cursor()
    p = _ph()
    cur.execute(f"SELECT s.id, s.name AS title, s.topic_id, t.name AS topic_title, t.emoji "
                f"FROM subtopics s JOIN topics t ON t.id = s.topic_id WHERE s.id = {p}", (subtopic_id,))
    row = cur.fetchone()
    conn.close()
    return _row_to_dict(row)


def get_question_by_code(code):
    """code: 'ГП-01', 'gp01', 'gp-1', '1' — усе розпізнається."""
    m = re.search(r"(\d+)", str(code or ""))
    if not m:
        return None
    norm = "ГП-%02d" % int(m.group(1))
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(f"SELECT id FROM questions WHERE code = {_ph()} AND is_active = 1", (norm,))
    row = cur.fetchone()
    conn.close()
    return get_question_by_id(dict(row)["id"]) if row else None


def question_content(q):
    """Повний контент ГП (dict) або None для старих питань."""
    raw = q.get("content_json") if q else None
    if not raw:
        return None
    try:
        return json.loads(raw)
    except Exception:
        return None


def _stem(w):
    # грубий «стемінг» для української: штрафи/штрафу/штраф -> штраф
    return w[:max(4, len(w) - 2)] if len(w) > 5 else w


def search_questions(query, limit=10):
    words = [w for w in re.findall(r"[\w'’-]+", (query or "").lower()) if len(w) >= 3]
    if not words:
        return []
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("SELECT id, code, title, price, search_text FROM questions "
                "WHERE is_active = 1 AND code IS NOT NULL ORDER BY sort_order")
    rows = _rows_to_dicts(cur.fetchall())
    conn.close()
    stems = [_stem(w) for w in words]
    scored = []
    for r in rows:
        blob = r.get("search_text") or ""
        title = (r.get("title") or "").lower()
        hits = sum(1 for s in stems if s in blob)
        if hits == 0:
            continue
        score = hits * 10 + sum(3 for s in stems if s in title)
        if hits == len(stems):
            score += 50
        scored.append((score, r))
    scored.sort(key=lambda x: (-x[0], x[1]["code"]))
    if scored:  # відсікаємо «випадкові» збіги, якщо є значно кращі
        best = scored[0][0]
        scored = [x for x in scored if x[0] * 3 >= best]
    return [r for _s, r in scored[:limit]]


# ---- черга публікацій ------------------------------------------------------

def list_posts(statuses=None):
    conn = get_connection()
    cur = conn.cursor()
    if statuses:
        p = _ph()
        cur.execute(f"SELECT * FROM posts WHERE status IN ({', '.join([p]*len(statuses))}) "
                    f"ORDER BY COALESCE(scheduled_at, '9999'), gp_code", tuple(statuses))
    else:
        cur.execute("SELECT * FROM posts ORDER BY COALESCE(scheduled_at, '9999'), gp_code")
    rows = _rows_to_dicts(cur.fetchall())
    conn.close()
    return rows


def get_post(post_id):
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(f"SELECT * FROM posts WHERE id = {_ph()}", (post_id,))
    row = cur.fetchone()
    conn.close()
    return _row_to_dict(row)


def update_post(post_id, **fields):
    if not fields:
        return
    conn = get_connection()
    cur = conn.cursor()
    p = _ph()
    sset = ", ".join(f"{k} = {p}" for k in fields)
    cur.execute(f"UPDATE posts SET {sset} WHERE id = {p}", tuple(fields.values()) + (post_id,))
    conn.commit()
    conn.close()


def due_posts(now_utc_str):
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(f"SELECT * FROM posts WHERE status = 'scheduled' AND scheduled_at <= {_ph()} "
                f"ORDER BY scheduled_at", (now_utc_str,))
    rows = _rows_to_dicts(cur.fetchall())
    conn.close()
    return rows


def busy_slots():
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("SELECT scheduled_at FROM posts WHERE status = 'scheduled' AND scheduled_at IS NOT NULL")
    rows = {dict(r)["scheduled_at"] for r in cur.fetchall()}
    conn.close()
    return rows


if __name__ == "__main__":
    init_db()
    print("База даних ініціалізована й домигрована.")
    print("Додано: emoji для тем, підтема 'Усі питання' для кожної теми,")
    print("прив'язка існуючих питань, таблиця rate_limits.")
    print("Тепер bot.py повинен коректно працювати з наявними 27 питаннями.")
