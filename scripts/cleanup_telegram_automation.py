from __future__ import annotations

import argparse
import csv
import json
import sqlite3
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
TABLES_TO_CLEAR = (
    "access_invites",
    "auto_feedback_outputs",
    "job_runs",
    "teacher_groups",
    "teacher_hub_access",
    "user_notifications",
    "user_work_files",
    "web_users",
)
AUTO_FEEDBACK_CSV_HEADERS = (
    "timestamp",
    "user_id",
    "user_tag",
    "user_name",
    "action",
)


def table_names(connection: sqlite3.Connection) -> set[str]:
    return {
        row[0]
        for row in connection.execute(
            "select name from sqlite_master where type = 'table'"
        )
    }


def row_counts(connection: sqlite3.Connection, tables: tuple[str, ...]) -> dict[str, int]:
    return {
        table: connection.execute(f'select count(*) from "{table}"').fetchone()[0]
        for table in tables
    }


def snapshot_database(source_path: Path, backup_path: Path) -> None:
    backup_path.parent.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(source_path, timeout=30)) as source_db:
        with closing(sqlite3.connect(backup_path)) as backup_db:
            source_db.backup(backup_db)


def reset_files(logs_dir: Path, groups_json_path: Path) -> dict[str, int]:
    csv_path = logs_dir / "auto_feedback_actions.csv"
    csv_path.parent.mkdir(parents=True, exist_ok=True)
    with csv_path.open("w", encoding="utf-8", newline="") as csv_file:
        csv.writer(csv_file).writerow(AUTO_FEEDBACK_CSV_HEADERS)

    removed_archives = 0
    archive_dir = logs_dir / "archive"
    if archive_dir.is_dir():
        for archive_path in archive_dir.glob("auto_feedback_actions*.csv"):
            archive_path.unlink()
            removed_archives += 1

    groups_removed = 0
    if groups_json_path.is_file():
        payload = json.loads(groups_json_path.read_text(encoding="utf-8"))
        if isinstance(payload, list):
            groups_removed = len(payload)
        groups_json_path.write_text("[]\n", encoding="utf-8")

    return {
        "auto_feedback_csv_rows_after": 0,
        "auto_feedback_archives_removed": removed_archives,
        "legacy_groups_removed": groups_removed,
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Back up and clear Telegram automation, groups, and staff accounts"
    )
    parser.add_argument("--db", type=Path, default=ROOT / "logs" / "bot_analytics.db")
    parser.add_argument("--logs-dir", type=Path, default=ROOT / "logs")
    parser.add_argument("--groups-json", type=Path, default=ROOT / "groups_for_user.json")
    parser.add_argument("--backup-dir", type=Path, default=ROOT / "backups")
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()

    db_path = args.db.resolve()
    if not db_path.is_file():
        raise SystemExit(f"Database not found: {db_path}")

    with closing(sqlite3.connect(f"file:{db_path.as_posix()}?mode=ro", uri=True)) as connection:
        existing_tables = table_names(connection)
        selected_tables = tuple(table for table in TABLES_TO_CLEAR if table in existing_tables)
        before = row_counts(connection, selected_tables)

    if not args.apply:
        print(json.dumps({"mode": "dry-run", "rows_to_delete": before}, sort_keys=True))
        return

    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    backup_path = args.backup_dir.resolve() / f"pre_cleanup_{timestamp}.db"
    snapshot_database(db_path, backup_path)

    with closing(sqlite3.connect(db_path, timeout=60)) as connection:
        connection.execute("pragma foreign_keys = on")
        connection.execute("begin immediate")
        try:
            for table in selected_tables:
                connection.execute(f'delete from "{table}"')
            if "sqlite_sequence" in existing_tables:
                placeholders = ",".join("?" for _ in selected_tables)
                connection.execute(
                    f"delete from sqlite_sequence where name in ({placeholders})",
                    selected_tables,
                )
            connection.commit()
        except Exception:
            connection.rollback()
            raise

        connection.execute("pragma wal_checkpoint(truncate)")
        connection.execute("vacuum")
        connection.execute("pragma journal_mode = wal")
        integrity = connection.execute("pragma quick_check").fetchone()[0]
        after = row_counts(connection, selected_tables)

    if integrity != "ok":
        raise SystemExit(f"Cleanup completed but SQLite quick_check returned {integrity!r}")

    file_result = reset_files(args.logs_dir.resolve(), args.groups_json.resolve())
    print(
        json.dumps(
            {
                "mode": "applied",
                "backup": str(backup_path),
                "rows_before": before,
                "rows_after": after,
                "sqlite_quick_check": integrity,
                **file_result,
            },
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
