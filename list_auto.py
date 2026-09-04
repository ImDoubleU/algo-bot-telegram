import argparse
import sqlite3
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Show auto-feedback groups grouped by teacher_id."
    )
    parser.add_argument(
        "--db",
        default="logs/bot_analytics.db",
        help="Path to SQLite database (default: logs/bot_analytics.db)",
    )
    args = parser.parse_args()

    db_path = Path(args.db)
    if not db_path.exists():
        raise SystemExit(f"DB not found: {db_path}")

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        cur = conn.cursor()
        cur.execute(
            """
            SELECT teacher_id, group_name, course_name, current_lesson_number, weekday, lesson_time
            FROM teacher_groups
            ORDER BY teacher_id ASC, weekday ASC, lesson_time ASC, group_name COLLATE NOCASE ASC
            """
        )
        rows = cur.fetchall()
    finally:
        conn.close()

    if not rows:
        print("No auto-feedback groups found.")
        return

    current_teacher_id = None
    index = 0
    for row in rows:
        teacher_id = row["teacher_id"]
        group_name = row["group_name"]
        group_course = row["course_name"]
        current_lesson_number = row["current_lesson_number"]
        if teacher_id != current_teacher_id:
            if current_teacher_id is not None:
                print()
            current_teacher_id = teacher_id
            index = 1
            print(f"teacher_id: {teacher_id}")
        else:
            index += 1
        print(f"  {index}. {group_name}, курс: {group_course}, текущий урок: {current_lesson_number}")


if __name__ == "__main__":
    main()
