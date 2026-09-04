import json

from app.settings import settings


class CoursesService:
    def __init__(self, courses_path=None):
        self.courses_path = courses_path or settings.courses_path
        self._courses = self._load_courses()

    def _load_courses(self) -> dict:
        with open(self.courses_path, "r", encoding="utf-8") as f:
            return json.load(f)

    def reload(self) -> None:
        self._courses = self._load_courses()

    def get_courses(self) -> dict:
        return self._courses

    def list_course_names(self) -> list[str]:
        return sorted(self._courses.keys())

    def get_lessons_in_order(self, course_name: str) -> list[tuple[str, dict]]:
        if course_name not in self._courses:
            return []
        lessons = list(self._courses[course_name].items())
        try:
            lessons.sort(key=lambda item: int(item[0].split()[-1]))
        except (ValueError, IndexError):
            lessons.sort(key=lambda item: item[0])
        return lessons

    def get_lesson(self, course_name: str, lesson_number: int) -> tuple[str | None, dict | None]:
        lessons = self.get_lessons_in_order(course_name)
        if lesson_number < 1 or lesson_number > len(lessons):
            return None, None
        return lessons[lesson_number - 1]
