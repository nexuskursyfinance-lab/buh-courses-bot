"""
review_and_fix_topics.py

Проходить по всіх УЖЕ ІМПОРТОВАНИХ питаннях у базі і дозволяє:
  1. Перевірити якість розпізнавання (чи не порожній якийсь із 7 блоків).
  2. Виправити/підтвердити тему для кожного питання.

Використання:
    python review_and_fix_topics.py

Нічого не видаляє і не дублює — тільки оновлює поле topic_id
для існуючих записів у таблиці questions.
"""

import database

BLOCK_LABELS = [
    ("block_audience", "1. Кому це актуально"),
    ("block_problem", "2. Суть проблеми"),
    ("block_solution", "3. Як діяти правильно"),
    ("block_example", "4. Приклад з практики"),
    ("block_mistakes", "5. Типові помилки"),
    ("block_checklist", "6. Короткий чеклист"),
    ("block_sources", "7. Джерело / нормативна база"),
]


def load_topics():
    conn = database.get_connection()
    cur = conn.cursor()
    cur.execute("SELECT id, name FROM topics WHERE is_active = 1 ORDER BY sort_order, name")
    rows = cur.fetchall()
    conn.close()
    return [(row["id"], row["name"]) for row in rows]


def load_questions():
    conn = database.get_connection()
    cur = conn.cursor()
    cur.execute("""
        SELECT id, slug, title, topic_id,
               block_audience, block_problem, block_solution,
               block_example, block_mistakes, block_checklist, block_sources
        FROM questions
        ORDER BY id
    """)
    rows = cur.fetchall()
    conn.close()
    return rows


def update_topic(question_id, new_topic_id):
    conn = database.get_connection()
    cur = conn.cursor()
    placeholder = "%s" if database.USE_POSTGRES else "?"
    cur.execute(
        f"UPDATE questions SET topic_id = {placeholder} WHERE id = {placeholder}",
        (new_topic_id, question_id),
    )
    conn.commit()
    conn.close()


def preview(text, length=90):
    text = (text or "").strip().replace("\n", " ")
    if not text:
        return "⚠️  ПОРОЖНЬО — перевірте файл вручну!"
    return text[:length] + ("…" if len(text) > length else "")


def main():
    topics = load_topics()
    topics_by_id = {tid: name for tid, name in topics}
    questions = load_questions()

    if not questions:
        print("У базі немає жодного питання. Спершу запустіть import_hot_questions.py.")
        return

    print(f"\nЗнайдено {len(questions)} питань у базі.\n")
    print("Доступні теми:")
    for idx, (tid, name) in enumerate(topics, start=1):
        print(f"  {idx}. {name}")

    empty_block_files = []
    changed = 0

    for q in questions:
        current_topic = topics_by_id.get(q["topic_id"], "?")
        print(f"\n{'='*70}")
        print(f"[{q['id']}] {q['title']}")
        print(f"Slug: {q['slug']}")
        print(f"Поточна тема: {current_topic}")
        print("-" * 70)

        has_empty = False
        for field, label in BLOCK_LABELS:
            content = q[field]
            p = preview(content)
            if "ПОРОЖНЬО" in p:
                has_empty = True
            print(f"  {label}: {p}")

        if has_empty:
            empty_block_files.append(q["title"])

        while True:
            choice = input(
                f"\nЗалишити тему «{current_topic}»? Натисніть Enter, "
                f"або введіть номер нової теми (1-{len(topics)}): "
            ).strip()
            if choice == "":
                break
            if choice.isdigit() and 1 <= int(choice) <= len(topics):
                new_topic_id, new_topic_name = topics[int(choice) - 1]
                if new_topic_id != q["topic_id"]:
                    update_topic(q["id"], new_topic_id)
                    print(f"  ✅ Тему змінено на «{new_topic_name}»")
                    changed += 1
                else:
                    print("  Тема залишилась без змін.")
                break
            print("  Некоректний вибір, спробуйте ще раз.")

    print(f"\n{'='*70}")
    print(f"Перевірку завершено. Тем змінено: {changed} з {len(questions)}.")

    if empty_block_files:
        print(f"\n⚠️  УВАГА: у {len(empty_block_files)} питань(-і) є порожні блоки — перевірте вручну:")
        for title in empty_block_files:
            print(f"   - {title}")
    else:
        print("\n✅ Порожніх блоків не знайдено — контент розпізнано повністю у всіх питаннях.")


if __name__ == "__main__":
    main()
