from __future__ import annotations

import argparse
import csv
import re
import secrets
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from app.settings import settings
from webapp.deps import stats_service, web_auth_service


OUTPUT_DIR = settings.base_dir / "private"


def _slugify_username(raw_value: str, fallback: str) -> str:
    value = (raw_value or "").strip().lower()
    value = re.sub(r"[^a-z0-9_]+", "_", value)
    value = re.sub(r"_+", "_", value).strip("_")
    return value or fallback


def _generate_password(length: int = 14) -> str:
    alphabet = "ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz23456789"
    return "".join(secrets.choice(alphabet) for _ in range(length))


def _unique_username(base_username: str, used_usernames: set[str]) -> str:
    candidate = base_username
    suffix = 1
    while candidate in used_usernames:
        suffix += 1
        candidate = f"{base_username}_{suffix}"
    used_usernames.add(candidate)
    return candidate


def _teacher_ids_from_groups() -> list[int]:
    conn = sqlite3.connect(settings.db_path)
    cur = conn.cursor()
    cur.execute(
        """
        SELECT DISTINCT teacher_id
        FROM teacher_groups
        ORDER BY teacher_id ASC
        """
    )
    rows = [int(row[0]) for row in cur.fetchall()]
    conn.close()
    return rows


def _existing_teacher_users() -> dict[int, tuple]:
    teacher_users = {}
    for row in web_auth_service.list_users():
        linked_teacher_id = row[4]
        role = row[3]
        username = str(row[1])
        is_active = int(row[5])
        if linked_teacher_id and role == "teacher" and is_active == 1 and username != "teacher_demo":
            teacher_users[int(linked_teacher_id)] = row
    return teacher_users


def _admin_linked_teacher_ids() -> set[int]:
    admin_teacher_ids: set[int] = set()
    for row in web_auth_service.list_users():
        linked_teacher_id = row[4]
        role = row[3]
        is_active = int(row[5])
        if linked_teacher_id and role == "admin" and is_active == 1:
            admin_teacher_ids.add(int(linked_teacher_id))
    return admin_teacher_ids


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--reset-passwords",
        action="store_true",
        help="Rotate passwords for existing teacher accounts and include them in the export.",
    )
    args = parser.parse_args()

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    teacher_ids = _teacher_ids_from_groups()
    existing_teacher_users = _existing_teacher_users()
    admin_linked_teacher_ids = _admin_linked_teacher_ids()
    used_usernames = {str(row[1]).lower() for row in web_auth_service.list_users()}
    provisioned_rows: list[dict[str, str]] = []

    for teacher_id in teacher_ids:
        if teacher_id in admin_linked_teacher_ids:
            continue

        telegram_username, full_name = stats_service.tracker.get_latest_user_identity(teacher_id)
        display_name = full_name or telegram_username or f"Teacher {teacher_id}"

        existing_teacher = existing_teacher_users.get(teacher_id)
        if existing_teacher:
            status = "existing"
            password = ""
            web_auth_service.update_user_profile(existing_teacher[1], display_name=display_name, is_active=1)
            if args.reset_passwords:
                password = _generate_password()
                web_auth_service.update_user_password(existing_teacher[1], password)
                status = "password_reset"
            provisioned_rows.append(
                {
                    "teacher_id": str(teacher_id),
                    "display_name": display_name,
                    "telegram_username": telegram_username,
                    "web_username": existing_teacher[1],
                    "password": password,
                    "status": status,
                }
            )
            continue

        preferred_username = _slugify_username(telegram_username, f"teacher_{teacher_id}")
        final_username = _unique_username(preferred_username, used_usernames)
        password = _generate_password()
        web_auth_service.create_user(
            final_username,
            password,
            display_name,
            role="teacher",
            linked_teacher_id=teacher_id,
        )
        provisioned_rows.append(
            {
                "teacher_id": str(teacher_id),
                "display_name": display_name,
                "telegram_username": telegram_username,
                "web_username": final_username,
                "password": password,
                "status": "created",
            }
        )

    demo_user = web_auth_service.get_user_by_username("teacher_demo")
    if demo_user:
        web_auth_service.update_user_profile("teacher_demo", display_name="Demo Teacher (Disabled)", is_active=0)

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    csv_path = OUTPUT_DIR / f"teacher_accounts_{timestamp}.csv"
    md_path = OUTPUT_DIR / f"teacher_accounts_{timestamp}.md"

    with csv_path.open("w", encoding="utf-8", newline="") as csv_file:
        writer = csv.DictWriter(
            csv_file,
            fieldnames=["teacher_id", "display_name", "telegram_username", "web_username", "password", "status"],
        )
        writer.writeheader()
        writer.writerows(provisioned_rows)

    with md_path.open("w", encoding="utf-8") as md_file:
        md_file.write("# Teacher Web Accounts\n\n")
        md_file.write("Ниже перечислены teacher-аккаунты, созданные или подтвержденные по данным старого бота.\n\n")
        md_file.write("| Teacher ID | Имя | Telegram username | Web username | Пароль | Статус |\n")
        md_file.write("| --- | --- | --- | --- | --- | --- |\n")
        for row in provisioned_rows:
            md_file.write(
                f"| {row['teacher_id']} | {row['display_name']} | {row['telegram_username'] or '-'} | "
                f"{row['web_username']} | {row['password'] or 'не менялся'} | {row['status']} |\n"
            )

    print(f"teacher_ids={len(teacher_ids)}")
    print(f"csv={csv_path}")
    print(f"md={md_path}")
    print("demo_user_disabled=yes" if demo_user else "demo_user_disabled=no")
    for row in provisioned_rows:
        print(
            f"{row['status']}: teacher_id={row['teacher_id']} "
            f"web_username={row['web_username']} display_name={row['display_name']}"
        )


if __name__ == "__main__":
    main()
