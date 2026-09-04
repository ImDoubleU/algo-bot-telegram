from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from starlette.middleware.trustedhost import TrustedHostMiddleware
from starlette.middleware.sessions import SessionMiddleware

from app.settings import settings
from webapp.routes import access, auth, courses, dashboard, feedback, groups, hub, system


app = FastAPI(title="Algo Bot Web")
if settings.allowed_hosts != ["*"]:
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=settings.allowed_hosts)
app.add_middleware(
    SessionMiddleware,
    secret_key=settings.secret_key,
    session_cookie=settings.session_cookie,
    same_site=settings.session_same_site,
    https_only=settings.session_https_only,
    max_age=settings.session_max_age,
)
app.mount("/static", StaticFiles(directory=str(Path(__file__).resolve().parent / "static")), name="static")


@app.get("/health")
async def health():
    return {"status": "ok", "environment": settings.environment}

app.include_router(auth.router)
app.include_router(dashboard.router)
app.include_router(courses.router)
app.include_router(feedback.router)
app.include_router(groups.router)
app.include_router(hub.router)
app.include_router(access.router)
app.include_router(system.router)
