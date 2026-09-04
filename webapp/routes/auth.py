from fastapi import APIRouter, Form, Request
from fastapi.responses import RedirectResponse

from webapp.deps import web_auth_service
from webapp.routes.helpers import templates


router = APIRouter()

ADMIN_TELEGRAM_TAG = "@imdoubleu"
ADMIN_TELEGRAM_URL = "https://t.me/imdoubleu"


def _login_context(request: Request, error: str = "", status_code: int = 200):
    return templates.TemplateResponse(
        "login.html",
        {
            "request": request,
            "error": error,
            "admin_telegram_tag": ADMIN_TELEGRAM_TAG,
            "admin_telegram_url": ADMIN_TELEGRAM_URL,
        },
        status_code=status_code,
    )


@router.get("/login")
async def login_page(request: Request):
    if request.session.get("user"):
        return RedirectResponse("/", status_code=303)
    return _login_context(request)


@router.post("/login")
async def login(request: Request, username: str = Form(...), password: str = Form(...)):
    user = web_auth_service.authenticate(username, password)
    if not user:
        return _login_context(request, error="Неверный логин или пароль", status_code=400)
    request.session["user"] = user
    return RedirectResponse("/", status_code=303)


@router.post("/logout")
async def logout(request: Request):
    request.session.clear()
    return RedirectResponse("/login", status_code=303)
