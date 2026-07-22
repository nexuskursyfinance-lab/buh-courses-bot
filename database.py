"""
Модуль для роботи з базою даних
Підтримує SQLite (локально) та PostgreSQL (Heroku)
Автоматично визначає тип БД за змінною DATABASE_URL
"""
import os
import logging

log = logging.getLogger(__name__)

DATABASE_URL = os.getenv("DATABASE_URL", "")
DB_PATH      = os.getenv("DB_PATH", "buh_courses.db")
USE_POSTGRES = DATABASE_URL.startswith(("postgres://", "postgresql://"))

if USE_POSTGRES:
    import psycopg2
    import psycopg2.extras
    _PG_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)
    log.info("🐘 БД: PostgreSQL (Heroku)")
else:
    import sqlite3
    log.info(f"🗄  БД: SQLite ({DB_PATH})")


def get_connection():
    if USE_POSTGRES:
        return psycopg2.connect(_PG_URL, cursor_factory=psycopg2.extras.RealDictCursor)
    else:
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        return conn


def ph():
    return "%s" if USE_POSTGRES else "?"


def placeholder(n=1):
    s = "%s" if USE_POSTGRES else "?"
    return ",".join([s] * n)


def _exec(conn, sql, params=()):
    """Виконує SQL для обох типів БД"""
    if USE_POSTGRES:
        cur = conn.cursor()
        cur.execute(sql, params)
        conn.commit()
        cur.close()
    else:
        conn.execute(sql, params)
        conn.commit()


def _fetch(conn, sql, params=()):
    if USE_POSTGRES:
        cur = conn.cursor()
        cur.execute(sql, params)
        rows = cur.fetchall()
        cur.close()
        return rows
    return conn.execute(sql, params).fetchall()


def _fetchone(conn, sql, params=()):
    if USE_POSTGRES:
        cur = conn.cursor()
        cur.execute(sql, params)
        row = cur.fetchone()
        cur.close()
        return row
    return conn.execute(sql, params).fetchone()


