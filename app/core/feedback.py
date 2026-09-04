from datetime import datetime

from app.core.courses import CoursesService
from app.core.models import ActionResult


def get_time_based_greeting(now: datetime | None = None) -> str:
    current_hour = (now or datetime.now()).hour
    if 6 <= current_hour < 12:
        return "Доброе утро, уважаемые родители!"
    if 12 <= current_hour < 18:
        return "Добрый день, уважаемые родители!"
    return "Добрый вечер, уважаемые родители!"


class FeedbackService:
    def __init__(self, courses_service: CoursesService):
        self.courses_service = courses_service

    def format_feedback(
        self,
        lesson_name: str,
        lesson_number: int,
        lesson_date: datetime,
        feedback_data: dict,
        offset: int = 0,
        is_repetition: bool = False,
    ) -> str:
        adjusted_lesson_number = lesson_number + offset
        formatted_date = lesson_date.strftime("%d.%m.%Y")
        greeting = get_time_based_greeting()
        educational_text = (
            "Сегодня мы с ребятами повторяли тему предыдущего занятия, чтобы укрепить знания по ней."
            if is_repetition else feedback_data.get("educational_results", "")
        )
        return f"""Обратная связь урок №{adjusted_lesson_number:02d} от {formatted_date}

{greeting}

{educational_text}

Начислены астрокоины за урок №{adjusted_lesson_number:02d} от {formatted_date}
Количество астрокоинов, а также куда их потратить, можно посмотреть на сайте
http://algoritmika52.ru/

На онлайн-платформе «Алгоритмика» предоставлен весь материал, пройденный на уроках, и прогресс ребенка.

Удачной недели!"""

    def format_feedback_with_absent_students(
        self,
        lesson_name: str,
        lesson_number: int,
        lesson_date: datetime,
        feedback_data: dict,
        absent_students: list[str],
        offset: int = 0,
        is_repetition: bool = False,
    ) -> str:
        adjusted_lesson_number = lesson_number + offset
        formatted_date = lesson_date.strftime("%d.%m.%Y")
        greeting = get_time_based_greeting()
        educational_text = (
            "Сегодня мы с ребятами повторяли тему предыдущего занятия, чтобы укрепить знания по ней."
            if is_repetition else feedback_data.get("educational_results", "")
        )
        absent_text = self._format_absent_students(absent_students)
        return f"""Обратная связь урок №{adjusted_lesson_number:02d} от {formatted_date}

{greeting}

{educational_text}

{absent_text}

Начислены астрокоины за урок №{adjusted_lesson_number:02d} от {formatted_date}
Количество астрокоинов, а также куда их потратить, можно посмотреть на сайте
http://algoritmika52.ru/

На онлайн-платформе «Алгоритмика» предоставлен весь материал, пройденный на уроках, и прогресс ребенка.

Удачной недели!"""

    def format_feedback_online_individual(
        self,
        lesson_name: str,
        lesson_number: int,
        lesson_date: datetime,
        feedback_data: dict,
        absent_students: list[str] | None = None,
        offset: int = 0,
        is_repetition: bool = False,
    ) -> str:
        adjusted_lesson_number = lesson_number + offset
        formatted_date = lesson_date.strftime("%d.%m.%Y")
        greeting = get_time_based_greeting()
        educational_text = (
            "Сегодня мы с ребятами повторяли тему предыдущего занятия, чтобы укрепить знания по ней."
            if is_repetition else feedback_data.get("educational_results", "")
        )
        absent_text = f"\n\n{self._format_absent_students(absent_students)}" if absent_students else ""
        return f"""Обратная связь урок №{adjusted_lesson_number:02d} от {formatted_date}

{greeting}

{educational_text}{absent_text}

На онлайн-платформе «Алгоритмика» предоставлен весь материал, пройденный на уроках, и прогресс ребенка.

Удачной недели!"""

    def build_feedback_preview(
        self,
        course_name: str,
        lesson_number: int,
        lesson_date: str,
        offset: int = 0,
        absent_students: list[str] | None = None,
        is_online_or_individual: bool = False,
        is_repetition: bool = False,
    ) -> ActionResult:
        lesson_name, feedback_data = self.courses_service.get_lesson(course_name, lesson_number)
        if not lesson_name:
            return ActionResult(ok=False, message="Урок не найден")
        lesson_dt = datetime.strptime(lesson_date, "%Y-%m-%d")
        if is_online_or_individual:
            text = self.format_feedback_online_individual(
                lesson_name,
                lesson_number,
                lesson_dt,
                feedback_data,
                absent_students,
                offset,
                is_repetition,
            )
        elif absent_students:
            text = self.format_feedback_with_absent_students(
                lesson_name,
                lesson_number,
                lesson_dt,
                feedback_data,
                absent_students,
                offset,
                is_repetition,
            )
        else:
            text = self.format_feedback(
                lesson_name,
                lesson_number,
                lesson_dt,
                feedback_data,
                offset,
                is_repetition,
            )
        return ActionResult(ok=True, data={"lesson_name": lesson_name, "feedback_text": text, "feedback_data": feedback_data})

    def build_auto_feedback_content(
        self,
        group_row,
        lesson_date_str: str,
        lesson_number_override: int | None = None,
    ) -> ActionResult:
        course_name = group_row["course_name"]
        lessons = self.courses_service.get_lessons_in_order(course_name)
        lesson_number = int(lesson_number_override or group_row["current_lesson_number"])
        lesson_offset = int(group_row["lesson_offset"])
        if lesson_number < 1 or lesson_number > len(lessons):
            return ActionResult(ok=False, message=f"Номер урока {lesson_number} вне диапазона 1..{len(lessons)}")

        lesson_name, feedback_data = lessons[lesson_number - 1]
        lesson_date = datetime.strptime(lesson_date_str, "%Y-%m-%d")
        if group_row["lesson_mode"] == "individual" or group_row["lesson_place"] == "online":
            feedback_text = self.format_feedback_online_individual(
                lesson_name,
                lesson_number,
                lesson_date,
                feedback_data,
                None,
                offset=lesson_offset,
            )
        else:
            feedback_text = self.format_feedback(
                lesson_name,
                lesson_number,
                lesson_date,
                feedback_data,
                offset=lesson_offset,
            )

        return ActionResult(
            ok=True,
            data={
                "lesson_name": lesson_name,
                "feedback_text": feedback_text,
                "image_path": feedback_data.get("image"),
                "course_name": course_name,
                "lesson_number": lesson_number,
                "lesson_date_str": lesson_date_str,
            },
        )

    def _format_absent_students(self, absent_students: list[str] | None) -> str:
        if not absent_students:
            return ""
        if len(absent_students) == 1:
            return f"{absent_students[0]}, ждем на отработке за 30 минут до начала следующего занятия."
        if len(absent_students) == 2:
            return f"{absent_students[0]} и {absent_students[1]}, ждем на отработке за 30 минут до начала следующего занятия."
        names_except_last = ", ".join(absent_students[:-1])
        last_name = absent_students[-1]
        return f"{names_except_last} и {last_name}, ждем на отработке за 30 минут до начала следующего занятия."
