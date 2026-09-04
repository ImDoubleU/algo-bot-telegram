import sqlite3
from datetime import datetime, timedelta

WEEKDAY_NAMES = {
    0: "Понедельник",
    1: "Вторник",
    2: "Среда",
    3: "Четверг",
    4: "Пятница",
    5: "Суббота",
    6: "Воскресенье",
}


class TeacherGroupManager:
    """Хранилище групп преподавателей для автоматической отправки ОС."""

    def __init__(self, db_path='logs/bot_analytics.db'):
        self.db_path = db_path
        self.init_table()

    def _connect(self, row_factory=None):
        conn = sqlite3.connect(self.db_path, timeout=15)
        conn.execute("PRAGMA busy_timeout = 5000")
        if row_factory is not None:
            conn.row_factory = row_factory
        return conn

    def init_table(self):
        conn = self._connect()
        cursor = conn.cursor()
        cursor.execute("PRAGMA journal_mode = WAL")
        cursor.execute("PRAGMA synchronous = NORMAL")
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS teacher_groups (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                teacher_id INTEGER NOT NULL,
                teacher_chat_id INTEGER NOT NULL,
                group_name TEXT NOT NULL,
                course_name TEXT NOT NULL,
                lesson_offset INTEGER DEFAULT 0,
                current_lesson_number INTEGER DEFAULT 1,
                first_lesson_date TEXT NOT NULL,
                weekday INTEGER NOT NULL,
                lesson_time TEXT NOT NULL,
                lesson_mode TEXT NOT NULL,
                lesson_place TEXT NOT NULL,
                is_active INTEGER DEFAULT 1,
                last_sent_for_date TEXT,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        conn.commit()
        conn.close()

    def add_group(self, teacher_id, teacher_chat_id, group_name, course_name, lesson_offset,
                  current_lesson_number, first_lesson_date, weekday, lesson_time,
                  lesson_mode, lesson_place):
        # If a lesson is already considered finished at creation time,
        # mark it as already sent to avoid instant "catch-up" auto-send.
        now_dt = datetime.now()
        temp_group = {
            "first_lesson_date": first_lesson_date,
            "lesson_time": lesson_time,
            "lesson_mode": lesson_mode
        }
        initial_last_sent = self._get_due_lesson_date(temp_group, now_dt)

        conn = self._connect()
        cursor = conn.cursor()
        cursor.execute('''
            INSERT INTO teacher_groups (
                teacher_id, teacher_chat_id, group_name, course_name, lesson_offset,
                current_lesson_number, first_lesson_date, weekday, lesson_time,
                lesson_mode, lesson_place, is_active, last_sent_for_date
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?)
        ''', (
            teacher_id, teacher_chat_id, group_name, course_name, lesson_offset,
            current_lesson_number, first_lesson_date, weekday, lesson_time,
            lesson_mode, lesson_place, initial_last_sent
        ))
        group_id = cursor.lastrowid
        conn.commit()
        conn.close()
        return group_id

    def list_groups_for_teacher(self, teacher_id):
        conn = self._connect(row_factory=sqlite3.Row)
        cursor = conn.cursor()
        cursor.execute('''
            SELECT *
            FROM teacher_groups
            WHERE teacher_id = ?
            ORDER BY weekday ASC, lesson_time ASC, group_name ASC
        ''', (teacher_id,))
        rows = cursor.fetchall()
        conn.close()
        return rows

    def get_group(self, group_id):
        conn = self._connect(row_factory=sqlite3.Row)
        cursor = conn.cursor()
        cursor.execute('SELECT * FROM teacher_groups WHERE id = ?', (group_id,))
        row = cursor.fetchone()
        conn.close()
        return row

    def update_group_field(self, group_id, field_name, value):
        if field_name not in {
            "group_name", "course_name", "lesson_offset", "current_lesson_number",
            "first_lesson_date", "weekday", "lesson_time", "lesson_mode",
            "lesson_place", "is_active", "last_sent_for_date"
        }:
            return
        conn = self._connect()
        cursor = conn.cursor()
        cursor.execute(
            f"UPDATE teacher_groups SET {field_name} = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
            (value, group_id)
        )
        conn.commit()
        conn.close()

    def delete_group(self, group_id):
        conn = self._connect()
        cursor = conn.cursor()
        cursor.execute('DELETE FROM teacher_groups WHERE id = ?', (group_id,))
        conn.commit()
        conn.close()

    def mark_sent(self, group_id, lesson_date, advance_lesson=False):
        conn = self._connect()
        cursor = conn.cursor()
        if advance_lesson:
            cursor.execute('''
                UPDATE teacher_groups
                SET last_sent_for_date = ?,
                    current_lesson_number = current_lesson_number + 1,
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
            ''', (lesson_date, group_id))
        else:
            cursor.execute('''
                UPDATE teacher_groups
                SET last_sent_for_date = ?,
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
            ''', (lesson_date, group_id))
        conn.commit()
        conn.close()

    def _parse_start_datetime(self, first_lesson_date, lesson_time):
        return datetime.strptime(
            f"{first_lesson_date} {lesson_time}",
            "%Y-%m-%d %H:%M"
        )

    def _lesson_duration_minutes(self, lesson_mode):
        # Auto feedback is sent relative to lesson start time.
        # individual: +60 minutes, group: +90 minutes.
        return 60 if lesson_mode == "individual" else 90

    def _get_due_lesson_date(self, group_row, now_dt):
        try:
            first_start = self._parse_start_datetime(
                group_row["first_lesson_date"], group_row["lesson_time"]
            )
        except ValueError:
            return None

        duration = self._lesson_duration_minutes(group_row["lesson_mode"])
        if now_dt < first_start + timedelta(minutes=duration):
            return None

        days_since_start = (now_dt.date() - first_start.date()).days
        if days_since_start < 0:
            return None

        weeks_since_start = days_since_start // 7
        due_date = first_start.date() + timedelta(days=7 * weeks_since_start)
        due_start = datetime.strptime(
            f"{due_date.strftime('%Y-%m-%d')} {group_row['lesson_time']}",
            "%Y-%m-%d %H:%M"
        )

        if now_dt < due_start + timedelta(minutes=duration):
            due_date = due_date - timedelta(days=7)
            if due_date < first_start.date():
                return None

        return due_date.strftime("%Y-%m-%d")

    def get_due_groups(self, now_dt):
        conn = self._connect(row_factory=sqlite3.Row)
        cursor = conn.cursor()
        cursor.execute('SELECT * FROM teacher_groups WHERE is_active = 1')
        rows = cursor.fetchall()
        conn.close()

        due_groups = []
        for row in rows:
            due_lesson_date = self._get_due_lesson_date(row, now_dt)
            if not due_lesson_date:
                continue
            if row["last_sent_for_date"] == due_lesson_date:
                continue
            due_groups.append((row, due_lesson_date))
        return due_groups

    def get_weekly_feedback_date_for_group(self, group_row):
        """
        Returns the scheduled lesson date tied to weekly cadence.
        For manual test sends this keeps the lesson date stable and not tied to "today".
        """
        def _field(row, key):
            if isinstance(row, dict):
                return row.get(key)
            try:
                return row[key]
            except (KeyError, TypeError, IndexError):
                return None

        last_sent_for_date = _field(group_row, "last_sent_for_date")
        if last_sent_for_date:
            try:
                return (
                    datetime.strptime(last_sent_for_date, "%Y-%m-%d").date() + timedelta(days=7)
                ).strftime("%Y-%m-%d")
            except ValueError:
                pass

        first_lesson_date = _field(group_row, "first_lesson_date")
        if first_lesson_date:
            try:
                return datetime.strptime(first_lesson_date, "%Y-%m-%d").strftime("%Y-%m-%d")
            except ValueError:
                pass

        # Safe fallback to avoid crashes on malformed data.
        return datetime.now().strftime("%Y-%m-%d")


