from fastapi import HTTPException, Request, status

from app.core.access import AccessService
from app.core.actions import ActionsService
from app.core.analytics import AnalyticsService
from app.core.courses import CoursesService
from app.core.feedback import FeedbackService
from app.core.groups import GroupsService
from app.core.hub import HubService
from app.core.jobs import JobsService
from app.core.models import Actor
from app.core.stats import StatsService
from app.core.web_auth import WebAuthService


courses_service = CoursesService()
feedback_service = FeedbackService(courses_service)
groups_service = GroupsService()
analytics_service = AnalyticsService()
jobs_service = JobsService()
actions_service = ActionsService(feedback_service, groups_service, analytics_service, jobs_service)
hub_service = HubService()
access_service = AccessService()
stats_service = StatsService()
web_auth_service = WebAuthService()


def get_current_user(request: Request) -> dict:
    user = request.session.get("user")
    if not user:
        raise HTTPException(status_code=status.HTTP_303_SEE_OTHER, headers={"Location": "/login"})
    return user


def get_current_actor(request: Request) -> Actor:
    user = get_current_user(request)
    return Actor(
        actor_id=int(user["id"]),
        username=user["username"],
        display_name=user["display_name"],
        role=user["role"],
    )


def require_admin(request: Request) -> dict:
    user = get_current_user(request)
    if user["role"] != "admin":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Admin only")
    return user


def get_linked_teacher_id(user: dict) -> int | None:
    linked = user.get("linked_teacher_id")
    return int(linked) if linked not in (None, "") else None
