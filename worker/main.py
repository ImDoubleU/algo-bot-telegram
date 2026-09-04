import asyncio
import logging
from datetime import datetime

from app.core.actions import ActionsService
from app.core.analytics import AnalyticsService
from app.core.courses import CoursesService
from app.core.feedback import FeedbackService
from app.core.groups import GroupsService
from app.core.jobs import JobsService
from app.core.models import Actor
from app.settings import settings


logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
logger = logging.getLogger(__name__)

courses_service = CoursesService()
feedback_service = FeedbackService(courses_service)
groups_service = GroupsService()
analytics_service = AnalyticsService()
jobs_service = JobsService()
actions_service = ActionsService(feedback_service, groups_service, analytics_service, jobs_service)
system_actor = Actor(actor_id=0, username="system", display_name="System Worker", role="admin")


async def worker_loop():
    logger.info("Worker started with interval=%ss", settings.worker_interval_sec)
    while True:
        now_dt = datetime.now()
        due_groups = groups_service.get_due_groups(now_dt)
        for group_row, lesson_date_str in due_groups:
            try:
                result = actions_service.run_auto_feedback_for_group(
                    actor=system_actor,
                    group_id=group_row["id"],
                    lesson_date_str=lesson_date_str,
                    manual_trigger=False,
                )
                if not result.ok:
                    jobs_service.record_job_run(
                        "auto_feedback",
                        "execution_error",
                        result.message,
                        group_row["id"],
                        lesson_date_str,
                    )
            except Exception as exc:
                logger.exception("Worker failed for group %s", group_row["id"])
                jobs_service.record_job_run(
                    "auto_feedback",
                    "execution_error",
                    str(exc),
                    group_row["id"],
                    lesson_date_str,
                )
        await asyncio.sleep(settings.worker_interval_sec)


def main():
    asyncio.run(worker_loop())


if __name__ == "__main__":
    main()
