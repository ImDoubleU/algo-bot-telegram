from __future__ import annotations

import argparse
import re
import shutil
import sqlite3
from contextlib import closing
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DIST = ROOT / "dist" / "algo_bot_telegram_bundle"
TOKEN_PATTERN = re.compile(rb"\b\d{6,12}:[A-Za-z0-9_-]{30,}\b")

SOURCE_DIRS = ("core", "services", "data", "images")
SOURCE_FILES = (
    "bot.py",
    "config.py",
    "backup_manager.py",
    "access_manager.py",
    "groups_for_user.json",
    "requirements-telegram.txt",
    ".env.telegram.example",
    "scripts/preflight_telegram.py",
    "scripts/cleanup_telegram_automation.py",
    "deploy/linux/algo-bot-telegram.service",
    "deploy/linux/install_telegram.sh",
    "deploy/linux/update_telegram.sh",
    "deploy/linux/README_TELEGRAM_RU.md",
)
STATE_FILES = (
    "logs/user_analytics.csv",
    "logs/auto_feedback_actions.csv",
)


def copy_tree(relative_path: str) -> None:
    source = ROOT / relative_path
    target = DIST / relative_path
    shutil.copytree(
        source,
        target,
        dirs_exist_ok=True,
        ignore=shutil.ignore_patterns("__pycache__", "*.pyc", "*.pyo"),
    )


def copy_file(relative_path: str) -> None:
    source = ROOT / relative_path
    if not source.is_file():
        raise FileNotFoundError(source)
    target = DIST / relative_path
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, target)


def snapshot_database() -> None:
    source_path = ROOT / "logs" / "bot_analytics.db"
    target_path = DIST / "logs" / "bot_analytics.db"
    target_path.parent.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(source_path, timeout=30)) as source_db:
        with closing(sqlite3.connect(target_path)) as target_db:
            source_db.backup(target_db)


def assert_no_embedded_tokens() -> None:
    findings = []
    for path in DIST.rglob("*"):
        if not path.is_file():
            continue
        tail = b""
        with path.open("rb") as source_file:
            while chunk := source_file.read(1024 * 1024):
                payload = tail + chunk
                if TOKEN_PATTERN.search(payload):
                    findings.append(str(path.relative_to(DIST)))
                    break
                tail = payload[-128:]
    if findings:
        raise RuntimeError(f"Telegram token-like values found in bundle: {findings}")


def write_notes(include_state: bool) -> None:
    state_note = (
        "A consistent SQLite snapshot and analytics CSV files are included."
        if include_state
        else "Runtime state is not included; use --include-state for the first deployment."
    )
    (DIST / "BUNDLE_NOTES.txt").write_text(
        "\n".join(
            (
                "Algo Bot Telegram-only production bundle",
                "",
                "Runs one long-polling process: bot.py.",
                "No webapp, reverse proxy, public port, or separate worker is included.",
                state_note,
                "The Telegram token is never included; install it via /etc/algo-bot/telegram.env.",
                "The old hard-coded token must be revoked before production launch.",
                "",
            )
        ),
        encoding="utf-8",
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Build the Telegram-only deployment bundle")
    parser.add_argument(
        "--include-state",
        action="store_true",
        help="include a consistent SQLite snapshot and analytics CSV files",
    )
    args = parser.parse_args()

    if DIST.exists():
        shutil.rmtree(DIST)
    DIST.mkdir(parents=True)

    for relative_path in SOURCE_DIRS:
        copy_tree(relative_path)
    for relative_path in SOURCE_FILES:
        copy_file(relative_path)
    for relative_dir in ("logs", "backups", "exports"):
        (DIST / relative_dir).mkdir(parents=True, exist_ok=True)

    if args.include_state:
        snapshot_database()
        for relative_path in STATE_FILES:
            if (ROOT / relative_path).is_file():
                copy_file(relative_path)

    write_notes(args.include_state)
    assert_no_embedded_tokens()
    print(f"BUNDLE_OK path={DIST} include_state={args.include_state}")


if __name__ == "__main__":
    main()
