from __future__ import annotations

import json
import os
import re
import sqlite3
from pathlib import Path

import openpyxl  # noqa: F401
import pandas  # noqa: F401
import telegram  # noqa: F401
from telegram.ext import JobQueue  # noqa: F401


ROOT = Path(__file__).resolve().parents[1]
TOKEN_PATTERN = re.compile(r"^\d{6,12}:[A-Za-z0-9_-]{30,}$")


def fail(message: str) -> None:
    raise SystemExit(f"PRECHECK_FAILED: {message}")


def main() -> None:
    token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    if not TOKEN_PATTERN.fullmatch(token):
        fail("TELEGRAM_BOT_TOKEN is missing or malformed")

    raw_admin_ids = os.getenv("TELEGRAM_ADMIN_IDS", "").strip()
    try:
        admin_ids = [int(value.strip()) for value in raw_admin_ids.split(",") if value.strip()]
    except ValueError:
        fail("TELEGRAM_ADMIN_IDS must contain comma-separated integers")
    if not admin_ids:
        fail("TELEGRAM_ADMIN_IDS must contain at least one ID")

    courses_path = ROOT / "data" / "courses.json"
    if not courses_path.is_file():
        fail(f"missing {courses_path.relative_to(ROOT)}")
    with courses_path.open(encoding="utf-8") as courses_file:
        courses = json.load(courses_file)
    if not isinstance(courses, dict) or not courses:
        fail("data/courses.json must contain a non-empty JSON object")

    db_path = ROOT / "logs" / "bot_analytics.db"
    if not db_path.is_file():
        fail(f"missing {db_path.relative_to(ROOT)}")
    connection = sqlite3.connect(
        f"file:{db_path.as_posix()}?mode=ro&immutable=1",
        uri=True,
    )
    try:
        result = connection.execute("pragma quick_check").fetchone()[0]
    finally:
        connection.close()
    if result != "ok":
        fail(f"SQLite quick_check returned {result!r}")

    for relative_dir in ("data", "images"):
        path = ROOT / relative_dir
        if not path.is_dir():
            fail(f"missing directory: {relative_dir}")

    for relative_dir in ("logs", "backups", "exports"):
        path = ROOT / relative_dir
        path.mkdir(parents=True, exist_ok=True)
        if not os.access(path, os.W_OK):
            fail(f"directory is not writable: {relative_dir}")

    print(
        "PRECHECK_OK "
        f"admins={len(admin_ids)} courses={len(courses)} db={db_path.relative_to(ROOT)}"
    )


if __name__ == "__main__":
    main()
