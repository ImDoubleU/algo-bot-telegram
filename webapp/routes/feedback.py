from fastapi import APIRouter, Depends, Form, Request

from webapp.deps import actions_service, get_current_actor, get_current_user
from webapp.routes.helpers import templates


router = APIRouter(prefix="/feedback")


@router.post("/preview")
async def preview_feedback(
    request: Request,
    course_name: str = Form(...),
    lesson_number: int = Form(...),
    lesson_date: str = Form(...),
    offset: int = Form(0),
    absent_students: str = Form(""),
    is_online_or_individual: bool = Form(False),
    is_repetition: bool = Form(False),
    user=Depends(get_current_user),
    actor=Depends(get_current_actor),
):
    absent_list = [item.strip() for item in absent_students.split() if item.strip()]
    result = actions_service.preview_feedback(
        actor=actor,
        course_name=course_name,
        lesson_number=lesson_number,
        lesson_date=lesson_date,
        offset=offset,
        absent_students=absent_list,
        is_online_or_individual=is_online_or_individual,
        is_repetition=is_repetition,
    )
    return templates.TemplateResponse(
        "feedback_preview.html",
        {
            "request": request,
            "user": user,
            "result": result,
            "course_name": course_name,
            "lesson_number": lesson_number,
            "lesson_date": lesson_date,
            "offset": offset,
            "absent_students": absent_students,
            "is_online_or_individual": is_online_or_individual,
            "is_repetition": is_repetition,
        },
        status_code=200 if result.ok else 400,
    )


@router.post("/render")
async def render_feedback(
    request: Request,
    course_name: str = Form(...),
    lesson_number: int = Form(...),
    lesson_date: str = Form(...),
    offset: int = Form(0),
    absent_students: str = Form(""),
    is_online_or_individual: bool = Form(False),
    is_repetition: bool = Form(False),
    user=Depends(get_current_user),
    actor=Depends(get_current_actor),
):
    return await preview_feedback(
        request=request,
        course_name=course_name,
        lesson_number=lesson_number,
        lesson_date=lesson_date,
        offset=offset,
        absent_students=absent_students,
        is_online_or_individual=is_online_or_individual,
        is_repetition=is_repetition,
        user=user,
        actor=actor,
    )