def init_db():
    conn = get_connection()
    if USE_POSTGRES:
        cur = conn.cursor()
        cur.execute("""
            CREATE TABLE IF NOT EXISTS users (
                id SERIAL PRIMARY KEY, telegram_id BIGINT UNIQUE NOT NULL,
                username TEXT, full_name TEXT, phone TEXT,
                has_access INTEGER DEFAULT 0, created_at TIMESTAMPTZ DEFAULT NOW())""")
        cur.execute("""
            CREATE TABLE IF NOT EXISTS topics (
                id SERIAL PRIMARY KEY, title TEXT NOT NULL,
                emoji TEXT DEFAULT '📚', sort_order INTEGER DEFAULT 0)""")
        cur.execute("""
            CREATE TABLE IF NOT EXISTS subtopics (
                id SERIAL PRIMARY KEY, topic_id INTEGER NOT NULL REFERENCES topics(id), title TEXT NOT NULL)""")
        cur.execute("""
            CREATE TABLE IF NOT EXISTS questions (
                id SERIAL PRIMARY KEY, topic_id INTEGER NOT NULL REFERENCES topics(id),
                subtopic_id INTEGER REFERENCES subtopics(id), question TEXT NOT NULL, answer TEXT NOT NULL)""")
        cur.execute("""
            CREATE TABLE IF NOT EXISTS payments (
                id SERIAL PRIMARY KEY, telegram_id BIGINT NOT NULL,
                order_id TEXT UNIQUE NOT NULL, amount NUMERIC(10,2) NOT NULL,
                status TEXT DEFAULT 'pending', liqpay_data TEXT,
                created_at TIMESTAMPTZ DEFAULT NOW(), paid_at TIMESTAMPTZ)""")
        cur.execute("""
            CREATE TABLE IF NOT EXISTS rate_limits (
                telegram_id BIGINT NOT NULL, action TEXT NOT NULL,
                count INTEGER DEFAULT 1, window_start TIMESTAMPTZ DEFAULT NOW(),
                PRIMARY KEY (telegram_id, action))""")
        conn.commit()
        cur.close()
    else:
        cur = conn.cursor()
        cur.execute("""CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT, telegram_id INTEGER UNIQUE NOT NULL,
            username TEXT, full_name TEXT, phone TEXT,
            has_access INTEGER DEFAULT 0, created_at TEXT DEFAULT (datetime('now')))""")
        cur.execute("""CREATE TABLE IF NOT EXISTS topics (
            id INTEGER PRIMARY KEY AUTOINCREMENT, title TEXT NOT NULL,
            emoji TEXT DEFAULT '📚', sort_order INTEGER DEFAULT 0)""")
        cur.execute("""CREATE TABLE IF NOT EXISTS subtopics (
            id INTEGER PRIMARY KEY AUTOINCREMENT, topic_id INTEGER NOT NULL,
            title TEXT NOT NULL, FOREIGN KEY (topic_id) REFERENCES topics(id))""")
        cur.execute("""CREATE TABLE IF NOT EXISTS questions (
            id INTEGER PRIMARY KEY AUTOINCREMENT, topic_id INTEGER NOT NULL,
            subtopic_id INTEGER, question TEXT NOT NULL, answer TEXT NOT NULL,
            FOREIGN KEY (topic_id) REFERENCES topics(id),
            FOREIGN KEY (subtopic_id) REFERENCES subtopics(id))""")
        cur.execute("""CREATE TABLE IF NOT EXISTS payments (
            id INTEGER PRIMARY KEY AUTOINCREMENT, telegram_id INTEGER NOT NULL,
            order_id TEXT UNIQUE NOT NULL, amount REAL NOT NULL,
            status TEXT DEFAULT 'pending', liqpay_data TEXT,
            created_at TEXT DEFAULT (datetime('now')), paid_at TEXT,
            FOREIGN KEY (telegram_id) REFERENCES users(telegram_id))""")
        cur.execute("""CREATE TABLE IF NOT EXISTS rate_limits (
            telegram_id INTEGER NOT NULL, action TEXT NOT NULL,
            count INTEGER DEFAULT 1, window_start TEXT DEFAULT (datetime('now')),
            PRIMARY KEY (telegram_id, action))""")
        conn.commit()
    conn.close()
    log.info("✅ БД ініціалізована")


def check_rate_limit(telegram_id: int, action: str, max_per_hour: int = 20) -> bool:
    conn = get_connection()
    p = ph()
    try:
        if USE_POSTGRES:
            cur = conn.cursor()
            cur.execute(f"DELETE FROM rate_limits WHERE telegram_id={p} AND action={p} AND window_start < NOW() - INTERVAL '1 hour'", (telegram_id, action))
            cur.execute(f"INSERT INTO rate_limits (telegram_id, action, count) VALUES ({p},{p},1) ON CONFLICT (telegram_id, action) DO UPDATE SET count = rate_limits.count + 1 RETURNING count", (telegram_id, action))
            count = cur.fetchone()["count"]
            conn.commit()
            cur.close()
        else:
            cur = conn.cursor()
            cur.execute(f"DELETE FROM rate_limits WHERE telegram_id={p} AND action={p} AND datetime(window_start, '+1 hour') < datetime('now')", (telegram_id, action))
            cur.execute(f"INSERT INTO rate_limits (telegram_id, action, count) VALUES ({p},{p},1) ON CONFLICT (telegram_id, action) DO UPDATE SET count = count + 1", (telegram_id, action))
            conn.commit()
            row = conn.execute(f"SELECT count FROM rate_limits WHERE telegram_id={p} AND action={p}", (telegram_id, action)).fetchone()
            count = row["count"] if row else 1
        return count <= max_per_hour
    except Exception as e:
        log.error(f"Rate limit error: {e}")
        return True
    finally:
        conn.close()


