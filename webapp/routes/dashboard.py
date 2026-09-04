from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse

from webapp.deps import get_current_user, get_linked_teacher_id, groups_service, stats_service, jobs_service
from webapp.routes.helpers import templates
from webapp.deps import web_auth_service


router = APIRouter()


@router.get("/")
async def dashboard(request: Request, user=Depends(get_current_user)):
    teacher_id = get_linked_teacher_id(user)
    use_personal_scope = bool(teacher_id) and user["role"] in {"teacher", "admin"}
    groups = groups_service.list_groups(teacher_id) if use_personal_scope else groups_service.list_all_groups()
    notifications = jobs_service.list_notifications(int(user["id"]), unread_only=False, limit=8)
    teacher_name = ""
    if use_personal_scope and teacher_id:
        username, full_name = stats_service.tracker.get_latest_user_identity(teacher_id)
        teacher_name = full_name or username or f"teacher_id {teacher_id}"
    return templates.TemplateResponse(
        "dashboard.html",
        {
            "request": request,
            "user": user,
            "groups_count": len(groups),
            "notifications": notifications,
            "teacher_name": teacher_name,
            "message": request.query_params.get("message", ""),
            "error": request.query_params.get("error", ""),
        },
    )


@router.post("/account/password")
async def change_own_password(
    current_password: str = Form(...),
    new_password: str = Form(...),
    confirm_password: str = Form(...),
    user=Depends(get_current_user),
):
    if new_password != confirm_password:
        return RedirectResponse("/?error=password_mismatch", status_code=303)
    if not web_auth_service.authenticate(user["username"], current_password):
        return RedirectResponse("/?error=password_current_invalid", status_code=303)
    web_auth_service.update_user_password(user["username"], new_password)
    return RedirectResponse("/?message=password_changed", status_code=303)
