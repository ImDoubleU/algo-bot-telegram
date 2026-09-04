import json
import shutil
import sqlite3
from datetime import datetime
from pathlib import Path


# === Configure here ===
USER_ID = 1844642345
GROUPS_JSON_PATH = "groups_for_user.json"
DB_PATH = "logs/bot_analytics.db"
CREATE_BACKUP = True
# ======================


REQUIRED_FIELDS = [
    "group_name",
    "course_name",
    "lesson_offset",
    "current_lesson_number",
    "first_lesson_date",
    "weekday",
    "lesson_time",
    "lesson_mode",
    "lesson_place",
]


def _validate_date(value: str) -> None:
    datetime.strptime(value, "%Y-%m-%d")


def _validate_time(value: str) -> None:
    datetime.strptime(value, "%H:%M")


def _validate_group(group: dict) -> None:
    missing = [key for key in REQUIRED_FIELDS if key not in group]
    if missing:
        raise ValueError(f"Missing fields: {', '.join(missing)}")

    _validate_date(str(group["first_lesson_date"]))
    _validate_time(str(group["lesson_time"]))

    weekday = int(group["weekday"])
    if weekday < 0 or weekday > 6:
        raise ValueError("weekday must be in range 0..6")

    if int(group["current_lesson_number"]) < 1:
        raise ValueError("current_lesson_number must be >= 1")

    lesson_mode = str(group["lesson_mode"])
    if lesson_mode not in {"group", "individual"}:
        raise ValueError("lesson_mode must be 'group' or 'individual'")

    lesson_place = str(group["lesson_place"])
    if lesson_place not in {"online", "offline"}:
        raise ValueError("lesson_place must be 'online' or 'offline'")


def _backup_db(db_path: Path) -> Path:
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_path = db_path.with_name(f"{db_path.stem}_backup_{timestamp}{db_path.suffix}")
    shutil.copy2(db_path, backup_path)
    return backup_path


def _load_groups(json_path: Path) -> list[dict]:
    with json_path.open("r", encoding="utf-8") as f:
        payload = json.load(f)

    if isinstance(payload, list):
        groups = payload
    elif isinstance(payload, dict) and "groups" in payload and isinstance(payload["groups"], list):
        groups = payload["groups"]
    else:
        raise ValueError("JSON must be either a list of groups or object with key 'groups'.")

    if not groups:
        raise ValueError("No groups found in JSON.")

    return groups


def main() -> None:
    db_path = Path(DB_PATH)
    json_path = Path(GROUPS_JSON_PATH)

    if not db_path.exists():
        raise SystemExit(f"DB not found: {db_path}")
    if not json_path.exists():
        raise SystemExit(f"JSON not found: {json_path}")

    groups = _load_groups(json_path)

    prepared_rows = []
    for idx, group in enumerate(groups, start=1):
        try:
            _validate_group(group)
        except Exception as e:
            raise SystemExit(f"Group #{idx} invalid: {e}") from e

        prepared_rows.append(
            (
                USER_ID,  # teacher_id
                int(group.get("teacher_chat_id", USER_ID)),  # teacher_chat_id
                str(group["group_name"]),
                str(group["course_name"]),
                int(group["lesson_offset"]),
                int(group["current_lesson_number"]),
                str(group["first_lesson_date"]),
                int(group["weekday"]),
                str(group["lesson_time"]),
                str(group["lesson_mode"]),
                str(group["lesson_place"]),
                int(group.get("is_active", 1)),
                group.get("last_sent_for_date"),
            )
        )

    backup_path = None
    if CREATE_BACKUP:
        backup_path = _backup_db(db_path)

    conn = sqlite3.connect(db_path)
    try:
        cur = conn.cursor()
        inserted_ids = []
        for row in prepared_rows:
            cur.execute(
                """
                INSERT INTO teacher_groups (
                    teacher_id, teacher_chat_id, group_name, course_name, lesson_offset,
                    current_lesson_number, first_lesson_date, weekday, lesson_time,
                    lesson_mode, lesson_place, is_active, last_sent_for_date
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                row,
            )
            inserted_ids.append(cur.lastrowid)
        conn.commit()
    finally:
        conn.close()

    if backup_path:
        print(f"Backup: {backup_path}")
    print(f"Inserted groups: {len(inserted_ids)}")
    print(f"Group IDs: {inserted_ids}")


if __name__ == "__main__":
    main()
