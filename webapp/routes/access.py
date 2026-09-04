from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse

from app.settings import settings
from webapp.deps import require_admin, web_auth_service
from webapp.routes.helpers import templates


router = APIRouter(prefix="/access")


@router.get("")
async def access_page(request: Request, user=Depends(require_admin)):
    web_users = []
    for item in web_auth_service.list_users():
        web_users.append(
            {
                "id": item[0],
                "username": item[1],
                "display_name": item[2],
                "password_plain": item[3] or "",
                "can_delete": item[1] not in {settings.admin_username, user["username"]},
            }
        )
    return templates.TemplateResponse(
        "access.html",
        {
            "request": request,
            "user": user,
            "web_users": web_users,
            "message": request.query_params.get("message", ""),
            "error": request.query_params.get("error", ""),
        },
    )


@router.post("/web-users/create")
async def create_web_user(
    username: str = Form(...),
    password: str = Form(...),
    display_name: str = Form(...),
    role: str = Form("teacher"),
    linked_teacher_id: int = Form(0),
    user=Depends(require_admin),
):
    created = web_auth_service.create_user(username, password, display_name, role, linked_teacher_id or None)
    if not created:
        return RedirectResponse("/access?error=username_exists", status_code=303)
    return RedirectResponse(f"/access?message=created:{username}", status_code=303)


@router.post("/web-users/{username}/reset-password")
async def reset_web_user_password(
    username: str,
    password: str = Form(...),
    user=Depends(require_admin),
):
    web_auth_service.update_user_password(username, password)
    return RedirectResponse(f"/access?message=password_reset:{username}", status_code=303)


@router.post("/web-users/{username}/delete")
async def delete_web_user(
    username: str,
    user=Depends(require_admin),
):
    if username == user["username"]:
        return RedirectResponse("/access?error=cannot_delete_self", status_code=303)
    deleted = web_auth_service.delete_user(username)
    if not deleted:
        return RedirectResponse("/access?error=cannot_delete_system_admin", status_code=303)
    return RedirectResponse(f"/access?message=deleted:{username}", status_code=303)
