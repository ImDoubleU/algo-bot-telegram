from datetime import datetime

from fastapi import APIRouter, Depends, Form, Query, Request
from fastapi.responses import RedirectResponse

from app.core.models import ActionResult
from services.teacher_group_service import WEEKDAY_NAMES
from webapp.deps import actions_service, courses_service, get_current_actor, get_current_user, get_linked_teacher_id, groups_service, jobs_service, stats_service
from webapp.routes.helpers import templates


router = APIRouter(prefix="/groups")

WEEKDAY_OPTIONS = [(index, title) for index, title in WEEKDAY_NAMES.items()]
MODE_OPTIONS = [("group", "Групповое"), ("individual", "Индивидуальное")]
PLACE_OPTIONS = [("offline", "Оффлайн"), ("online", "Онлайн")]
MODE_LABELS = dict(MODE_OPTIONS)
PLACE_LABELS = dict(PLACE_OPTIONS)


def _teacher_label(teacher_id: int | None) -> str:
    if not teacher_id:
        return ""
    username, full_name = stats_service.tracker.get_latest_user_identity(teacher_id)
    return full_name or username or f"teacher_id {teacher_id}"


def _teacher_choices():
    choices = []
    for row in groups_service.list_teacher_ids():
        teacher_id = int(row["teacher_id"])
        choices.append(
            {
                "teacher_id": teacher_id,
                "label": _teacher_label(teacher_id),
                "groups_count": row["groups_count"],
            }
        )
    return choices


def _selected_teacher_id(user: dict, teacher_id_override: int | None = None) -> int | None:
    if user["role"] == "teacher":
        return get_linked_teacher_id(user)
    if teacher_id_override is not None:
        return teacher_id_override
    return get_linked_teacher_id(user)


def _resolve_allowed_groups(user: dict, teacher_id_override: int | None = None):
    teacher_id = _selected_teacher_id(user, teacher_id_override)
    if teacher_id:
        return groups_service.list_groups(teacher_id), teacher_id
    return groups_service.list_all_groups(), teacher_id


def _ensure_group_access(user: dict, group):
    if not group:
        return False
    teacher_id = get_linked_teacher_id(user)
    if user["role"] == "teacher":
        return teacher_id is not None and int(group["teacher_id"]) == teacher_id
    return True


def _group_card(group):
    latest_output = jobs_service.get_latest_auto_feedback_output(int(group["id"]))
    return {
        "id": int(group["id"]),
        "teacher_id": int(group["teacher_id"]),
        "group_name": group["group_name"],
        "course_name": group["course_name"],
        "lesson_time": group["lesson_time"],
        "lesson_mode": group["lesson_mode"],
        "lesson_mode_label": MODE_LABELS.get(group["lesson_mode"], group["lesson_mode"]),
        "lesson_place": group["lesson_place"],
        "lesson_place_label": PLACE_LABELS.get(group["lesson_place"], group["lesson_place"]),
        "current_lesson_number": int(group["current_lesson_number"]),
        "first_lesson_date": group["first_lesson_date"],
        "next_feedback_date": groups_service.get_reference_lesson_date(group),
        "last_sent_for_date": group["last_sent_for_date"],
        "is_active": bool(group["is_active"]),
        "latest_output_id": latest_output[0] if latest_output else None,
        "latest_output_date": latest_output[2] if latest_output else "",
        "latest_output_status": latest_output[6] if latest_output else "",
    }


def _build_schedule(groups):
    rows = []
    for weekday_index, weekday_title in WEEKDAY_OPTIONS:
        day_groups = [_group_card(group) for group in groups if int(group["weekday"]) == weekday_index]
        day_groups.sort(key=lambda item: (item["lesson_time"], item["group_name"]))
        rows.append({"weekday_index": weekday_index, "weekday_title": weekday_title, "groups": day_groups})
    return rows


def _groups_page_context(
    request: Request,
    user: dict,
    teacher_id_override: int | None = None,
    status_code: int = 200,
    error: str = "",
    success: str = "",
):
    groups, teacher_id = _resolve_allowed_groups(user, teacher_id_override)
    active_groups = sum(1 for group in groups if int(group["is_active"]) == 1)
    return templates.TemplateResponse(
        "groups.html",
        {
            "request": request,
            "user": user,
            "groups": groups,
            "course_names": courses_service.list_course_names(),
            "teacher_id": teacher_id,
            "teacher_name": _teacher_label(teacher_id),
            "teacher_choices": _teacher_choices() if user["role"] == "admin" else [],
            "schedule_rows": _build_schedule(groups),
            "groups_count": len(groups),
            "active_groups_count": active_groups,
            "error": error,
            "success": success,
            "weekday_options": WEEKDAY_OPTIONS,
            "mode_options": MODE_OPTIONS,
            "place_options": PLACE_OPTIONS,
        },
        status_code=status_code,
    )


