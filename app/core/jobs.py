import csv
import os
from datetime import datetime

from core.sqlite_service import SQLiteService

from app.settings import settings


class JobsService:
    def __init__(self, db_path=None, auto_feedback_csv_path=None):
        self.db = SQLiteService(str(db_path or settings.db_path))
        self.auto_feedback_csv_path = str(auto_feedback_csv_path or settings.auto_feedback_csv_path)
        self._init_tables()

    def _init_tables(self):
        self.db.execute_script([
            "PRAGMA journal_mode = WAL",
            "PRAGMA synchronous = NORMAL",
            """
            CREATE TABLE IF NOT EXISTS job_runs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                job_name TEXT NOT NULL,
                status TEXT NOT NULL,
                details TEXT DEFAULT '',
                group_id INTEGER,
                lesson_date TEXT DEFAULT '',
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS user_notifications (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                level TEXT NOT NULL DEFAULT 'info',
                title TEXT NOT NULL,
                message TEXT NOT NULL,
                is_read INTEGER NOT NULL DEFAULT 0,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS auto_feedback_outputs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                group_id INTEGER NOT NULL,
                lesson_date TEXT NOT NULL,
                lesson_name TEXT DEFAULT '',
                feedback_text TEXT NOT NULL,
                image_path TEXT DEFAULT '',
                status TEXT NOT NULL DEFAULT 'success',
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP
            )
            """,
        ])

    def record_job_run(self, job_name: str, status: str, details: str = "", group_id: int | None = None, lesson_date: str = ""):
        self.db.execute(
            """
            INSERT INTO job_runs (job_name, status, details, group_id, lesson_date)
            VALUES (?, ?, ?, ?, ?)
            """,
            (job_name, status, details, group_id, lesson_date),
        )

    def get_recent_job_runs(self, limit: int = 50):
        return self.db.fetchall(
            """
            SELECT id, job_name, status, details, group_id, lesson_date, created_at
            FROM job_runs
            ORDER BY id DESC
            LIMIT ?
            """,
            (limit,),
        )

    def save_auto_feedback_output(
        self,
        group_id: int,
        lesson_date: str,
        lesson_name: str,
        feedback_text: str,
        image_path: str = "",
        status: str = "success",
    ):
        self.db.execute(
            """
            INSERT INTO auto_feedback_outputs
            (group_id, lesson_date, lesson_name, feedback_text, image_path, status)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (group_id, lesson_date, lesson_name, feedback_text, image_path, status),
        )

    def get_latest_auto_feedback_output(self, group_id: int):
        return self.db.fetchone(
            """
            SELECT id, group_id, lesson_date, lesson_name, feedback_text, image_path, status, created_at
            FROM auto_feedback_outputs
            WHERE group_id = ?
            ORDER BY id DESC
            LIMIT 1
            """,
            (group_id,),
        )

    def get_auto_feedback_outputs_for_group(self, group_id: int, limit: int = 20):
        return self.db.fetchall(
            """
            SELECT id, group_id, lesson_date, lesson_name, feedback_text, image_path, status, created_at
            FROM auto_feedback_outputs
            WHERE group_id = ?
            ORDER BY id DESC
            LIMIT ?
            """,
            (group_id, limit),
        )

    def list_all_auto_feedback_outputs_for_group(self, group_id: int):
        return self.db.fetchall(
            """
            SELECT id, group_id, lesson_date, lesson_name, feedback_text, image_path, status, created_at
            FROM auto_feedback_outputs
            WHERE group_id = ?
            ORDER BY lesson_date ASC, id ASC
            """,
            (group_id,),
        )

    def get_auto_feedback_output(self, output_id: int):
        return self.db.fetchone(
            """
            SELECT id, group_id, lesson_date, lesson_name, feedback_text, image_path, status, created_at
            FROM auto_feedback_outputs
            WHERE id = ?
            """,
            (output_id,),
        )

    def get_output_for_group_and_lesson_date(self, group_id: int, lesson_date: str):
        return self.db.fetchone(
            """
            SELECT id, group_id, lesson_date, lesson_name, feedback_text, image_path, status, created_at
            FROM auto_feedback_outputs
            WHERE group_id = ? AND lesson_date = ?
            ORDER BY id DESC
            LIMIT 1
            """,
            (group_id, lesson_date),
        )

    def update_auto_feedback_output(
        self,
        output_id: int,
        *,
        lesson_name: str,
        feedback_text: str,
        image_path: str = "",
        status: str = "success",
    ):
        self.db.execute(
            """
            UPDATE auto_feedback_outputs
            SET lesson_name = ?, feedback_text = ?, image_path = ?, status = ?
            WHERE id = ?
            """,
            (lesson_name, feedback_text, image_path, status, output_id),
        )

    def create_notification(self, user_id: int, title: str, message: str, level: str = "info"):
        self.db.execute(
            """
            INSERT INTO user_notifications (user_id, level, title, message, is_read)
            VALUES (?, ?, ?, ?, 0)
            """,
            (user_id, level, title, message),
        )

    def create_notification_for_teacher(self, teacher_id: int, title: str, message: str, level: str = "info"):
        linked_users = self.db.fetchall(
            """
            SELECT id
            FROM web_users
            WHERE linked_teacher_id = ? AND is_active = 1
            """,
            (teacher_id,),
        )
        for row in linked_users:
            self.create_notification(int(row[0]), title, message, level)

    def list_notifications(self, user_id: int, unread_only: bool = False, limit: int = 20):
        if unread_only:
            return self.db.fetchall(
                """
                SELECT id, level, title, message, is_read, created_at
                FROM user_notifications
                WHERE user_id = ? AND is_read = 0
                ORDER BY id DESC
                LIMIT ?
                """,
                (user_id, limit),
            )
        return self.db.fetchall(
            """
            SELECT id, level, title, message, is_read, created_at
            FROM user_notifications
            WHERE user_id = ?
            ORDER BY id DESC
            LIMIT ?
            """,
            (user_id, limit),
        )

    def mark_notifications_read(self, user_id: int):
        self.db.execute(
            """
            UPDATE user_notifications
            SET is_read = 1
            WHERE user_id = ? AND is_read = 0
            """,
            (user_id,),
        )

    def log_auto_feedback_csv(self, action: str, user_id: int | str = "", user_tag: str = "", user_name: str = ""):
        headers = ["timestamp", "user_id", "user_tag", "user_name", "action"]
        os.makedirs(os.path.dirname(self.auto_feedback_csv_path), exist_ok=True)
        if not os.path.exists(self.auto_feedback_csv_path):
            with open(self.auto_feedback_csv_path, "w", encoding="utf-8", newline="") as csv_file:
                csv.writer(csv_file).writerow(headers)
        with open(self.auto_feedback_csv_path, "a", encoding="utf-8", newline="") as csv_file:
            csv.writer(csv_file).writerow([
                datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                user_id,
                user_tag,
                user_name,
                action,
            ])
