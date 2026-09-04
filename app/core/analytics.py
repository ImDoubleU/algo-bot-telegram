from services.activity_tracker_service import UserActivityTracker

from app.core.models import Actor


class AnalyticsService:
    def __init__(self, tracker: UserActivityTracker | None = None):
        self.tracker = tracker or UserActivityTracker()

    def log_action(self, actor: Actor, action_type: str, action_details: str = "", course_name: str = "", lesson_number: int | None = None, lesson_date: str = "") -> None:
        self.tracker.log_action_for_actor(
            actor_id=actor.actor_id,
            actor_username=actor.username,
            actor_display_name=actor.display_name,
            action_type=action_type,
            action_details=action_details,
            course_name=course_name,
            lesson_number=lesson_number,
            lesson_date=lesson_date,
        )