def _prepare_week_context(group, selected_lesson_date: str, selected_output_id: int | None = None):
    lessons = courses_service.get_lessons_in_order(group["course_name"])
    total_lessons = len(lessons)
    week_options = groups_service.build_week_options(group, selected_lesson_date, total_lessons)

    for item in week_options:
        item["output"] = jobs_service.get_output_for_group_and_lesson_date(int(group["id"]), item["lesson_date"]) if item["is_valid"] else None
        item["state"] = "future"
        if item["output"]:
            item["state"] = "ready"
        elif not item["is_valid"]:
            item["state"] = "disabled"
        elif item["is_past_or_today"]:
            result = actions_service.ensure_auto_feedback_output_for_group_date(
                int(group["id"]),
                item["lesson_date"],
                item["lesson_number"],
            )
            if result.ok:
                item["output"] = result.data["output"]
                item["state"] = "ready"
            else:
                item["state"] = "error"
                item["error_message"] = result.message

        item["output_id"] = item["output"][0] if item.get("output") else None

    selected_week = next((item for item in week_options if item["lesson_date"] == selected_lesson_date), None)
    selected_output = None
    if selected_output_id:
        candidate = jobs_service.get_auto_feedback_output(selected_output_id)
        if candidate and int(candidate[1]) == int(group["id"]):
            selected_output = candidate
    if not selected_output and selected_week and selected_week.get("output"):
        selected_output = selected_week["output"]

    selected_hint = ""
    if selected_week and not selected_output:
        if not selected_week["is_valid"]:
            selected_hint = "Для этой недели в курсе нет соответствующего урока."
        elif selected_week["is_future"]:
            selected_hint = "Для будущей недели ОС еще не сформирована. Она появится после занятия или после ручного запуска."
        else:
            selected_hint = selected_week.get("error_message", "Для выбранной недели ОС пока не удалось подготовить.")

    selected_index = next((index for index, item in enumerate(week_options) if item["lesson_date"] == selected_lesson_date), None)
    previous_week = week_options[selected_index - 1] if selected_index is not None and selected_index > 0 else None
    next_week = week_options[selected_index + 1] if selected_index is not None and selected_index < len(week_options) - 1 else None

    return {
        "week_options": week_options,
        "selected_week": selected_week,
        "selected_output": selected_output,
        "selected_hint": selected_hint,
        "previous_week": previous_week,
        "next_week": next_week,
        "reference_week_date": groups_service.get_reference_lesson_date(group),
    }


def _render_group_detail(
    request: Request,
    user: dict,
    group,
    *,
    lesson_date: str | None = None,
    output_id: int | None = None,
    run_result=None,
    status_code: int = 200,
):
    selected_output = None
    if output_id:
        candidate = jobs_service.get_auto_feedback_output(output_id)
        if candidate and int(candidate[1]) == int(group["id"]):
            selected_output = candidate

    selected_lesson_date = lesson_date or (selected_output[2] if selected_output else groups_service.get_reference_lesson_date(group))
    week_context = _prepare_week_context(group, selected_lesson_date, output_id)
    latest_output = jobs_service.get_latest_auto_feedback_output(int(group["id"]))
    jobs = [job for job in jobs_service.get_recent_job_runs(limit=50) if job[4] == int(group["id"])]

    return templates.TemplateResponse(
        "group_detail.html",
        {
            "request": request,
            "user": user,
            "group": group,
            "jobs": jobs,
            "latest_output": latest_output,
            "selected_output": week_context["selected_output"],
            "selected_week": week_context["selected_week"],
            "selected_hint": week_context["selected_hint"],
            "previous_week": week_context["previous_week"],
            "next_week": week_context["next_week"],
            "reference_week_date": week_context["reference_week_date"],
            "week_options": week_context["week_options"],
            "today": selected_lesson_date,
            "run_result": run_result,
            "teacher_name": _teacher_label(int(group["teacher_id"])),
            "mode_label": MODE_LABELS.get(group["lesson_mode"], group["lesson_mode"]),
            "place_label": PLACE_LABELS.get(group["lesson_place"], group["lesson_place"]),
            "weekday_options": WEEKDAY_OPTIONS,
            "mode_options": MODE_OPTIONS,
            "place_options": PLACE_OPTIONS,
        },
        status_code=status_code,
    )


@router.get("")
async def groups_list(request: Request, teacher_id: int | None = Query(None), user=Depends(get_current_user)):
    return _groups_page_context(request, user, teacher_id_override=teacher_id)


