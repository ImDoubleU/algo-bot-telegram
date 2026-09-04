from datetime import datetime

from fastapi import APIRouter, Depends, Request

from webapp.deps import courses_service, get_current_user
from webapp.routes.helpers import templates


router = APIRouter(prefix="/courses")


@router.get("")
async def courses_list(request: Request, user=Depends(get_current_user)):
    course_cards = []
    for course_name in courses_service.list_course_names():
        lessons = courses_service.get_lessons_in_order(course_name)
        course_cards.append(
            {
                "name": course_name,
                "lesson_count": len(lessons),
                "preview_text": lessons[0][1].get("educational_results", "")[:180] if lessons else "",
            }
        )
    return templates.TemplateResponse(
        "courses.html",
        {"request": request, "user": user, "courses": course_cards},
    )


@router.get("/{course_name:path}")
async def course_detail(request: Request, course_name: str, user=Depends(get_current_user)):
    lessons = courses_service.get_lessons_in_order(course_name)
    today = datetime.now().strftime("%Y-%m-%d")
    return templates.TemplateResponse(
        "course_detail.html",
        {
            "request": request,
            "user": user,
            "course_name": course_name,
            "lessons": lessons,
            "today": today,
        },
    )
