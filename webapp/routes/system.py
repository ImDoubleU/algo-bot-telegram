from pathlib import Path

from fastapi import APIRouter, Depends, Request

from app.settings import settings
from webapp.deps import jobs_service, require_admin
from webapp.routes.helpers import templates


router = APIRouter(prefix="/system")


def _tail_file(path: Path, lines: int = 50) -> list[str]:
    if not path.exists():
        return []
    return path.read_text(encoding="utf-8", errors="ignore").splitlines()[-lines:]


@router.get("/logs")
async def system_logs(request: Request, user=Depends(require_admin)):
    app_log = _tail_file(settings.logs_path / "bot_activity.log")
    analytics_log = _tail_file(settings.user_analytics_csv_path)
    return templates.TemplateResponse(
        "system.html",
        {
            "request": request,
            "user": user,
            "app_log": app_log,
            "analytics_log": analytics_log,
            "jobs": jobs_service.get_recent_job_runs(),
        },
    )


@router.get("/jobs")
async def system_jobs(request: Request, user=Depends(require_admin)):
    return templates.TemplateResponse(
        "system.html",
        {"request": request, "user": user, "app_log": [], "analytics_log": [], "jobs": jobs_service.get_recent_job_runs()},
    )
