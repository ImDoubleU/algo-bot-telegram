import sqlite3
from datetime import date, datetime, timedelta

from services.teacher_group_service import TeacherGroupManager


class GroupsService:
    def __init__(self, manager: TeacherGroupManager | None = None):
        self.manager = manager or TeacherGroupManager()

    def list_groups(self, teacher_id: int | None = None):
        if teacher_id is None:
            return self.list_all_groups()
        return self.manager.list_groups_for_teacher(teacher_id)

    def list_all_groups(self):
        conn = self.manager._connect(row_factory=sqlite3.Row)
        cur = conn.cursor()
        cur.execute("SELECT * FROM teacher_groups ORDER BY teacher_id ASC, weekday ASC, lesson_time ASC, group_name ASC")
        rows = cur.fetchall()
        conn.close()
        return rows

    def list_teacher_ids(self):
        conn = self.manager._connect(row_factory=sqlite3.Row)
        cur = conn.cursor()
        cur.execute(
            """
            SELECT teacher_id, COUNT(*) AS groups_count
            FROM teacher_groups
            GROUP BY teacher_id
            ORDER BY teacher_id ASC
            """
        )
        rows = cur.fetchall()
        conn.close()
        return rows

    def get_group(self, group_id: int):
        return self.manager.get_group(group_id)

    def create_group(self, **kwargs):
        return self.manager.add_group(**kwargs)

    def update_group_field(self, group_id: int, field_name: str, value):
        self.manager.update_group_field(group_id, field_name, value)

    def delete_group(self, group_id: int):
        self.manager.delete_group(group_id)

    def mark_sent(self, group_id: int, lesson_date: str, advance_lesson: bool = False):
        self.manager.mark_sent(group_id, lesson_date, advance_lesson=advance_lesson)

    def get_due_groups(self, now_dt):
        return self.manager.get_due_groups(now_dt)

    def get_reference_lesson_date(self, group_row) -> str:
        return self.manager.get_weekly_feedback_date_for_group(group_row)

    def get_lesson_number_for_date(self, group_row, lesson_date_str: str) -> int:
        reference_date = datetime.strptime(self.get_reference_lesson_date(group_row), "%Y-%m-%d").date()
        lesson_date = datetime.strptime(lesson_date_str, "%Y-%m-%d").date()
        delta_weeks = (lesson_date - reference_date).days // 7
        return int(group_row["current_lesson_number"]) + delta_weeks

    def shift_lesson_date(self, lesson_date_str: str, weeks: int) -> str:
        lesson_date = datetime.strptime(lesson_date_str, "%Y-%m-%d").date()
        return (lesson_date + timedelta(days=7 * weeks)).strftime("%Y-%m-%d")

    def build_week_options(
        self,
        group_row,
        selected_lesson_date: str,
        total_lessons: int,
        *,
        span_before: int = 6,
        span_after: int = 4,
        today: date | None = None,
    ) -> list[dict]:
        today_date = today or datetime.now().date()
        selected_date = datetime.strptime(selected_lesson_date, "%Y-%m-%d").date()
        reference_date = datetime.strptime(self.get_reference_lesson_date(group_row), "%Y-%m-%d").date()
        first_lesson_date = datetime.strptime(group_row["first_lesson_date"], "%Y-%m-%d").date()
        selected_offset = (selected_date - reference_date).days // 7

        options = []
        for delta in range(selected_offset - span_before, selected_offset + span_after + 1):
            lesson_date = reference_date + timedelta(days=7 * delta)
            lesson_number = int(group_row["current_lesson_number"]) + delta
            is_valid = first_lesson_date <= lesson_date and 1 <= lesson_number <= total_lessons
            options.append(
                {
                    "lesson_date": lesson_date.strftime("%Y-%m-%d"),
                    "label": lesson_date.strftime("%d.%m"),
                    "full_label": lesson_date.strftime("%d.%m.%Y"),
                    "lesson_number": lesson_number,
                    "is_selected": lesson_date == selected_date,
                    "is_past_or_today": lesson_date <= today_date,
                    "is_future": lesson_date > today_date,
                    "is_valid": is_valid,
                }
            )
        return options
