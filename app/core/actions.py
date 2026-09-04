from app.core.analytics import AnalyticsService
from app.core.feedback import FeedbackService
from app.core.groups import GroupsService
from app.core.jobs import JobsService
from app.core.models import ActionResult, Actor


class ActionsService:
    def __init__(
        self,
        feedback_service: FeedbackService,
        groups_service: GroupsService,
        analytics_service: AnalyticsService,
        jobs_service: JobsService,
    ):
        self.feedback_service = feedback_service
        self.groups_service = groups_service
        self.analytics_service = analytics_service
        self.jobs_service = jobs_service

    def preview_feedback(
        self,
        actor: Actor,
        course_name: str,
        lesson_number: int,
        lesson_date: str,
        offset: int = 0,
        absent_students: list[str] | None = None,
        is_online_or_individual: bool = False,
        is_repetition: bool = False,
    ) -> ActionResult:
        result = self.feedback_service.build_feedback_preview(
            course_name=course_name,
            lesson_number=lesson_number,
            lesson_date=lesson_date,
            offset=offset,
            absent_students=absent_students,
            is_online_or_individual=is_online_or_individual,
            is_repetition=is_repetition,
        )
        if result.ok:
            self.analytics_service.log_action(
                actor,
                "WEB_PREVIEW_FEEDBACK",
                action_details=f"preview for {course_name}",
                course_name=course_name,
                lesson_number=lesson_number,
                lesson_date=lesson_date,
            )
        return result

    def run_auto_feedback_for_group(
        self,
        actor: Actor,
        group_id: int,
        lesson_date_str: str | None = None,
        manual_trigger: bool = True,
    ) -> ActionResult:
        group = self.groups_service.get_group(group_id)
        if not group:
            return ActionResult(ok=False, message="Группа не найдена")

        effective_lesson_date = lesson_date_str or self.groups_service.get_reference_lesson_date(group)
        result = self.feedback_service.build_auto_feedback_content(group, effective_lesson_date)

        if not result.ok:
            self.jobs_service.record_job_run("auto_feedback", "content_error", result.message, group_id, effective_lesson_date)
            self.jobs_service.log_auto_feedback_csv(action=f"{group['group_name']} | {group['course_name']} | content_error")
            if not manual_trigger:
                self.jobs_service.create_notification_for_teacher(
                    int(group["teacher_id"]),
                    "Ошибка авто-ОС",
                    f"Для группы «{group['group_name']}» не удалось сформировать авто-ОС: {result.message}",
                    "error",
                )
            return result

        self.jobs_service.save_auto_feedback_output(
            group_id=group_id,
            lesson_date=effective_lesson_date,
            lesson_name=result.data["lesson_name"],
            feedback_text=result.data["feedback_text"],
            image_path=result.data["image_path"] or "",
            status="success",
        )
        self.jobs_service.record_job_run("auto_feedback", "success", f"group={group['group_name']}", group_id, effective_lesson_date)
        self.jobs_service.log_auto_feedback_csv(
            action=f"{group['group_name']} | {group['course_name']} | урок {group['current_lesson_number']}"
        )

        if manual_trigger:
            self.analytics_service.log_action(
                actor,
                "WEB_RUN_AUTO_FEEDBACK",
                action_details=f"group_id={group_id}",
                course_name=group["course_name"],
                lesson_number=int(group["current_lesson_number"]),
                lesson_date=effective_lesson_date,
            )
        else:
            self.groups_service.mark_sent(group_id, effective_lesson_date, advance_lesson=True)
            self.jobs_service.create_notification_for_teacher(
                int(group["teacher_id"]),
                "Авто-ОС сформирована",
                f"Группа «{group['group_name']}», дата {effective_lesson_date}. Обратная связь успешно сформирована.",
                "success",
            )

        return ActionResult(
            ok=True,
            message="Авто-ОС сформирована",
            data={
                "group": group,
                "feedback_text": result.data["feedback_text"],
                "image_path": result.data["image_path"],
                "lesson_date": effective_lesson_date,
                "lesson_name": result.data["lesson_name"],
            },
        )

    def ensure_auto_feedback_output_for_group_date(self, group_id: int, lesson_date_str: str, lesson_number: int) -> ActionResult:
        existing_output = self.jobs_service.get_output_for_group_and_lesson_date(group_id, lesson_date_str)
        if existing_output:
            return ActionResult(ok=True, data={"output": existing_output, "existing": True})

        group = self.groups_service.get_group(group_id)
        if not group:
            return ActionResult(ok=False, message="Группа не найдена")

        result = self.feedback_service.build_auto_feedback_content(
            group,
            lesson_date_str,
            lesson_number_override=lesson_number,
        )
        if not result.ok:
            return result

        self.jobs_service.save_auto_feedback_output(
            group_id=group_id,
            lesson_date=lesson_date_str,
            lesson_name=result.data["lesson_name"],
            feedback_text=result.data["feedback_text"],
            image_path=result.data["image_path"] or "",
            status="success",
        )
        output = self.jobs_service.get_output_for_group_and_lesson_date(group_id, lesson_date_str)
        return ActionResult(ok=True, data={"output": output, "existing": False})

    def refresh_auto_feedback_outputs_for_group(self, group_id: int) -> ActionResult:
        group = self.groups_service.get_group(group_id)
        if not group:
            return ActionResult(ok=False, message="Группа не найдена")

        outputs = self.jobs_service.list_all_auto_feedback_outputs_for_group(group_id)
        updated_count = 0
        failed_count = 0

        for output in outputs:
            output_id = int(output[0])
            lesson_date = output[2]
            lesson_number = self.groups_service.get_lesson_number_for_date(group, lesson_date)
            result = self.feedback_service.build_auto_feedback_content(
                group,
                lesson_date,
                lesson_number_override=lesson_number,
            )

            if result.ok:
                self.jobs_service.update_auto_feedback_output(
                    output_id,
                    lesson_name=result.data["lesson_name"],
                    feedback_text=result.data["feedback_text"],
                    image_path=result.data["image_path"] or "",
                    status="success",
                )
                updated_count += 1
            else:
                self.jobs_service.update_auto_feedback_output(
                    output_id,
                    lesson_name="",
                    feedback_text=f"Не удалось обновить авто-ОС после изменения группы.\n\n{result.message}",
                    image_path="",
                    status="content_error",
                )
                failed_count += 1

        return ActionResult(
            ok=failed_count == 0,
            message="Существующие авто-ОС обновлены" if failed_count == 0 else "Часть авто-ОС не удалось обновить",
            data={
                "updated_outputs": updated_count,
                "failed_outputs": failed_count,
                "total_outputs": len(outputs),
            },
        )