@router.get("/{group_id:int}")
async def group_detail(
    request: Request,
    group_id: int,
    lesson_date: str | None = Query(None),
    output_id: int | None = Query(None),
    user=Depends(get_current_user),
):
    group = groups_service.get_group(group_id)
    if not _ensure_group_access(user, group):
        return _groups_page_context(request, user, status_code=403, error="Нет доступа к этой группе")
    return _render_group_detail(request, user, group, lesson_date=lesson_date, output_id=output_id)


@router.post("/create")
async def create_group(
    request: Request,
    teacher_id: int = Form(...),
    teacher_chat_id: int = Form(0),
    group_name: str = Form(...),
    course_name: str = Form(...),
    lesson_offset: int = Form(0),
    current_lesson_number: int = Form(1),
    first_lesson_date: str = Form(...),
    weekday: int = Form(...),
    lesson_time: str = Form(...),
    lesson_mode: str = Form(...),
    lesson_place: str = Form(...),
    user=Depends(get_current_user),
):
    if user["role"] == "teacher":
        linked_teacher_id = get_linked_teacher_id(user)
        if linked_teacher_id is None:
            return _groups_page_context(request, user, status_code=400, error="Учетная запись преподавателя не привязана к teacher_id")
        teacher_id = linked_teacher_id
        teacher_chat_id = linked_teacher_id

    groups_service.create_group(
        teacher_id=teacher_id,
        teacher_chat_id=teacher_chat_id,
        group_name=group_name,
        course_name=course_name,
        lesson_offset=lesson_offset,
        current_lesson_number=current_lesson_number,
        first_lesson_date=first_lesson_date,
        weekday=weekday,
        lesson_time=lesson_time,
        lesson_mode=lesson_mode,
        lesson_place=lesson_place,
    )
    return _groups_page_context(request, user, teacher_id_override=teacher_id, success="Группа добавлена в авто-выдачу")


@router.post("/{group_id:int}/update")
async def update_group(
    group_id: int,
    group_name: str = Form(...),
    course_name: str = Form(...),
    lesson_offset: int = Form(...),
    current_lesson_number: int = Form(...),
    first_lesson_date: str = Form(...),
    weekday: int = Form(...),
    lesson_time: str = Form(...),
    lesson_mode: str = Form(...),
    lesson_place: str = Form(...),
    is_active: int = Form(0),
    user=Depends(get_current_user),
):
    group = groups_service.get_group(group_id)
    if not _ensure_group_access(user, group):
        return RedirectResponse("/groups", status_code=303)
    for field_name, value in {
        "group_name": group_name,
        "course_name": course_name,
        "lesson_offset": lesson_offset,
        "current_lesson_number": current_lesson_number,
        "first_lesson_date": first_lesson_date,
        "weekday": weekday,
        "lesson_time": lesson_time,
        "lesson_mode": lesson_mode,
        "lesson_place": lesson_place,
        "is_active": is_active,
    }.items():
        groups_service.update_group_field(group_id, field_name, value)
    actions_service.refresh_auto_feedback_outputs_for_group(group_id)
    return RedirectResponse(f"/groups/{group_id}", status_code=303)


@router.post("/{group_id:int}/delete")
async def delete_group(group_id: int, user=Depends(get_current_user)):
    group = groups_service.get_group(group_id)
    if not _ensure_group_access(user, group):
        return RedirectResponse("/groups", status_code=303)
    groups_service.delete_group(group_id)
    return RedirectResponse("/groups", status_code=303)


@router.post("/{group_id:int}/run-auto-feedback")
async def run_auto_feedback(
    request: Request,
    group_id: int,
    lesson_date: str = Form(""),
    user=Depends(get_current_user),
    actor=Depends(get_current_actor),
):
    group = groups_service.get_group(group_id)
    if not _ensure_group_access(user, group):
        return RedirectResponse("/groups", status_code=303)
    target_lesson_date = lesson_date or groups_service.get_reference_lesson_date(group)
    lesson_number = groups_service.get_lesson_number_for_date(group, target_lesson_date)
    result = actions_service.ensure_auto_feedback_output_for_group_date(group_id, target_lesson_date, lesson_number)
    if result.ok:
        output = result.data["output"]
        result = ActionResult(ok=True, message="Авто-ОС подготовлена для выбранной недели", data={"output": output})
    group = groups_service.get_group(group_id)
    return _render_group_detail(
        request,
        user,
        group,
        lesson_date=target_lesson_date,
        output_id=result.data.get("output")[0] if result.ok and result.data.get("output") else None,
        run_result=result,
        status_code=200 if result.ok else 400,
    )