def upsert_user(telegram_id: int, username: str = None, full_name: str = None):
    conn = get_connection()
    p = placeholder(3)
    if USE_POSTGRES:
        sql = f"INSERT INTO users (telegram_id, username, full_name) VALUES ({p}) ON CONFLICT (telegram_id) DO UPDATE SET username=EXCLUDED.username, full_name=EXCLUDED.full_name"
    else:
        sql = f"INSERT INTO users (telegram_id, username, full_name) VALUES ({p}) ON CONFLICT(telegram_id) DO UPDATE SET username=excluded.username, full_name=excluded.full_name"
    _exec(conn, sql, (telegram_id, username, full_name))
    conn.close()


def get_user(telegram_id: int):
    conn = get_connection()
    row = _fetchone(conn, f"SELECT * FROM users WHERE telegram_id={ph()}", (telegram_id,))
    conn.close()
    return row


def set_user_access(telegram_id: int, has_access: int = 1):
    conn = get_connection()
    _exec(conn, f"UPDATE users SET has_access={ph()} WHERE telegram_id={ph()}", (has_access, telegram_id))
    conn.close()


def get_stats():
    conn = get_connection()
    users   = _fetchone(conn, "SELECT COUNT(*) AS cnt FROM users")
    sales   = _fetchone(conn, "SELECT COUNT(*) AS cnt FROM payments WHERE status='success'")
    revenue = _fetchone(conn, "SELECT COALESCE(SUM(amount),0) AS total FROM payments WHERE status='success'")
    pending = _fetchone(conn, "SELECT COUNT(*) AS cnt FROM payments WHERE status='pending'")
    conn.close()
    def v(r, k="cnt"):
        if r is None: return 0
        try: return r[k]
        except: return r[0]
    return {"users": v(users), "sales": v(sales), "revenue": float(v(revenue, "total") or 0), "pending": v(pending)}


def get_all_topics():
    conn = get_connection()
    rows = _fetch(conn, "SELECT * FROM topics ORDER BY sort_order")
    conn.close()
    return rows


def get_subtopics(topic_id: int):
    conn = get_connection()
    rows = _fetch(conn, f"SELECT * FROM subtopics WHERE topic_id={ph()}", (topic_id,))
    conn.close()
    return rows


def get_questions_by_topic(topic_id: int):
    conn = get_connection()
    sql = f"""SELECT q.*, s.title AS subtopic_title FROM questions q
        LEFT JOIN subtopics s ON q.subtopic_id = s.id
        WHERE q.topic_id={ph()} ORDER BY q.subtopic_id, q.id"""
    rows = _fetch(conn, sql, (topic_id,))
    conn.close()
    return rows


def create_payment(telegram_id: int, order_id: str, amount: float):
    conn = get_connection()
    _exec(conn, f"INSERT INTO payments (telegram_id, order_id, amount) VALUES ({placeholder(3)})", (telegram_id, order_id, amount))
    conn.close()


def confirm_payment(order_id: str, liqpay_data: str):
    conn = get_connection()
    p = ph()
    if USE_POSTGRES:
        _exec(conn, f"UPDATE payments SET status='success', liqpay_data={p}, paid_at=NOW() WHERE order_id={p}", (liqpay_data, order_id))
    else:
        _exec(conn, f"UPDATE payments SET status='success', liqpay_data={p}, paid_at=datetime('now') WHERE order_id={p}", (liqpay_data, order_id))
    row = _fetchone(conn, f"SELECT telegram_id FROM payments WHERE order_id={p}", (order_id,))
    conn.close()
    if row:
        tid = row["telegram_id"]
        set_user_access(tid, 1)
        return tid
    return None


def get_payment_by_order(order_id: str):
    conn = get_connection()
    row = _fetchone(conn, f"SELECT * FROM payments WHERE order_id={ph()}", (order_id,))
    conn.close()
    return row
