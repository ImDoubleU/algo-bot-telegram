from fastapi import APIRouter, Depends, Query, Request

from webapp.deps import require_admin, stats_service
from webapp.routes.helpers import templates


router = APIRouter(prefix="/stats")


@router.get("")
async def stats_page(request: Request, q: str = Query(""), date: str = Query(""), user=Depends(require_admin)):
    daily_stats = stats_service.get_daily_stats(date or None)
    users = stats_service.search_users(q) if q else []
    selected_user = stats_service.get_user_stats(user_id=users[0][0], date=date or None) if users else None
    return templates.TemplateResponse(
        "stats.html",
        {
            "request": request,
            "user": user,
            "daily_stats": daily_stats,
            "users": users,
            "selected_user": selected_user,
            "q": q,
            "date": date,
        },
    )
