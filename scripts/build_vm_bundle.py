from __future__ import annotations

import shutil
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DIST = ROOT / "dist" / "algo_bot_vm_bundle"

INCLUDE_DIRS = [
    "app",
    "webapp",
    "worker",
    "data",
]

INCLUDE_FILES = [
    "access_manager.py",
    "core/sqlite_service.py",
    "services/__init__.py",
    "services/activity_tracker_service.py",
    "services/teacher_group_service.py",
    "requirements-web.txt",
    "run_webapp.py",
    "run_worker.py",
    "logs/bot_analytics.db",
    "logs/user_analytics.csv",
    "logs/auto_feedback_actions.csv",
]

EXCLUDED_TOP_LEVEL = [
    "bot_env",
    "private",
    "backups",
    "exports",
    "images",
    "__pycache__",
    "bot.py",
    "config.py",
    "backup_manager.py",
    "add_n_groups_for_user.py",
    "list_auto.py",
    "groups_for_user.json",
]

SECRET_ENV_KEYS = {
    "WEBAPP_SECRET_KEY",
    "WEBAPP_ADMIN_PASSWORD",
}


def _ignore_filter(_path: str, names: list[str]) -> set[str]:
    ignored: set[str] = set()
    for name in names:
        if name == "__pycache__" or name.endswith(".pyc") or name.endswith(".pyo"):
            ignored.add(name)
    return ignored


def _copy_dir(relative_path: str) -> None:
    source = ROOT / relative_path
    target = DIST / relative_path
    shutil.copytree(source, target, ignore=_ignore_filter, dirs_exist_ok=True)


def _copy_file(relative_path: str) -> None:
    source = ROOT / relative_path
    target = DIST / relative_path
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, target)


def _write_sanitized_env_sample() -> None:
    source = ROOT / ".env.webapp"
    if not source.exists():
        return

    lines: list[str] = []
    for raw_line in source.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            lines.append(raw_line)
            continue
        key, value = raw_line.split("=", 1)
        if key.strip() in SECRET_ENV_KEYS:
            lines.append(f'{key}="REPLACE_ME"')
        else:
            lines.append(raw_line)

    sample_path = DIST / ".env.webapp.sample"
    sample_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _write_notes() -> None:
    notes = """Algo Bot VM bundle

Included:
- app/
- webapp/
- worker/
- data/
- access_manager.py
- core/sqlite_service.py
- services/activity_tracker_service.py
- services/teacher_group_service.py
- services/__init__.py
- requirements-web.txt
- run_webapp.py
- run_worker.py
- logs/bot_analytics.db
- logs/user_analytics.csv
- logs/auto_feedback_actions.csv
- .env.webapp.sample

Excluded on purpose:
- bot_env/
- private/
- backups/
- exports/
- images/
- __pycache__/
- bot.py
- config.py
- backup_manager.py
- add_n_groups_for_user.py
- list_auto.py
- groups_for_user.json
- core/callback_router.py
- core/telegram_api.py
- core/ui_errors.py
- core/validation.py
- services/teacher_hub_service.py
- logs/bot_activity.log*

Security notes:
- Source config.py contains a Telegram bot token fallback and must not be uploaded to the VM.
- Source logs/bot_activity.log* contain Telegram API URLs with the bot token and must not be uploaded.
- Source private/ contains credential exports and must not be uploaded.
- Copy your real .env.webapp to the server separately if needed; this bundle contains only .env.webapp.sample.
"""
    (DIST / "BUNDLE_NOTES.txt").write_text(notes, encoding="utf-8")


def main() -> None:
    if DIST.exists():
        shutil.rmtree(DIST)
    DIST.mkdir(parents=True, exist_ok=True)

    for directory in INCLUDE_DIRS:
        _copy_dir(directory)

    for file_path in INCLUDE_FILES:
        _copy_file(file_path)

    _write_sanitized_env_sample()
    _write_notes()

    print(f"Bundle created: {DIST}")


if __name__ == "__main__":
    main()
