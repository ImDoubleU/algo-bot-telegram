import os
os.makedirs('logs', exist_ok=True)
import json
import asyncio
import csv
import logging
import logging.handlers
import traceback
from datetime import datetime, timedelta
from telegram import LinkPreviewOptions, MessageEntity, Update, InlineKeyboardMarkup, InlineKeyboardButton
from telegram.ext import Application, CommandHandler, MessageHandler, ContextTypes, filters, CallbackQueryHandler
from backup_manager import BackupManager, monthly_maintenance
from config import BOT_CONFIG, BACKUP_CONFIG
from access_manager import access_manager
from services.activity_tracker_service import UserActivityTracker
from services.teacher_group_service import TeacherGroupManager, WEEKDAY_NAMES
from services.teacher_hub_service import TeacherHubManager
from core.callback_router import CallbackRouter
from core.feedback_text import MAX_BOT_LINK_LABEL, MAX_BOT_URL, build_astrocoins_block
from core.telegram_api import telegram_api_call
from core.ui_errors import build_error_keyboard, build_error_text
from core.validation import ValidationError, parse_date, parse_int, parse_time

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.handlers.TimedRotatingFileHandler(
            'logs/bot_activity.log',
            when='D',
            interval=1,
            backupCount=20000,
            encoding='utf-8'
        ),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)
logging.getLogger('httpx').setLevel(logging.WARNING)
logging.getLogger('httpcore').setLevel(logging.WARNING)

TOKEN = BOT_CONFIG['token']
AUTO_FEEDBACK_MAX_CONCURRENT = int(BOT_CONFIG.get('auto_feedback_max_concurrent', 5))
AUTO_FEEDBACK_SEND_SPACING_SEC = float(BOT_CONFIG.get('auto_feedback_send_spacing_sec', 0.05))

_auto_feedback_scheduler_lock = asyncio.Lock()
_auto_feedback_send_lock = asyncio.Lock()
_auto_feedback_last_send_ts = 0.0
_auto_feedback_csv_lock = asyncio.Lock()
AUTO_FEEDBACK_CSV_PATH = 'logs/auto_feedback_actions.csv'
AUTO_FEEDBACK_TEST_CLEANUP_TTL_HOURS = int(BOT_CONFIG.get('auto_feedback_test_cleanup_ttl_hours', 2))
AUTO_FEEDBACK_CSV_HEADERS = [
    "timestamp",
    "user_id",
    "user_tag",
    "user_name",
    "action",
]
FEEDBACK_LINK_PREVIEW_OPTIONS = LinkPreviewOptions(is_disabled=True)


def build_feedback_link_entities(text: str) -> list[MessageEntity]:
    label_start = text.find(MAX_BOT_LINK_LABEL)
    if label_start < 0:
        return []

    utf16_offset = len(text[:label_start].encode("utf-16-le")) // 2
    utf16_length = len(MAX_BOT_LINK_LABEL.encode("utf-16-le")) // 2
    return [MessageEntity(
        type=MessageEntity.TEXT_LINK,
        offset=utf16_offset,
        length=utf16_length,
        url=MAX_BOT_URL,
    )]

try:
    with open('data/courses.json', 'r', encoding='utf-8') as f:
        COURSES = json.load(f)
except (FileNotFoundError, json.JSONDecodeError) as e:
    logger.error(f"Ошибка загрузки courses.json: {e}")
    COURSES = {}

os.makedirs('data', exist_ok=True)
os.makedirs('images', exist_ok=True)

activity_tracker = UserActivityTracker()
teacher_group_manager = TeacherGroupManager()
hub_manager = TeacherHubManager()






def get_lessons_in_order(course_name):
    """Получение уроков в хронологическом порядке"""
    if course_name not in COURSES:
        return []
    lessons = list(COURSES[course_name].items())
    try:
        lessons.sort(key=lambda x: int(x[0].split()[-1]))
    except (ValueError, IndexError):
        lessons.sort(key=lambda x: x[0])
    return lessons


def get_time_based_greeting():
    """Возвращает приветствие в зависимости от времени суток"""
    current_hour = datetime.now().hour

    if 6 <= current_hour < 12:
        greeting = "☀️ Доброе утро, уважаемые родители!"
    elif 12 <= current_hour < 18:
        greeting = "👋 Добрый день, уважаемые родители!"
    else:
        greeting = "🌙 Добрый вечер, уважаемые родители!"
    return greeting


def format_feedback(lesson_name, lesson_number, lesson_date, feedback_data, offset=0, is_repetition=False):
    """Форматирование обратной связи с учетом смещения и режима повторения"""

    adjusted_lesson_number = lesson_number + offset
    formatted_date = lesson_date.strftime("%d.%m.%Y")

    greeting = get_time_based_greeting()

    if is_repetition:
        educational_text = "Сегодня мы с ребятами повторяли тему предыдущего занятия, чтобы укрепить знания по ней."
    else:
        educational_text = feedback_data.get('educational_results', '')

    feedback_text = f"""Обратная связь урок №{adjusted_lesson_number:02d} от {formatted_date}

{greeting}

{educational_text}

{build_astrocoins_block(adjusted_lesson_number, formatted_date)}

👍 На онлайн-платформе «Алгоритмика» предоставлен весь материал, пройденный на уроках и прогресс 📈 ребенка.

🔥 Удачной недели!"""

    return feedback_text


async def handle_absent_students(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Обрабатывает добавление отсутствующих учеников"""
    query = update.callback_query
    await query.answer()

    if query.data == "add_absent":
        await query.message.edit_text(
            "👥 Введите имена отсутствующих учеников через пробел:\n\n"
            "Пример: *Ваня Настя Коля Юля*",
            parse_mode='Markdown'
        )
        context.user_data['awaiting_absent_students'] = True


async def handle_absent_students_input(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Обрабатывает ввод имен отсутствующих учеников"""
    if context.user_data.get('awaiting_absent_students'):
        absent_students_text = update.message.text.strip()
        context.user_data['awaiting_absent_students'] = False

        if absent_students_text:
            absent_students = [
                name.strip() for name in absent_students_text.split() if name.strip()]
            context.user_data['absent_students'] = absent_students

            activity_tracker.log_action(
                update, "ДОБАВЛЕНИЕ_ОТСУТСТВУЮЩИХ",
                f"Добавлены отсутствующие: {', '.join(absent_students)}",
                course_name=context.user_data.get('selected_course', ''),
                lesson_number=context.user_data.get(
                    'current_lesson_index', 0) + 1
            )

            await show_lesson(update, context)
        else:
            await update.message.reply_text("❌ Не введены имена отсутствующих.")
            await show_lesson(update, context)


def format_feedback_with_absent_students(lesson_name, lesson_number, lesson_date, feedback_data, absent_students, offset=0, is_repetition=False):
    """Форматирование обратной связи с датой и отсутствующими учениками с учетом смещения"""
    adjusted_lesson_number = lesson_number + offset
    formatted_date = lesson_date.strftime("%d.%m.%Y")

    if is_repetition:
        educational_text = "Сегодня мы с ребятами повторяли тему предыдущего занятия, чтобы укрепить знания по ней."
    else:
        educational_text = feedback_data.get('educational_results', '')

    greeting = get_time_based_greeting()

    if absent_students:
        if len(absent_students) == 1:
            absent_text = f"{absent_students[0]}, ждем на отработке за 30 минут до начала следующего занятия."
        elif len(absent_students) == 2:
            absent_text = f"{absent_students[0]} и {absent_students[1]}, ждем на отработке за 30 минут до начала следующего занятия."
        else:
            names_except_last = ", ".join(absent_students[:-1])
            last_name = absent_students[-1]
            absent_text = f"{names_except_last} и {last_name}, ждем на отработке за 30 минут до начала следующего занятия."
    else:
        absent_text = ""

    feedback_text = f"""Обратная связь урок №{adjusted_lesson_number:02d} от {formatted_date}

{greeting}

{educational_text}

{absent_text}

{build_astrocoins_block(adjusted_lesson_number, formatted_date)}

👍 На онлайн-платформе «Алгоритмика» предоставлен весь материал, пройденный на уроках и прогресс 📈 ребенка.

🔥 Удачной недели!"""

    return feedback_text


def format_feedback_online_individual(lesson_name, lesson_number, lesson_date, feedback_data, absent_students=None, offset=0, is_repetition=False):
    """Форматирование обратной связи для онлайн/индивидуальных занятий с учетом отсутствующих, оффсета и режима повторения"""

    adjusted_lesson_number = lesson_number + offset
    formatted_date = lesson_date.strftime("%d.%m.%Y")

    if is_repetition:
        educational_text = "Сегодня мы с ребятами повторяли тему предыдущего занятия, чтобы укрепить знания по ней."
    else:
        educational_text = feedback_data.get('educational_results', '')

    greeting = get_time_based_greeting()

    absent_text = ""
    if absent_students:
        if len(absent_students) == 1:
            absent_text = f"{absent_students[0]}, ждем на отработке за 30 минут до начала следующего занятия."
        elif len(absent_students) == 2:
            absent_text = f"{absent_students[0]} и {absent_students[1]}, ждем на отработке за 30 минут до начала следующего занятия."
        else:
            names_except_last = ", ".join(absent_students[:-1])
            last_name = absent_students[-1]
            absent_text = f"{names_except_last} и {last_name}, ждем на отработке за 30 минут до начала следующего занятия."

        absent_text = f"\n\n{absent_text}"

    feedback_text = f"""Обратная связь урок №{adjusted_lesson_number:02d} от {formatted_date}

{greeting}

{educational_text}{absent_text}

{build_astrocoins_block(adjusted_lesson_number, formatted_date)}

👍 На онлайн-платформе «Алгоритмика» предоставлен весь материал, пройденный на уроках и прогресс 📈 ребенка.

🔥 Удачной недели!"""

    return feedback_text


def build_weekday_keyboard(selected_weekday=None):
    keyboard = []
    row = []
    for weekday in range(7):
        title = WEEKDAY_NAMES[weekday]
        if selected_weekday == weekday:
            title = f"• {title} •"
        row.append(InlineKeyboardButton(title, callback_data=f"af_weekday_{weekday}"))
        if len(row) == 2:
            keyboard.append(row)
            row = []
    if row:
        keyboard.append(row)
    return InlineKeyboardMarkup(keyboard)


def clear_auto_feedback_wizard(context: ContextTypes.DEFAULT_TYPE):
    context.user_data.pop('af_wizard', None)


def build_course_picker_keyboard(page=0, page_size=8):
    course_names = list(COURSES.keys())
    total = len(course_names)
    total_pages = max(1, (total + page_size - 1) // page_size)
    page = max(0, min(page, total_pages - 1))

    start = page * page_size
    end = min(start + page_size, total)

    keyboard = []
    for idx in range(start, end):
        keyboard.append([InlineKeyboardButton(
            course_names[idx], callback_data=f"af_course_pick_{idx}"
        )])

    nav_row = []
    if page > 0:
        nav_row.append(InlineKeyboardButton("◀️", callback_data=f"af_course_page_{page - 1}"))
    nav_row.append(InlineKeyboardButton(f"{page + 1}/{total_pages}", callback_data="af_course_page_noop"))
    if page < total_pages - 1:
        nav_row.append(InlineKeyboardButton("▶️", callback_data=f"af_course_page_{page + 1}"))
    keyboard.append(nav_row)
    keyboard.append([InlineKeyboardButton("◀️ Отмена", callback_data="auto_feedback_menu")])

    return InlineKeyboardMarkup(keyboard)


async def show_auto_feedback_menu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.effective_user.id
    groups = teacher_group_manager.list_groups_for_teacher(user_id)

    keyboard = []
    for group in groups:
        status = "🟢" if group["is_active"] else "🔴"
        keyboard.append([InlineKeyboardButton(
            f"{status} {group['group_name']}", callback_data=f"af_group_{group['id']}"
        )])

    keyboard.append([InlineKeyboardButton(
        "➕ Добавить группу", callback_data="af_add_group_start"
    )])
    keyboard.append([InlineKeyboardButton(
        "◀️ Назад к курсам", callback_data="mode_feedback"
    )])

    text = (
        "🗓️ Автоматическая ОС\n\n"
        "Здесь можно настроить группы и автоматическую еженедельную отправку ОС.\n"
        "После начала занятия бот отправит ОС автоматически:\n"
        "• Индивидуальные - через 1 час после начала занятия\n"
        "• Групповые - через 1,5 часа после начала занятия"
    )

    if update.callback_query:
        await update.callback_query.message.edit_text(text, reply_markup=InlineKeyboardMarkup(keyboard))
    else:
        await update.message.reply_text(text, reply_markup=InlineKeyboardMarkup(keyboard))


def format_group_card(group):
    active_text = "Активна" if group["is_active"] else "Остановлена"
    weekday_text = WEEKDAY_NAMES.get(group["weekday"], str(group["weekday"]))
    mode_text = "Индивидуально" if group["lesson_mode"] == "individual" else "Группа"
    place_text = "Онлайн" if group["lesson_place"] == "online" else "Очно"
    return (
        f"👥 Группа: {group['group_name']}\n"
        f"📚 Курс: {group['course_name']}\n"
        f"🔢 Следующий урок для ОС: {group['current_lesson_number']}\n"
        f"⚙️ Смещение: {group['lesson_offset']}\n"
        f"📅 Первая дата: {group['first_lesson_date']}\n"
        f"🗓️ День недели: {weekday_text}\n"
        f"🕒 Время: {group['lesson_time']}\n"
        f"🎯 Формат: {mode_text}, {place_text}\n"
        f"📤 Последняя авто-отправка: {group['last_sent_for_date'] or 'еще не было'}\n"
        f"🔁 Статус: {active_text}"
    )


async def show_group_details(update: Update, context: ContextTypes.DEFAULT_TYPE, group_id: int) -> None:
    group = teacher_group_manager.get_group(group_id)
    if not group or group["teacher_id"] != update.effective_user.id:
        if update.callback_query:
            await update.callback_query.message.edit_text("❌ Группа не найдена.")
        return

    keyboard = [
        [InlineKeyboardButton("📤 Отправить тест сейчас", callback_data=f"af_send_now_{group_id}")],
        [InlineKeyboardButton("➕ Следующий урок", callback_data=f"af_next_lesson_{group_id}")],
        [InlineKeyboardButton("✏️ Указать последний пройденный", callback_data=f"af_set_lesson_{group_id}")],
        [InlineKeyboardButton("⚙️ Установить смещение", callback_data=f"af_set_offset_{group_id}")],
        [InlineKeyboardButton(
            "⏸ Остановить" if group["is_active"] else "▶️ Активировать",
            callback_data=f"af_toggle_{group_id}"
        )],
        [InlineKeyboardButton("🗑 Удалить группу", callback_data=f"af_delete_{group_id}")],
        [InlineKeyboardButton("◀️ К списку групп", callback_data="auto_feedback_menu")]
    ]

    await update.callback_query.message.edit_text(
        format_group_card(group),
        reply_markup=InlineKeyboardMarkup(keyboard)
    )


def build_auto_feedback_content(group_row, lesson_date_str):
    course_name = group_row["course_name"]
    lessons = get_lessons_in_order(course_name)
    lesson_number = int(group_row["current_lesson_number"])
    lesson_offset = int(group_row["lesson_offset"])

    if lesson_number < 1 or lesson_number > len(lessons):
        return None, None, f"номер урока {lesson_number} вне диапазона 1..{len(lessons)}"

    lesson_name, feedback_data = lessons[lesson_number - 1]
    lesson_date = datetime.strptime(lesson_date_str, "%Y-%m-%d")

    if group_row["lesson_mode"] == "individual" or group_row["lesson_place"] == "online":
        feedback_text = format_feedback_online_individual(
            lesson_name, lesson_number, lesson_date, feedback_data, None, offset=lesson_offset
        )
    else:
        feedback_text = format_feedback(
            lesson_name, lesson_number, lesson_date, feedback_data, offset=lesson_offset
        )

    image_path = feedback_data.get('image')
    return feedback_text, image_path, None


def _make_unique_archive_path(base_path):
    if not os.path.exists(base_path):
        return base_path
    root, ext = os.path.splitext(base_path)
    idx = 1
    while True:
        candidate = f"{root}_{idx}{ext}"
        if not os.path.exists(candidate):
            return candidate
        idx += 1


def _get_user_full_name(user):
    if not user:
        return ""
    first_name = (user.first_name or "").strip()
    last_name = (user.last_name or "").strip()
    return " ".join(part for part in [first_name, last_name] if part)


def _rotate_auto_feedback_csv_if_needed():
    if not os.path.exists(AUTO_FEEDBACK_CSV_PATH):
        return

    now_dt = datetime.now()
    file_mtime = datetime.fromtimestamp(os.path.getmtime(AUTO_FEEDBACK_CSV_PATH))
    if (file_mtime.year, file_mtime.month) == (now_dt.year, now_dt.month):
        return

    archive_dir = os.path.join('logs', 'archive')
    os.makedirs(archive_dir, exist_ok=True)
    archive_name = f"auto_feedback_actions_{file_mtime.year}-{file_mtime.month:02d}.csv"
    archive_path = _make_unique_archive_path(os.path.join(archive_dir, archive_name))
    os.replace(AUTO_FEEDBACK_CSV_PATH, archive_path)


def _rewrite_auto_feedback_csv(rows):
    with open(AUTO_FEEDBACK_CSV_PATH, 'w', encoding='utf-8', newline='') as csv_file:
        writer = csv.writer(csv_file)
        for row in rows:
            writer.writerow(row)


def _format_auto_feedback_user_tag(username):
    username = (username or "").strip()
    if not username:
        return ""
    return username if username.startswith("@") else f"@{username}"


def _get_group_row_value(group_row, key):
    if not group_row:
        return ""
    if isinstance(group_row, dict):
        return group_row.get(key, "")
    try:
        return group_row[key]
    except (TypeError, KeyError, IndexError):
        return ""


def _resolve_auto_feedback_user_identity(user_id):
    if user_id in ("", None):
        return "", ""
    try:
        resolved_user_id = int(user_id)
    except (TypeError, ValueError):
        return "", ""
    username, full_name = activity_tracker.get_latest_user_identity(resolved_user_id)
    return _format_auto_feedback_user_tag(username), full_name


def _format_auto_feedback_action(action, status="", group_row=None):
    if action == "auto_feedback_send" and group_row:
        group_name = str(_get_group_row_value(group_row, "group_name") or "").strip()
        course_name = str(_get_group_row_value(group_row, "course_name") or "").strip()
        lesson_number = _get_group_row_value(group_row, "current_lesson_number")
        lesson_label = ""
        if lesson_number not in ("", None):
            lesson_label = f"урок {lesson_number}"
        action_parts = [part for part in [group_name, course_name, lesson_label] if part]
        if action_parts:
            return " | ".join(action_parts)

    action_value = (action or "").replace("auto_feedback_", "").replace("_", " ").strip()
    action_value = action_value or "auto feedback action"
    status_value = (status or "").strip()
    if status_value and status_value.lower() != "ok":
        return f"{action_value}: {status_value}"
    return action_value


def _ensure_auto_feedback_csv_ready():
    _rotate_auto_feedback_csv_if_needed()

    if not os.path.exists(AUTO_FEEDBACK_CSV_PATH):
        _rewrite_auto_feedback_csv([AUTO_FEEDBACK_CSV_HEADERS])
        return

    with open(AUTO_FEEDBACK_CSV_PATH, 'r', encoding='utf-8', newline='') as csv_file:
        rows = list(csv.reader(csv_file))

    if not rows:
        _rewrite_auto_feedback_csv([AUTO_FEEDBACK_CSV_HEADERS])
        return

    header = rows[0]
    if header == AUTO_FEEDBACK_CSV_HEADERS:
        return

    legacy_backup = _make_unique_archive_path(
        os.path.join('logs', 'archive', f"auto_feedback_actions_legacy_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv")
    )
    os.makedirs(os.path.dirname(legacy_backup), exist_ok=True)
    os.replace(AUTO_FEEDBACK_CSV_PATH, legacy_backup)
    _rewrite_auto_feedback_csv([AUTO_FEEDBACK_CSV_HEADERS])


async def log_auto_feedback_csv(
    action,
    group_row=None,
    lesson_date_str="",
    manual_trigger=False,
    status="ok",
    details="",
    triggered_by_user_id=None,
    source="",
    actor_role="",
    actor_chat_id="",
    triggered_by_username="",
    triggered_by_name="",
):
    """
    Write auto-feedback actions to compact CSV log.
    CSV is rotated monthly.
    """
    async with _auto_feedback_csv_lock:
        _ensure_auto_feedback_csv_ready()
        with open(AUTO_FEEDBACK_CSV_PATH, 'a', encoding='utf-8', newline='') as csv_file:
            writer = csv.writer(csv_file)
            if action == "auto_feedback_send" and group_row:
                user_id = _get_group_row_value(group_row, "teacher_id")
                user_tag = ""
                user_name = ""
            else:
                user_id = triggered_by_user_id or _get_group_row_value(group_row, "teacher_id")
                user_tag = _format_auto_feedback_user_tag(triggered_by_username)
                user_name = (triggered_by_name or "").strip()

            if user_id not in ("", None):
                resolved_tag, resolved_name = _resolve_auto_feedback_user_identity(user_id)
                if not user_tag:
                    user_tag = resolved_tag
                if not user_name:
                    user_name = resolved_name

            action_label = _format_auto_feedback_action(action, status, group_row=group_row)
            writer.writerow([
                datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                user_id,
                user_tag,
                user_name,
                action_label,
            ])


def get_valid_test_cleanup_data(context: ContextTypes.DEFAULT_TYPE, group_id: int):
    cleanup_store = context.user_data.setdefault("af_test_cleanup", {})
    cleanup_data = cleanup_store.get(group_id)
    if not cleanup_data:
        return None

    expires_at_raw = cleanup_data.get("expires_at", "")
    if expires_at_raw:
        try:
            expires_at = datetime.fromisoformat(expires_at_raw)
            if datetime.now() > expires_at:
                cleanup_store.pop(group_id, None)
                return None
        except ValueError:
            cleanup_store.pop(group_id, None)
            return None

    return cleanup_data


async def throttle_telegram_send():
    """Global lightweight throttling to smooth burst sends under load."""
    global _auto_feedback_last_send_ts
    if AUTO_FEEDBACK_SEND_SPACING_SEC <= 0:
        return
    loop = asyncio.get_running_loop()
    async with _auto_feedback_send_lock:
        now = loop.time()
        delta = now - _auto_feedback_last_send_ts
        wait_for = AUTO_FEEDBACK_SEND_SPACING_SEC - delta
        if wait_for > 0:
            await asyncio.sleep(wait_for)
        _auto_feedback_last_send_ts = loop.time()


async def send_auto_feedback_for_group(
    application: Application,
    group_row,
    lesson_date_str,
    manual_trigger=False,
    triggered_by_user_id=None,
    return_message_ids=False,
    source="",
    actor_role="",
    actor_chat_id="",
    triggered_by_username="",
    triggered_by_name="",
):
    async def _safe_send(send_coro_factory, operation_name):
        await throttle_telegram_send()
        return await telegram_api_call(
            call_factory=send_coro_factory,
            operation_name=operation_name,
            retries=2,
            timeout_sec=20.0,
        )

    sent_message_ids = []
    resolved_source = source or ("scheduler" if not manual_trigger else "manual")
    resolved_actor_role = actor_role or ("system" if not manual_trigger else "teacher")
    resolved_actor_chat_id = actor_chat_id or ""
    feedback_text, image_path, error_text = build_auto_feedback_content(group_row, lesson_date_str)

    if error_text:
        sent = await _safe_send(lambda: application.bot.send_message(
            chat_id=group_row["teacher_chat_id"],
            text=f"❌ Не удалось сформировать авто-ОС для группы «{group_row['group_name']}»: {error_text}"
        ), f"auto_feedback_error_send_group_{group_row['id']}")
        await log_auto_feedback_csv(
            action="auto_feedback_send",
            group_row=group_row,
            lesson_date_str=lesson_date_str,
            manual_trigger=manual_trigger,
            status="content_error" if sent else "transport_error",
            details=error_text,
            triggered_by_user_id=triggered_by_user_id,
            source=resolved_source,
            actor_role=resolved_actor_role,
            actor_chat_id=resolved_actor_chat_id,
            triggered_by_username=triggered_by_username,
            triggered_by_name=triggered_by_name,
        )
        if not sent:
            return (False, sent_message_ids) if return_message_ids else False
        if not manual_trigger:
            teacher_group_manager.mark_sent(group_row["id"], lesson_date_str, advance_lesson=False)
        return (True, sent_message_ids) if return_message_ids else True

    header = (
        f"🤖 Авто-ОС\n"
        f"👥 {group_row['group_name']}\n"
        f"📚 {group_row['course_name']}\n"
        f"📅 Дата занятия: {datetime.strptime(lesson_date_str, '%Y-%m-%d').strftime('%d.%m.%Y')}\n\n"
    )

    info_caption = header if len(header) <= 1024 else f"🤖 Авто-ОС\n👥 {group_row['group_name']}"

    if image_path and os.path.exists(image_path):
        async def _send_photo_with_info_caption():
            with open(image_path, 'rb') as image_file:
                return await application.bot.send_photo(
                    chat_id=group_row["teacher_chat_id"],
                    photo=image_file,
                    caption=info_caption
                )
        sent = await _safe_send(_send_photo_with_info_caption, f"auto_feedback_info_photo_group_{group_row['id']}")
        if not sent:
            await log_auto_feedback_csv(
                action="auto_feedback_send",
                group_row=group_row,
                lesson_date_str=lesson_date_str,
                manual_trigger=manual_trigger,
                status="transport_error",
                details="failed_to_send_info_photo",
                triggered_by_user_id=triggered_by_user_id,
                source=resolved_source,
                actor_role=resolved_actor_role,
                actor_chat_id=resolved_actor_chat_id,
                triggered_by_username=triggered_by_username,
                triggered_by_name=triggered_by_name,
            )
            return (False, sent_message_ids) if return_message_ids else False
        sent_message_ids.append(sent.message_id)
    else:
        sent = await _safe_send(lambda: application.bot.send_message(
            chat_id=group_row["teacher_chat_id"],
            text=header
        ), f"auto_feedback_info_text_group_{group_row['id']}")
        if not sent:
            await log_auto_feedback_csv(
                action="auto_feedback_send",
                group_row=group_row,
                lesson_date_str=lesson_date_str,
                manual_trigger=manual_trigger,
                status="transport_error",
                details="failed_to_send_info_message",
                triggered_by_user_id=triggered_by_user_id,
                source=resolved_source,
                actor_role=resolved_actor_role,
                actor_chat_id=resolved_actor_chat_id,
                triggered_by_username=triggered_by_username,
                triggered_by_name=triggered_by_name,
            )
            return (False, sent_message_ids) if return_message_ids else False
        sent_message_ids.append(sent.message_id)
    sent = await _safe_send(lambda: application.bot.send_message(
        chat_id=group_row["teacher_chat_id"],
        text=feedback_text,
        entities=build_feedback_link_entities(feedback_text),
        link_preview_options=FEEDBACK_LINK_PREVIEW_OPTIONS,
    ), f"auto_feedback_payload_group_{group_row['id']}")
    if not sent:
        await log_auto_feedback_csv(
            action="auto_feedback_send",
            group_row=group_row,
            lesson_date_str=lesson_date_str,
            manual_trigger=manual_trigger,
            status="transport_error",
            details="failed_to_send_feedback_text",
            triggered_by_user_id=triggered_by_user_id,
            source=resolved_source,
            actor_role=resolved_actor_role,
            actor_chat_id=resolved_actor_chat_id,
            triggered_by_username=triggered_by_username,
            triggered_by_name=triggered_by_name,
        )
        return (False, sent_message_ids) if return_message_ids else False
    sent_message_ids.append(sent.message_id)

    await log_auto_feedback_csv(
        action="auto_feedback_send",
        group_row=group_row,
        lesson_date_str=lesson_date_str,
        manual_trigger=manual_trigger,
        status="sent",
        details="",
        triggered_by_user_id=triggered_by_user_id,
        source=resolved_source,
        actor_role=resolved_actor_role,
        actor_chat_id=resolved_actor_chat_id,
        triggered_by_username=triggered_by_username,
        triggered_by_name=triggered_by_name,
    )

    if not manual_trigger:
        teacher_group_manager.mark_sent(group_row["id"], lesson_date_str, advance_lesson=True)
    return (True, sent_message_ids) if return_message_ids else True


async def auto_feedback_scheduler(context: ContextTypes.DEFAULT_TYPE):
    if _auto_feedback_scheduler_lock.locked():
        logger.warning("Auto-feedback scheduler is still running from previous cycle, skipping this tick.")
        return

    async with _auto_feedback_scheduler_lock:
        now_dt = datetime.now()
        due_groups = teacher_group_manager.get_due_groups(now_dt)
        if not due_groups:
            return

        due_groups.sort(key=lambda item: (item[0]["lesson_time"], item[0]["id"]))
        send_limit = max(1, AUTO_FEEDBACK_MAX_CONCURRENT)
        semaphore = asyncio.Semaphore(send_limit)

        async def _process_group(group_row, lesson_date_str):
            async with semaphore:
                try:
                    success = await send_auto_feedback_for_group(
                        context.application,
                        group_row,
                        lesson_date_str,
                        manual_trigger=False,
                        source="scheduler",
                        actor_role="system",
                    )
                    if not success:
                        logger.warning(
                            f"Auto-send skipped for group {group_row['id']} due to transport errors; will retry next cycle."
                        )
                except Exception as e:
                    logger.error(f"Ошибка авто-отправки ОС для группы {group_row['id']}: {e}")
                    logger.error(traceback.format_exc())

        tasks = [
            _process_group(group_row, lesson_date_str)
            for group_row, lesson_date_str in due_groups
        ]
        await asyncio.gather(*tasks)


async def start_add_group_wizard(update: Update, context: ContextTypes.DEFAULT_TYPE):
    clear_auto_feedback_wizard(context)
    context.user_data['af_wizard'] = {
        "step": "group_name",
        "data": {}
    }
    await update.callback_query.message.edit_text(
        "Введите название группы:",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("◀️ Отмена", callback_data="auto_feedback_menu")]
        ])
    )


async def handle_auto_feedback_text_input(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    wizard = context.user_data.get('af_wizard')
    if not wizard:
        return False

    step = wizard.get("step")
    data = wizard.get("data", {})
    user_text = update.message.text.strip()

    if step == "group_name":
        data["group_name"] = user_text
        wizard["step"] = "course_pick"
        wizard["data"] = data
        await update.message.reply_text(
            "Выберите курс из встроенной клавиатуры:",
            reply_markup=build_course_picker_keyboard(page=0)
        )
        return True

    if step == "course_pick":
        await update.message.reply_text(
            "Курс нужно выбрать кнопками в сообщении выше."
        )
        return True

    if step == "course_name":
        if user_text not in COURSES:
            await update.message.reply_text("❌ Курс не найден. Введите точное название курса.")
            return True
        data["course_name"] = user_text
        wizard["step"] = "lesson_offset"
        wizard["data"] = data
        await update.message.reply_text("Введите смещение номера урока (например: 0, 1, -1):")
        return True

    if step == "lesson_offset":
        try:
            data["lesson_offset"] = parse_int(user_text, "смещение")
        except ValidationError as e:
            await update.message.reply_text(f"❌ {e}")
            return True
        wizard["step"] = "current_lesson_number"
        wizard["data"] = data
        await update.message.reply_text(
            "Введите номер последнего прошедшего занятия "
            "(например: 0 если занятий еще не было, 5 если последним был урок №5):"
        )
        return True

    if step == "current_lesson_number":
        try:
            last_completed_lesson_number = parse_int(user_text, "номер последнего прошедшего занятия")
        except ValidationError as e:
            await update.message.reply_text(f"❌ {e}")
            return True

        total_lessons = len(get_lessons_in_order(data["course_name"]))
        max_last_completed = max(0, total_lessons - 1)
        if last_completed_lesson_number < 0 or last_completed_lesson_number > max_last_completed:
            await update.message.reply_text(
                f"❌ Номер последнего прошедшего занятия должен быть в диапазоне 0..{max_last_completed}."
            )
            return True

        # In storage we keep the next lesson number for auto-feedback content generation.
        data["current_lesson_number"] = last_completed_lesson_number + 1
        wizard["step"] = "first_lesson_date"
        wizard["data"] = data
        await update.message.reply_text("Введите дату первого занятия в формате ДД.ММ.ГГГГ:")
        return True

    if step == "first_lesson_date":
        try:
            dt = parse_date(user_text, "%d.%m.%Y", "дата первого занятия")
        except ValidationError:
            await update.message.reply_text("❌ Неверный формат даты. Используйте ДД.ММ.ГГГГ.")
            return True

        data["first_lesson_date"] = dt.strftime("%Y-%m-%d")
        wizard["step"] = "weekday"
        wizard["data"] = data

        await update.message.reply_text(
            "Выберите день недели для регулярного занятия:",
            reply_markup=build_weekday_keyboard()
        )
        return True

    if step == "lesson_time":
        try:
            parse_time(user_text, "время занятия")
        except ValidationError:
            await update.message.reply_text("❌ Неверный формат времени. Используйте ЧЧ:ММ (например 18:30).")
            return True

        data["lesson_time"] = user_text
        wizard["step"] = "lesson_mode"
        wizard["data"] = data

        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("Группа", callback_data="af_mode_group")],
            [InlineKeyboardButton("Индивидуально", callback_data="af_mode_individual")]
        ])
        await update.message.reply_text("Выберите формат занятия:", reply_markup=keyboard)
        return True

    if step == "set_lesson_manual":
        try:
            last_completed_lesson_number = parse_int(user_text, "номер последнего прошедшего занятия")
        except ValidationError as e:
            await update.message.reply_text(f"❌ {e}")
            return True
        group_id = data.get("group_id")
        group = teacher_group_manager.get_group(group_id)
        if not group:
            clear_auto_feedback_wizard(context)
            await update.message.reply_text("❌ Группа не найдена.")
            return True
        total_lessons = len(get_lessons_in_order(group["course_name"]))
        max_last_completed = max(0, total_lessons - 1)
        if last_completed_lesson_number < 0 or last_completed_lesson_number > max_last_completed:
            await update.message.reply_text(
                f"❌ Номер последнего прошедшего занятия должен быть в диапазоне 0..{max_last_completed}."
            )
            return True
        old_lesson_number = int(group["current_lesson_number"])
        new_lesson_number = last_completed_lesson_number + 1
        teacher_group_manager.update_group_field(group_id, "current_lesson_number", new_lesson_number)
        updated_group = teacher_group_manager.get_group(group_id)
        await log_auto_feedback_csv(
            action="auto_feedback_group_updated",
            group_row=updated_group,
            lesson_date_str="",
            manual_trigger=True,
            status="lesson_number_set",
            details=(
                f"field=current_lesson_number; old={old_lesson_number}; new={new_lesson_number}; "
                f"last_completed={last_completed_lesson_number}"
            ),
            triggered_by_user_id=update.effective_user.id,
            triggered_by_username=update.effective_user.username or "",
            triggered_by_name=_get_user_full_name(update.effective_user),
            source="text_input",
            actor_role="teacher",
            actor_chat_id=update.effective_chat.id,
        )
        clear_auto_feedback_wizard(context)
        await update.message.reply_text(
            "✅ Последний прошедший урок обновлен.",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("◀️ Вернуться к группе", callback_data=f"af_group_{group_id}")]])
        )
        return True

    if step == "set_offset_manual":
        try:
            offset_value = parse_int(user_text, "смещение")
        except ValidationError as e:
            await update.message.reply_text(f"❌ {e}")
            return True
        group_id = data.get("group_id")
        group = teacher_group_manager.get_group(group_id)
        if not group:
            clear_auto_feedback_wizard(context)
            await update.message.reply_text("❌ Группа не найдена.")
            return True
        old_offset = int(group["lesson_offset"])
        teacher_group_manager.update_group_field(group_id, "lesson_offset", offset_value)
        updated_group = teacher_group_manager.get_group(group_id)
        await log_auto_feedback_csv(
            action="auto_feedback_group_updated",
            group_row=updated_group,
            lesson_date_str="",
            manual_trigger=True,
            status="lesson_offset_set",
            details=f"field=lesson_offset; old={old_offset}; new={offset_value}",
            triggered_by_user_id=update.effective_user.id,
            triggered_by_username=update.effective_user.username or "",
            triggered_by_name=_get_user_full_name(update.effective_user),
            source="text_input",
            actor_role="teacher",
            actor_chat_id=update.effective_chat.id,
        )
        clear_auto_feedback_wizard(context)
        await update.message.reply_text("✅ Смещение обновлено.", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("◀️ Вернуться к группе", callback_data=f"af_group_{group_id}")]]))
        return True

    return False


async def finalize_add_group_wizard(update: Update, context: ContextTypes.DEFAULT_TYPE):
    wizard = context.user_data.get('af_wizard', {})
    data = wizard.get("data", {})

    first_date = datetime.strptime(data["first_lesson_date"], "%Y-%m-%d")
    if first_date.weekday() != data["weekday"]:
        clear_auto_feedback_wizard(context)
        await update.callback_query.message.edit_text(
            "❌ День недели не совпадает с выбранной датой первого занятия.\n"
            "Заполните группу заново и укажите корректную дату/день."
        )
        return

    group_id = teacher_group_manager.add_group(
        teacher_id=update.effective_user.id,
        teacher_chat_id=update.effective_chat.id,
        group_name=data["group_name"],
        course_name=data["course_name"],
        lesson_offset=data["lesson_offset"],
        current_lesson_number=data["current_lesson_number"],
        first_lesson_date=data["first_lesson_date"],
        weekday=data["weekday"],
        lesson_time=data["lesson_time"],
        lesson_mode=data["lesson_mode"],
        lesson_place=data["lesson_place"]
    )

    clear_auto_feedback_wizard(context)
    group = teacher_group_manager.get_group(group_id)
    await log_auto_feedback_csv(
        action="auto_feedback_group_added",
        group_row=group,
        lesson_date_str=data["first_lesson_date"],
        manual_trigger=True,
        status="created",
        details=(
            f"group_name={data['group_name']}; course_name={data['course_name']}; "
            f"lesson_offset={data['lesson_offset']}; current_lesson_number={data['current_lesson_number']}; "
            f"first_lesson_date={data['first_lesson_date']}; weekday={data['weekday']}; "
            f"lesson_time={data['lesson_time']}; lesson_mode={data['lesson_mode']}; "
            f"lesson_place={data['lesson_place']}"
        ),
        triggered_by_user_id=update.effective_user.id,
        triggered_by_username=update.effective_user.username or "",
        triggered_by_name=_get_user_full_name(update.effective_user),
        source="ui_button",
        actor_role="teacher",
        actor_chat_id=update.effective_chat.id,
    )
    await update.callback_query.message.edit_text(
        "✅ Группа добавлена.\n\n" + format_group_card(group),
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("👁 Открыть группу", callback_data=f"af_group_{group_id}")],
            [InlineKeyboardButton("◀️ К списку групп", callback_data="auto_feedback_menu")]
        ])
    )


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /start and redeem one-time access invitations."""
    activity_tracker.log_action(
        update, "СТАРТ", "Пользователь начал работу с ботом")

    context.user_data.clear()

    user = update.effective_user
    if not user_has_bot_access(user.id):
        invite_arg = context.args[0] if context.args else ""
        if invite_arg.startswith("access_"):
            invite_token = invite_arg.removeprefix("access_")
            granted = access_manager.redeem_invite(
                token=invite_token,
                user_id=user.id,
                username=user.username or "",
                first_name=user.first_name or "",
                last_name=user.last_name or "",
            )
            if granted:
                activity_tracker.log_action(
                    update,
                    "ACCESS_INVITE_REDEEMED",
                    "Доступ получен по одноразовой ссылке",
                    course_name="SYSTEM",
                )
                await update.message.reply_text(
                    "✅ Доступ открыт. Теперь вы можете пользоваться ботом."
                )
            else:
                await show_access_required(update, invalid_invite=True)
                return
        else:
            await show_access_required(update)
            return

    await show_main_menu(update, context)


async def show_courses(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Показывает список курсов с инлайн кнопками"""
    courses_list = list(COURSES.keys())

    keyboard = []
    for course in courses_list:
        keyboard.append([InlineKeyboardButton(
            course, callback_data=f"course_{course}")])

    keyboard.append([InlineKeyboardButton(
        "🗓️ Авто-ОС по группам", callback_data="auto_feedback_menu")])
    keyboard.append([InlineKeyboardButton(
        "🔄 Главное меню", callback_data="back_to_main_menu")])

    user = update.effective_user
    if user.id in BOT_CONFIG['admin_ids']:
        keyboard.append([InlineKeyboardButton(
            "📊 Статистика", callback_data="stats_main")])

    reply_markup = InlineKeyboardMarkup(keyboard)

    if update.message:
        await update.message.reply_text(
            "🎓 Выберите курс:",
            reply_markup=reply_markup
        )
    else:
        await update.callback_query.message.edit_text(
            "🎓 Выберите курс:",
            reply_markup=reply_markup
        )


async def handle_course_selection(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Обработка выбора курса через инлайн кнопки"""
    query = update.callback_query
    await query.answer()

    course_name = query.data.replace("course_", "")
    user_id = update.effective_user.id

    if course_name not in COURSES:
        await query.message.edit_text("Ошибка: курс не найден.")
        return

    context.user_data['selected_course'] = course_name
    lessons = get_lessons_in_order(course_name)
    context.user_data['lessons'] = lessons

    current_offset, repetition_count = activity_tracker.get_user_offset(
        user_id, course_name)
    context.user_data['current_offset'] = current_offset
    context.user_data['repetition_count'] = repetition_count

    saved_lesson_index = activity_tracker.get_course_lesson_state(
        user_id, course_name)

    if saved_lesson_index < len(lessons):
        context.user_data['current_lesson_index'] = saved_lesson_index
        logger.info(
            f"Восстановлен урок {saved_lesson_index + 1} для пользователя {user_id}, курс {course_name}")
    else:
        context.user_data['current_lesson_index'] = 0
        activity_tracker.save_course_lesson_state(user_id, course_name, 0)
        logger.info(
            f"Установлен первый урок для пользователя {user_id}, курс {course_name}")

    context.user_data['is_repetition_mode'] = False
    context.user_data['feedback_view_mode'] = 'regular'

    activity_tracker.log_action(
        update, "ВЫБОР_КУРСА", f"Выбран курс: {course_name}", course_name=course_name
    )

    keyboard = [
        [InlineKeyboardButton("🎯 Получить ОС урока",
                              callback_data="start_lesson")],
        [InlineKeyboardButton("🔄 Повторение урока",
                              callback_data="repetition_mode")],
        [InlineKeyboardButton(
            f"⚙️ Настройка номера (смещение: {current_offset})", callback_data="offset_settings")],
        [InlineKeyboardButton(
            "🔄 Выбор курса", callback_data="back_to_courses")]
    ]

    reply_markup = InlineKeyboardMarkup(keyboard)

    await query.message.edit_text(
        f"📚 Курс: {course_name}\n"
        f"🎯 Текущий урок: {saved_lesson_index + 1} из {len(lessons)}\n\n"
        "Выберите режим работы:",
        reply_markup=reply_markup
    )


async def handle_start_lesson(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Обработка начала обычного урока"""
    query = update.callback_query
    await query.answer()

    course_name = context.user_data['selected_course']
    lessons = context.user_data['lessons']

    context.user_data['is_repetition_mode'] = False
    context.user_data['feedback_view_mode'] = 'regular'

    current_index = context.user_data.get('current_lesson_index', 0)
    await show_lesson_navigation(update, context, course_name, lessons, current_index)

    activity_tracker.log_action(
        update, "РЕЖИМ_ОБЫЧНОГО_УРОКА",
        "Запущен режим обычного урока",
        course_name=course_name
    )


async def show_lesson_navigation(update: Update, context: ContextTypes.DEFAULT_TYPE,
                                 course_name: str, lessons: list, current_index: int) -> None:
    """Показывает навигацию по урокам с умным отображением кнопок"""
    if current_index >= len(lessons):
        current_index = 0

    lesson_name = lessons[current_index][0] if lessons else "Урок не найден"
    total_lessons = len(lessons)

    keyboard = []

    lesson_buttons = []

    if total_lessons <= 8:
        for i in range(total_lessons):
            button_text = f"•{i+1}•" if i == current_index else f"{i+1}"
            lesson_buttons.append(InlineKeyboardButton(
                button_text, callback_data=f"lesson_{i}"))
    else:
        show_lessons = set()

        for i in range(min(3, total_lessons)):
            show_lessons.add(i)

        start_range = max(0, current_index - 2)
        end_range = min(total_lessons, current_index + 3)
        for i in range(start_range, end_range):
            show_lessons.add(i)

        for i in range(max(0, total_lessons - 3), total_lessons):
            show_lessons.add(i)

        sorted_lessons = sorted(show_lessons)
        prev_lesson = -1

        for i in sorted_lessons:
            if prev_lesson != -1 and i - prev_lesson > 1:
                lesson_buttons.append(InlineKeyboardButton(
                    "...", callback_data="lesson_more"))

            button_text = f"•{i+1}•" if i == current_index else f"{i+1}"
            lesson_buttons.append(InlineKeyboardButton(
                button_text, callback_data=f"lesson_{i}"))
            prev_lesson = i

    for i in range(0, len(lesson_buttons), 6):
        keyboard.append(lesson_buttons[i:i+6])

    nav_buttons = []
    if current_index > 0:
        nav_buttons.append(InlineKeyboardButton(
            "◀️ Предыдущий", callback_data="prev_lesson"))

    nav_buttons.append(InlineKeyboardButton(
        "📅 Выбрать дату", callback_data="select_date"))

    if current_index < total_lessons - 1:
        nav_buttons.append(InlineKeyboardButton(
            "Следующий ▶️", callback_data="next_lesson"))

    if nav_buttons:
        keyboard.append(nav_buttons)

    current_offset = context.user_data.get('current_offset', 0)
    if current_offset != 0:
        keyboard.append([InlineKeyboardButton(
            f"⚙️ Оффсет: {current_offset}", callback_data="offset_settings")])

    keyboard.append([InlineKeyboardButton(
        "🔄 Выбор курса", callback_data="back_to_courses")])

    reply_markup = InlineKeyboardMarkup(keyboard)

    text = f"""📚 Курс: {course_name}
🎯 Урок: {lesson_name} ({current_index + 1}/{total_lessons})

Выберите действие:"""

    if update.callback_query:
        await update.callback_query.message.edit_text(text, reply_markup=reply_markup)
    else:
        await update.message.reply_text(text, reply_markup=reply_markup)


async def handle_lesson_selection(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Обработка выбора урока через инлайн кнопки"""
    query = update.callback_query
    await query.answer()

    if query.data == "lesson_more":
        await show_extended_lessons(update, context)
        return

    lesson_index = int(query.data.replace("lesson_", ""))
    context.user_data['current_lesson_index'] = lesson_index

    course_name = context.user_data['selected_course']
    user_id = update.effective_user.id

    activity_tracker.save_course_lesson_state(
        user_id, course_name, lesson_index)

    lessons = context.user_data['lessons']
    if lesson_index < len(lessons):
        lesson_name = lessons[lesson_index][0]
    else:
        lesson_name = f"Урок {lesson_index + 1}"

    activity_tracker.log_action(
        update, "ВЫБОР_УРОКА", f"Выбран урок №{lesson_index + 1}: {lesson_name}",
        course_name=course_name, lesson_number=lesson_index + 1
    )

    await request_lesson_date(update, context)


async def show_extended_lessons(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Показывает расширенный список уроков"""
    course_name = context.user_data['selected_course']
    lessons = context.user_data['lessons']
    current_index = context.user_data['current_lesson_index']
    total_lessons = len(lessons)

    keyboard = []

    row = []
    for i in range(total_lessons):
        lesson_name = lessons[i][0] if i < len(lessons) else f"Урок {i+1}"

        button_text = f"• {i+1} •" if i == current_index else f"{i+1}"
        row.append(InlineKeyboardButton(
            button_text, callback_data=f"lesson_{i}"))

        if len(row) == 8:
            keyboard.append(row)
            row = []

    if row:
        keyboard.append(row)

    nav_buttons = []
    if current_index > 0:
        nav_buttons.append(InlineKeyboardButton(
            "◀️ Предыдущий", callback_data="prev_lesson"))

    nav_buttons.append(InlineKeyboardButton(
        "📅 Выбрать дату", callback_data="select_date"))

    if current_index < total_lessons - 1:
        nav_buttons.append(InlineKeyboardButton(
            "Следующий ▶️", callback_data="next_lesson"))

    keyboard.append(nav_buttons)

    current_offset = context.user_data.get('current_offset', 0)
    if current_offset != 0:
        keyboard.append([InlineKeyboardButton(
            f"⚙️ Оффсет: {current_offset}", callback_data="offset_settings")])

    keyboard.append([InlineKeyboardButton(
        "🔄 Выбор курса", callback_data="back_to_courses")])

    reply_markup = InlineKeyboardMarkup(keyboard)

    await update.callback_query.message.edit_text(
        f"📚 {course_name}\n🎯 Всего уроков: {total_lessons}\n\nВыберите номер урока:",
        reply_markup=reply_markup
    )


async def request_lesson_date(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Запрашивает дату проведения урока с инлайн кнопками."""
    course_name = context.user_data['selected_course']
    lessons = context.user_data['lessons']
    current_index = context.user_data['current_lesson_index']

    if current_index < len(lessons):
        lesson_name = lessons[current_index][0]
    else:
        lesson_name = f"Урок {current_index + 1}"

    today = datetime.now().date()
    yesterday = today - timedelta(days=1)
    day_before_yesterday = today - timedelta(days=2)
    three_days_ago = today - timedelta(days=3)

    keyboard = [
        [
            InlineKeyboardButton(
                f"Сегодня ({today.strftime('%d.%m.%Y')})", callback_data="date_today"),
            InlineKeyboardButton(
                f"Вчера ({yesterday.strftime('%d.%m.%Y')})", callback_data="date_yesterday")
        ],
        [
            InlineKeyboardButton(
                f"Позавчера ({day_before_yesterday.strftime('%d.%m.%Y')})", callback_data="date_2days_ago"),
            InlineKeyboardButton(
                f"3 дня назад ({three_days_ago.strftime('%d.%m.%Y')})", callback_data="date_3days_ago")
        ],
        [
            InlineKeyboardButton("📅 Другая дата", callback_data="date_custom")
        ],
        [
            InlineKeyboardButton("◀️ Назад к уроку",
                                 callback_data="back_to_lesson_nav")
        ]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)

    await update.callback_query.message.edit_text(
        f"📚 {course_name}\n"
        f"🎯 {lesson_name}\n\n"
        "Выберите дату проведения урока:",
        reply_markup=reply_markup
    )


async def handle_date_selection(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Обрабатывает выбор даты через инлайн кнопки"""
    query = update.callback_query
    await query.answer()

    date_mapping = {
        "date_today": datetime.now().date(),
        "date_yesterday": (datetime.now() - timedelta(days=1)).date(),
        "date_2days_ago": (datetime.now() - timedelta(days=2)).date(),
        "date_3days_ago": (datetime.now() - timedelta(days=3)).date()
    }

    if query.data in date_mapping:
        lesson_date = datetime.combine(
            date_mapping[query.data], datetime.min.time())
        context.user_data['current_lesson_date'] = lesson_date

        course_name = context.user_data['selected_course']
        lesson_index = context.user_data['current_lesson_index']
        lesson_number = lesson_index + 1

        lessons = context.user_data['lessons']
        if lesson_index < len(lessons):
            lesson_name = lessons[lesson_index][0]
        else:
            lesson_name = f"Урок {lesson_number}"

        activity_tracker.log_action(
            update, "УСТАНОВКА_ДАТЫ", f"Установлена дата урока: {query.data}",
            course_name=course_name, lesson_number=lesson_number,
            lesson_date=lesson_date.strftime("%d.%m.%Y")
        )

        await show_current_feedback_view(update, context)

    elif query.data == "date_custom":
        await query.message.edit_text(
            "Введите дату проведения урока в формате ДД.ММ.ГГГГ (например, 15.09.2024):"
        )
        context.user_data['awaiting_custom_date'] = True

    elif query.data == "back_to_lesson_nav":
        course_name = context.user_data['selected_course']
        lessons = context.user_data['lessons']
        current_index = context.user_data['current_lesson_index']
        await show_lesson_navigation(update, context, course_name, lessons, current_index)


async def handle_custom_date_input(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Обрабатывает ручной ввод даты"""
    text = update.message.text

    try:
        lesson_date = parse_date(text, "%d.%m.%Y", "дата урока")
        context.user_data['current_lesson_date'] = lesson_date
        context.user_data['awaiting_custom_date'] = False

        course_name = context.user_data['selected_course']
        lesson_index = context.user_data['current_lesson_index']
        lesson_number = lesson_index + 1

        lessons = context.user_data['lessons']
        if lesson_index < len(lessons):
            lesson_name = lessons[lesson_index][0]
        else:
            lesson_name = f"Урок {lesson_number}"

        activity_tracker.log_action(
            update, "УСТАНОВКА_ДАТЫ", f"Установлена дата урока: {text}",
            course_name=course_name, lesson_number=lesson_number, lesson_date=text
        )

        await show_current_feedback_view(update, context)

    except ValidationError:
        await update.message.reply_text(
            "Неверный формат даты. Пожалуйста, введите дату в формате ДД.ММ.ГГГГ (например, 15.09.2024):"
        )


async def show_lesson(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Показывает текущий урок с датой и сохраняет прогресс"""
    try:
        course_name = context.user_data['selected_course']
        lessons = context.user_data['lessons']
        current_index = context.user_data['current_lesson_index']
        user_id = update.effective_user.id

        print(
            f"DEBUG: course_name={course_name}, lessons_count={len(lessons)}, current_index={current_index}")

        if not lessons or current_index >= len(lessons):
            error_msg = f"Ошибка: уроки не найдены. Уроков: {len(lessons)}, индекс: {current_index}"
            print(f"DEBUG ERROR: {error_msg}")
            if update.message:
                await update.message.reply_text(error_msg)
            else:
                await update.callback_query.message.edit_text(error_msg)
            return

        lesson_name, feedback_data = lessons[current_index]
        print(f"DEBUG: lesson_name={lesson_name}")

        lesson_date = context.user_data.get(
            'current_lesson_date', datetime.now())
        lesson_number = current_index + 1

        current_offset = context.user_data.get('current_offset', 0)
        is_repetition = context.user_data.get('is_repetition_mode', False)

        print(f"DEBUG: offset={current_offset}, repetition={is_repetition}")

        activity_tracker.save_user_state(user_id, course_name, current_index)

        absent_students = context.user_data.get('absent_students')

        if absent_students:
            feedback_text = format_feedback_with_absent_students(
                lesson_name, lesson_number, lesson_date, feedback_data, absent_students,
                offset=current_offset, is_repetition=is_repetition
            )
        else:
            feedback_text = format_feedback(
                lesson_name, lesson_number, lesson_date, feedback_data,
                offset=current_offset, is_repetition=is_repetition
            )

        image_path = feedback_data.get('image')

        activity_tracker.log_action(
            update, "ПРОСМОТР_УРОКА", f"Просмотр урока: {lesson_name}",
            course_name=course_name, lesson_number=lesson_number,
            lesson_date=lesson_date.strftime("%d.%m.%Y")
        )

        keyboard = [
            [
                InlineKeyboardButton(
                    "◀️ Предыдущий", callback_data="prev_lesson"),
                InlineKeyboardButton(
                    "Следующий ▶️", callback_data="next_lesson")
            ],
            [
                InlineKeyboardButton(
                    "📅 Изменить дату", callback_data="select_date")
            ]
        ]

        if absent_students:
            keyboard.append([InlineKeyboardButton(
                "❌ Очистить отсутствующих", callback_data="clear_absent")])
            keyboard.append([InlineKeyboardButton(
                "👥 Изменить отсутствующих", callback_data="add_absent")])
        else:
            keyboard.append([InlineKeyboardButton(
                "👥 Добавить отсутствующих", callback_data="add_absent")])

        keyboard.append([InlineKeyboardButton(
            "💻 Онлайн/Индивид", callback_data="online_individual")])

        keyboard.append([InlineKeyboardButton(
            f"⚙️ Оффсет: {current_offset}", callback_data="offset_settings")])

        keyboard.append([InlineKeyboardButton(
            "🔄 Выбор курса", callback_data="back_to_courses")])

        reply_markup = InlineKeyboardMarkup(keyboard)

        if image_path and os.path.exists(image_path):
            if update.message:
                await update.message.reply_photo(
                    photo=open(image_path, 'rb'),
                    caption=feedback_text,
                    caption_entities=build_feedback_link_entities(feedback_text),
                    reply_markup=reply_markup
                )
            else:
                await update.callback_query.message.delete()
                await update.callback_query.message.chat.send_photo(
                    photo=open(image_path, 'rb'),
                    caption=feedback_text,
                    caption_entities=build_feedback_link_entities(feedback_text),
                    reply_markup=reply_markup
                )
        else:
            if update.message:
                await update.message.reply_text(
                    feedback_text,
                    entities=build_feedback_link_entities(feedback_text),
                    link_preview_options=FEEDBACK_LINK_PREVIEW_OPTIONS,
                    reply_markup=reply_markup
                )
            else:
                await update.callback_query.message.edit_text(
                    feedback_text,
                    entities=build_feedback_link_entities(feedback_text),
                    link_preview_options=FEEDBACK_LINK_PREVIEW_OPTIONS,
                    reply_markup=reply_markup
                )

    except Exception as e:
        error_msg = f"❌ Произошла ошибка в show_lesson: {str(e)}"
        print(f"DEBUG FULL ERROR: {error_msg}")
        print(f"DEBUG Traceback: {traceback.format_exc()}")

        if update.callback_query:
            await update.callback_query.message.edit_text(error_msg)
        else:
            await update.message.reply_text(error_msg)


async def handle_clear_absent_students(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Очищает список отсутствующих учеников"""
    query = update.callback_query
    await query.answer()

    if query.data == "clear_absent":
        absent_students = context.user_data.get('absent_students', [])

        if 'absent_students' in context.user_data:
            del context.user_data['absent_students']

        activity_tracker.log_action(
            update, "ОЧИСТКА_ОТСУТСТВУЮЩИХ",
            f"Очищены отсутствующие: {', '.join(absent_students)}",
            course_name=context.user_data.get('selected_course', ''),
            lesson_number=context.user_data.get('current_lesson_index', 0) + 1
        )

        await show_lesson(update, context)


async def handle_online_individual(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Обрабатывает создание версии для онлайн/индивидуальных занятий"""
    query = update.callback_query
    await query.answer()

    if query.data == "online_individual":
        context.user_data['feedback_view_mode'] = 'online_individual'
        await show_online_individual_lesson(update, context)

        course_name = context.user_data['selected_course']
        lessons = context.user_data['lessons']
        current_index = context.user_data['current_lesson_index']
        lesson_name, _ = lessons[current_index]
        lesson_date = context.user_data.get('current_lesson_date', datetime.now())
        lesson_number = current_index + 1

        activity_tracker.log_action(
            update,
            "ОНЛАЙН_ИНДИВИДУАЛЬНЫЙ",
            f"Создана версия для онлайн/индивидуальных занятий: {lesson_name}",
            course_name=course_name,
            lesson_number=lesson_number,
            lesson_date=lesson_date.strftime("%d.%m.%Y")
        )


async def show_online_individual_lesson(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Показывает текущий урок в формате онлайн/индивидуального занятия."""
    course_name = context.user_data['selected_course']
    lessons = context.user_data['lessons']
    current_index = context.user_data['current_lesson_index']

    lesson_name, feedback_data = lessons[current_index]
    lesson_date = context.user_data.get('current_lesson_date', datetime.now())
    lesson_number = current_index + 1

    current_offset = context.user_data.get('current_offset', 0)
    is_repetition = context.user_data.get('is_repetition_mode', False)
    absent_students = context.user_data.get('absent_students')

    feedback_text = format_feedback_online_individual(
        lesson_name, lesson_number, lesson_date, feedback_data, absent_students,
        offset=current_offset, is_repetition=is_repetition
    )

    image_path = feedback_data.get('image')
    keyboard = [
        [
            InlineKeyboardButton("◀️ Предыдущий", callback_data="prev_lesson"),
            InlineKeyboardButton("Следующий ▶️", callback_data="next_lesson")
        ],
        [
            InlineKeyboardButton("📅 Изменить дату", callback_data="select_date")
        ],
        [
            InlineKeyboardButton("◀️ Назад к обычной", callback_data="back_to_regular"),
            InlineKeyboardButton("🔄 Выбор курса", callback_data="back_to_courses")
        ]
    ]

    reply_markup = InlineKeyboardMarkup(keyboard)

    if image_path and os.path.exists(image_path):
        if update.message:
            await update.message.reply_photo(
                photo=open(image_path, 'rb'),
                caption=feedback_text,
                caption_entities=build_feedback_link_entities(feedback_text),
                reply_markup=reply_markup
            )
        else:
            await update.callback_query.message.delete()
            await update.callback_query.message.chat.send_photo(
                photo=open(image_path, 'rb'),
                caption=feedback_text,
                caption_entities=build_feedback_link_entities(feedback_text),
                reply_markup=reply_markup
            )
    else:
        if update.message:
            await update.message.reply_text(
                feedback_text,
                entities=build_feedback_link_entities(feedback_text),
                link_preview_options=FEEDBACK_LINK_PREVIEW_OPTIONS,
                reply_markup=reply_markup
            )
        else:
            await update.callback_query.message.edit_text(
                feedback_text,
                entities=build_feedback_link_entities(feedback_text),
                link_preview_options=FEEDBACK_LINK_PREVIEW_OPTIONS,
                reply_markup=reply_markup
            )


async def show_current_feedback_view(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Показывает урок в текущем выбранном виде (обычный или онлайн/индивидуальный)."""
    if context.user_data.get('feedback_view_mode') == 'online_individual':
        await show_online_individual_lesson(update, context)
    else:
        await show_lesson(update, context)


async def handle_back_to_regular(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Возвращает к обычной версии обратной связи"""
    query = update.callback_query
    await query.answer()

    if query.data == "back_to_regular":
        context.user_data['feedback_view_mode'] = 'regular'
        await show_lesson(update, context)

        activity_tracker.log_action(
            update, "ВОЗВРАТ_К_ОБЫЧНОЙ",
            "Возврат к обычной версии обратной связи",
            course_name=context.user_data.get('selected_course', ''),
            lesson_number=context.user_data.get('current_lesson_index', 0) + 1
        )


async def handle_navigation(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Обрабатывает навигацию по урокам"""
    query = update.callback_query
    await query.answer()

    if query.data == "select_date":
        await request_lesson_date(update, context)
        return

    if query.data == "back_to_lesson":
        await show_current_feedback_view(update, context)
        return

    course_name = context.user_data['selected_course']
    lessons = context.user_data['lessons']
    current_index = context.user_data['current_lesson_index']
    user_id = update.effective_user.id

    if query.data == "next_lesson":
        activity_tracker.log_action(update, "НАВИГАЦИЯ", "Следующий урок")

        if current_index < len(lessons) - 1:
            new_index = current_index + 1
            context.user_data['current_lesson_index'] = new_index

            activity_tracker.save_course_lesson_state(
                user_id, course_name, new_index)

            if 'current_lesson_date' in context.user_data:
                await show_current_feedback_view(update, context)
            else:
                await request_lesson_date(update, context)
        else:
            await query.message.edit_text(
                "🎉 Это последний урок в курсе!",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton(
                        "🔄 Выбор курса", callback_data="back_to_courses")]
                ])
            )

    elif query.data == "prev_lesson":
        activity_tracker.log_action(update, "НАВИГАЦИЯ", "Предыдущий урок")

        if current_index > 0:
            new_index = current_index - 1
            context.user_data['current_lesson_index'] = new_index

            activity_tracker.save_course_lesson_state(
                user_id, course_name, new_index)

            if 'current_lesson_date' in context.user_data:
                await show_current_feedback_view(update, context)
            else:
                await request_lesson_date(update, context)
        else:
            await query.message.edit_text(
                "Это первый урок в курсе.",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton(
                        "🔄 Выбор курса", callback_data="back_to_courses")]
                ])
            )


async def change_course(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Смена курса БЕЗ очистки состояния"""
    activity_tracker.log_action(update, "НАВИГАЦИЯ", "Смена курса")

    if 'absent_students' in context.user_data:
        del context.user_data['absent_students']
    if 'awaiting_absent_students' in context.user_data:
        del context.user_data['awaiting_absent_students']

    context.user_data.clear()

    if update.callback_query:
        await show_courses(update, context)
    else:
        await update.message.reply_text("Возвращаемся к выбору курса...")
        await show_courses(update, context)


async def stats_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Показывает статистику использования с расширенными возможностями"""
    user = update.effective_user

    ADMIN_IDS = BOT_CONFIG['admin_ids']

    if user.id not in ADMIN_IDS:
        if update.callback_query:
            await update.callback_query.message.edit_text("У вас нет прав для просмотра статистики.")
        else:
            await update.message.reply_text("У вас нет прав для просмотра статистики.")
        return

    keyboard = [
        [InlineKeyboardButton("📊 Статистика за сегодня",
                              callback_data="stats_today")],
        [InlineKeyboardButton("💾 Управление бэкапами",
                              callback_data="backup_menu")],
        [InlineKeyboardButton("👤 Поиск пользователя",
                              callback_data="stats_search_user")],
        [InlineKeyboardButton("📅 Статистика за дату",
                              callback_data="stats_by_date")],
        [InlineKeyboardButton("🔄 Назад к курсам",
                              callback_data="back_to_courses")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)

    if update.callback_query:
        await update.callback_query.message.edit_text(
            "📈 Меню статистики\nВыберите опцию:",
            reply_markup=reply_markup
        )
    else:
        await update.message.reply_text(
            "📈 Меню статистики\nВыберите опцию:",
            reply_markup=reply_markup
        )


async def show_backup_menu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Показывает меню управления резервными копиями"""
    keyboard = [
        [InlineKeyboardButton("🔄 Создать резервную копию",
                              callback_data="backup_create")],
        [InlineKeyboardButton("📊 Создать Excel отчет",
                              callback_data="backup_excel")],
        [InlineKeyboardButton("📋 Список бэкапов",
                              callback_data="backup_list")],
        [InlineKeyboardButton("📊 Информация о хранилище",
                              callback_data="backup_storage_info")],
        [InlineKeyboardButton("🔧 Запустить обслуживание",
                              callback_data="backup_maintenance")],
        [InlineKeyboardButton("📈 Назад к статистике",
                              callback_data="stats_main")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)

    await update.callback_query.message.edit_text(
        "💾 Управление резервными копиями\n\n*Теперь бэкапы включают Excel отчеты для анализа*",
        reply_markup=reply_markup,
        parse_mode='Markdown'
    )


async def handle_backup_management(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Управление резервными копиями для админов"""
    query = update.callback_query
    await query.answer()

    if query.data == "backup_create":
        manager = BackupManager()
        backup_path = manager.create_backup(include_excel=True)

        if backup_path:
            backups = manager.get_backup_info()
            if backups:
                latest = backups[0]

                text = f"""✅ Резервная копия создана:

📦 Архив: `{os.path.basename(backup_path)}`
📊 Размер: {latest['size_mb']} МБ
📈 Содержимое: полная БД + Excel за текущий месяц
🕐 Создана: {latest['created']}"""

            keyboard = [
                [InlineKeyboardButton("📋 Список бэкапов",
                                      callback_data="backup_list")],
                [InlineKeyboardButton(
                    "📊 Создать отчет за 30 дней", callback_data="backup_excel_30")],
                [InlineKeyboardButton(
                    "💾 Управление бэкапами", callback_data="backup_menu")],
                [InlineKeyboardButton(
                    "📈 Назад к статистике", callback_data="stats_main")]
            ]
            reply_markup = InlineKeyboardMarkup(keyboard)

            await query.message.edit_text(text, reply_markup=reply_markup, parse_mode='Markdown')
        else:
            keyboard = [
                [InlineKeyboardButton(
                    "🔄 Повторить", callback_data="backup_create")],
                [InlineKeyboardButton(
                    "💾 Управление бэкапами", callback_data="backup_menu")]
            ]
            reply_markup = InlineKeyboardMarkup(keyboard)

            await query.message.edit_text("❌ Ошибка создания резервной копии", reply_markup=reply_markup)

    elif query.data == "backup_excel":
        manager = BackupManager()
        excel_path = manager.create_standalone_excel_report(period_days=30)

        if excel_path:
            text = f"✅ Excel отчет за 30 дней создан:\n`{os.path.basename(excel_path)}`\n\nФайл сохранен в папке `exports/`"
        else:
            text = "❌ Ошибка создания Excel отчета"

        keyboard = [
            [InlineKeyboardButton("📦 Создать полный бэкап",
                                  callback_data="backup_create")],
            [InlineKeyboardButton("💾 Управление бэкапами",
                                  callback_data="backup_menu")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)

        await query.message.edit_text(text, reply_markup=reply_markup, parse_mode='Markdown')

    elif query.data == "backup_excel_30":
        manager = BackupManager()
        excel_path = manager.create_standalone_excel_report(period_days=30)

        if excel_path:
            text = f"✅ Excel отчет за 30 дней создан:\n`{os.path.basename(excel_path)}`\n\nФайл содержит данные за последние 30 дней."
        else:
            text = "❌ Ошибка создания Excel отчета"

        keyboard = [
            [InlineKeyboardButton("📦 Создать полный бэкап",
                                  callback_data="backup_create")],
            [InlineKeyboardButton("💾 Управление бэкапами",
                                  callback_data="backup_menu")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)

        await query.message.edit_text(text, reply_markup=reply_markup, parse_mode='Markdown')

    elif query.data == "backup_list":
        manager = BackupManager()
        backups = manager.get_backup_info()

        if backups:
            text = "📦 Список резервных копий:\n\n"
            for i, backup in enumerate(backups[:10], 1):
                excel_icon = "✅" if backup['has_excel'] else "❌"
                text += f"**{i}. {backup['filename']}**\n"
                text += f"   📏 Размер: {backup['size_mb']} МБ\n"
                text += f"   📊 Excel: {excel_icon}\n"
                text += f"   🕐 Создана: {backup['created']}\n\n"

            text += f"*Всего бэкапов: {len(backups)}*"

            keyboard = [
                [InlineKeyboardButton(
                    "🔄 Создать новый бэкап", callback_data="backup_create")],
                [InlineKeyboardButton(
                    "📊 Создать отчет за 30 дней", callback_data="backup_excel_30")],
                [InlineKeyboardButton(
                    "📊 Информация о хранилище", callback_data="backup_storage_info")],
                [InlineKeyboardButton(
                    "💾 Управление бэкапами", callback_data="backup_menu")],
                [InlineKeyboardButton(
                    "📈 Назад к статистике", callback_data="stats_main")]
            ]
            reply_markup = InlineKeyboardMarkup(keyboard)

            await query.message.edit_text(text, reply_markup=reply_markup, parse_mode='Markdown')
        else:
            keyboard = [
                [InlineKeyboardButton(
                    "🔄 Создать первый бэкап", callback_data="backup_create")],
                [InlineKeyboardButton(
                    "💾 Управление бэкапами", callback_data="backup_menu")]
            ]
            reply_markup = InlineKeyboardMarkup(keyboard)

            await query.message.edit_text(
                "📦 Резервные копии не найдены\n\n"
                "Создайте первую резервную копию для защиты данных.",
                reply_markup=reply_markup
            )

    elif query.data == "backup_storage_info":
        manager = BackupManager()
        storage_info = manager.get_storage_info()

        text = "💽 Информация о хранилище:\n\n"
        text += f"📊 **Основная БД:** {storage_info.get('db_size_mb', 0)} МБ\n"
        text += f"📦 **Резервные копии:** {storage_info.get('backups_count', 0)} шт. ({storage_info.get('backups_total_size_mb', 0)} МБ)\n"
        text += f"📁 **Excel экспорты:** {storage_info.get('exports_count', 0)} шт. ({storage_info.get('exports_total_size_mb', 0)} МБ)\n"
        text += f"💾 **Общий размер:** {storage_info.get('total_size_mb', 0)} МБ\n\n"
        text += f"⚙️ **Настройки:**\n"
        text += f"• Хранить данные: {BACKUP_CONFIG['keep_months']} мес.\n"
        text += f"• Хранить бэкапов: {BACKUP_CONFIG['keep_backups']} шт."

        keyboard = [
            [InlineKeyboardButton("🔧 Запустить обслуживание",
                                  callback_data="backup_maintenance")],
            [InlineKeyboardButton("📋 Список бэкапов",
                                  callback_data="backup_list")],
            [InlineKeyboardButton("💾 Управление бэкапами",
                                  callback_data="backup_menu")],
            [InlineKeyboardButton("📈 Назад к статистике",
                                  callback_data="stats_main")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)

        await query.message.edit_text(text, reply_markup=reply_markup, parse_mode='Markdown')

    elif query.data == "backup_maintenance":
        keyboard = [
            [InlineKeyboardButton(
                "✅ Да, выполнить", callback_data="backup_maintenance_confirm")],
            [InlineKeyboardButton("❌ Отмена", callback_data="backup_menu")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)

        await query.message.edit_text(
            "⚠️ **Внимание!**\n\n"
            "Это действие выполнит:\n"
            "• Создание резервной копии с Excel\n"
            "• Создание отчета за 30 дней\n"
            "• Очистку старых данных\n"
            "• Удаление старых бэкапов\n\n"
            "Продолжить?",
            reply_markup=reply_markup,
            parse_mode='Markdown'
        )

    elif query.data == "backup_maintenance_confirm":
        await query.message.edit_text("🔄 Выполняется обслуживание...")

        try:
            result = monthly_maintenance()

            text = "🔧 **Результат обслуживания:**\n\n"
            text += f"✅ **Резервная копия:** {'Создана' if result['backup_created'] else 'Ошибка'}\n"
            text += f"📊 **Excel отчет:** {'Создан' if result['excel_created'] else 'Ошибка'}\n"
            text += f"🗑️ **Удалено записей:** {result['records_deleted']}\n"
            text += f"📦 **Удалено старых бэкапов:** {result['old_backups_deleted']}\n\n"
            text += "✅ Обслуживание завершено!"

            keyboard = [
                [InlineKeyboardButton(
                    "📊 Информация о хранилище", callback_data="backup_storage_info")],
                [InlineKeyboardButton("📋 Список бэкапов",
                                      callback_data="backup_list")],
                [InlineKeyboardButton(
                    "💾 Управление бэкапами", callback_data="backup_menu")],
                [InlineKeyboardButton(
                    "📈 Назад к статистике", callback_data="stats_main")]
            ]
            reply_markup = InlineKeyboardMarkup(keyboard)

            await query.message.edit_text(
                text,
                reply_markup=reply_markup,
                parse_mode='Markdown'
            )

        except Exception as e:
            logger.error(f"Ошибка при выполнении обслуживания: {e}")
            await query.message.edit_text(
                f"❌ Ошибка при выполнении обслуживания:\n{str(e)}",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton(
                        "💾 Управление бэкапами", callback_data="backup_menu")]
                ])
            )


async def show_daily_stats(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Показывает дневную статистику"""
    try:
        stats = activity_tracker.get_daily_stats()

        action_translations = {
            "START": "Начало работы",
            "MESSAGE": "Сообщение",
            "COURSE_SELECTED": "Выбор курса",
            "LESSON_SELECTED": "Выбор урока",
            "DATE_SET": "Установка даты",
            "LESSON_VIEWED": "Просмотр урока",
            "NAVIGATION": "Навигация",
            "СТАРТ": "Начало работы",
            "СООБЩЕНИЕ": "Сообщение",
            "ВЫБОР_КУРСА": "Выбор курса",
            "ВЫБОР_УРОКА": "Выбор урока",
            "УСТАНОВКА_ДАТЫ": "Установка даты",
            "ПРОСМОТР_УРОКА": "Просмотр урока",
            "НАВИГАЦИЯ": "Навигация"
        }

        stats_text = f"""📊 Статистика за сегодня ({datetime.now().strftime('%d.%m.%Y')})

👥 Уникальных пользователей: {stats['unique_users']}
📈 Всего действий: {stats['total_actions']}
📚 Активных курсов: {stats['active_courses']}

🎯 Топ действий сегодня:
"""
        for action, count in stats['top_actions']:
            russian_action = action_translations.get(action, action)
            stats_text += f"\n• {russian_action}: {count}"

        keyboard = [
            [InlineKeyboardButton("🔄 Обновить", callback_data="stats_today")],
            [InlineKeyboardButton(
                "📈 Главное меню статистики", callback_data="stats_main")],
            [InlineKeyboardButton(
                "🔄 Выбор курса", callback_data="back_to_courses")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)

        if update.callback_query:
            await update.callback_query.message.edit_text(stats_text, reply_markup=reply_markup)
        else:
            await update.message.reply_text(stats_text, reply_markup=reply_markup)

    except Exception as e:
        error_text = f"Ошибка получения статистики: {e}"

        keyboard = [
            [InlineKeyboardButton("🔄 Повторить", callback_data="stats_today")],
            [InlineKeyboardButton(
                "📈 Главное меню статистики", callback_data="stats_main")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)

        if update.callback_query:
            await update.callback_query.message.edit_text(error_text, reply_markup=reply_markup)
        else:
            await update.message.reply_text(error_text, reply_markup=reply_markup)


async def handle_stats_navigation(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Обрабатывает навигацию в меню статистики"""
    query = update.callback_query
    await query.answer()

    logger.info(f"Обрабатывается callback: {query.data}")

    if query.data == "stats_today":
        logger.info("Обновление статистики за сегодня")
        await show_daily_stats(update, context)
    elif query.data == "stats_main":
        await stats_command(update, context)
    elif query.data == "stats_search_user":
        await query.message.edit_text(
            "Введите username или ID пользователя для поиска:"
        )
        context.user_data['awaiting_user_search'] = True
    elif query.data == "stats_by_date":
        await query.message.edit_text(
            "Введите дату в формате ДД.ММ.ГГГГ:"
        )
        context.user_data['awaiting_date_stats'] = True
    elif query.data == "back_to_courses":
        await change_course(update, context)
    else:
        logger.warning(f"Неизвестный callback: {query.data}")
        await query.message.edit_text("❌ Неизвестная команда")


async def handle_stats_user_search(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Обрабатывает поиск пользователя для статистики"""
    text = update.message.text
    context.user_data['awaiting_user_search'] = False

    try:
        users = activity_tracker.search_users(text)

        if not users:
            await update.message.reply_text(f"Пользователи по запросу '{text}' не найдены.")
            return

        if len(users) == 1:
            user_id = users[0][0]
            await show_user_stats(update, context, user_id=user_id)
        else:
            keyboard = []
            for user in users[:10]:
                username = user[1] if user[1] != 'unknown' else f"ID: {user[0]}"
                display_name = f"@{username}" if user[1] != 'unknown' else f"Пользователь {user[0]}"
                if user[2]:
                    display_name += f" ({user[2]}"
                    if user[3]:
                        display_name += f" {user[3]}"
                    display_name += ")"

                keyboard.append([InlineKeyboardButton(
                    display_name, callback_data=f"stats_user_{user[0]}")])

            keyboard.append([InlineKeyboardButton(
                "📈 Главное меню статистики", callback_data="stats_main")])
            keyboard.append([InlineKeyboardButton(
                "🔄 Выбор курса", callback_data="back_to_courses")])
            reply_markup = InlineKeyboardMarkup(keyboard)

            await update.message.reply_text(
                f"Найдено пользователей: {len(users)}\nВыберите пользователя:",
                reply_markup=reply_markup
            )

    except Exception as e:
        await update.message.reply_text(f"Ошибка поиска пользователя: {e}")


async def show_user_stats(update: Update, context: ContextTypes.DEFAULT_TYPE,
                          user_id: int = None, username: str = None, date: str = None) -> None:
    """Показывает статистику пользователя"""
    try:
        user_stats = activity_tracker.get_user_stats(
            user_id=user_id, username=username, date=date
        )

        if not user_stats:
            if update.callback_query:
                await update.callback_query.message.edit_text("Статистика пользователя не найдена.")
            else:
                await update.message.reply_text("Статистика пользователя не найдена.")
            return

        user_info = user_stats['user_info']
        stats = user_stats['stats']
        course_stats = user_stats['course_stats']
        recent_actions = user_stats['recent_actions']

        date_suffix = f" за {date}" if date else ""

        stats_text = f"""👤 Статистика пользователя{date_suffix}

📧 Username: @{user_info['username'] if user_info['username'] != 'unknown' else 'не указан'}
👨‍💼 Имя: {user_info['first_name'] or 'Не указано'} {user_info['last_name'] or ''}
🆔 ID: {user_info['user_id']}

📊 Активность:
• Всего действий: {stats['total_actions']}
• Курсов просмотрено: {stats['courses_accessed']}
• Первый визит: {user_info['first_seen'][:16]}
• Последний визит: {user_info['last_seen'][:16]}

📚 Статистика по курсам:
"""
        for course, views in course_stats[:5]:
            stats_text += f"• {course}: {views} просмотров\n"

        if len(course_stats) > 5:
            stats_text += f"• ... и ещё {len(course_stats) - 5} курсов\n"

        stats_text += "\n🕒 Последние действия:\n"
        for i, (action, details, timestamp, course) in enumerate(recent_actions[:3]):
            time = timestamp[11:16]
            short_details = details[:20] + \
                "..." if len(details) > 20 else details
            stats_text += f"{i+1}. {time} - {action}: {short_details}\n"

        if len(recent_actions) > 3:
            stats_text += f"... и ещё {len(recent_actions) - 3} действий\n"

        keyboard = [
            [InlineKeyboardButton(
                "📈 Главное меню статистики", callback_data="stats_main")],
            [InlineKeyboardButton(
                "🔄 Выбор курса", callback_data="back_to_courses")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)

        if update.callback_query:
            await update.callback_query.message.edit_text(stats_text, reply_markup=reply_markup)
        else:
            await update.message.reply_text(stats_text, reply_markup=reply_markup)

    except Exception as e:
        error_text = f"Ошибка получения статистики пользователя: {e}"
        if update.callback_query:
            await update.callback_query.message.edit_text(error_text)
        else:
            await update.message.reply_text(error_text)


async def show_main_menu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Показывает главное меню выбора режима работы"""
    if not user_has_bot_access(update.effective_user.id):
        await show_access_required(update)
        return

    activity_tracker.log_action(
        update, "СТАРТ", "Пользователь начал работу с ботом")

    context.user_data.clear()

    keyboard = [
        [InlineKeyboardButton("📊 Обратная связь",
                              callback_data="mode_feedback")],
        [InlineKeyboardButton("🎯 Хаб преподавателя",
                              callback_data="mode_teacher_hub")]
    ]

    reply_markup = InlineKeyboardMarkup(keyboard)

    if update.message:
        await update.message.reply_text(
            "🎓 *Добро пожаловать в бот Алгоритмики!*\n\n"
            "Выберите режим работы:",
            reply_markup=reply_markup,
            parse_mode='Markdown'
        )
    else:
        await update.callback_query.message.edit_text(
            "🎓 *Добро пожаловать в бот Алгоритмики!*\n\n"
            "Выберите режим работы:",
            reply_markup=reply_markup,
            parse_mode='Markdown'
        )




async def show_teacher_hub(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Показывает главное меню Хаба преподавателя"""
    user = update.effective_user

    activity_tracker.log_action(
        update, "TEACHER_HUB_ENTER", "Пользователь вошел в Хаб преподавателя"
    )

    sections = hub_manager.get_main_sections()

    keyboard = []

    for section in sections:
        keyboard.append([InlineKeyboardButton(
            section["title"], callback_data=section["id"])])

    if user.id in BOT_CONFIG['admin_ids']:
        keyboard.append([InlineKeyboardButton(
            "🔐 Управление доступом", callback_data="access_manage")])

    keyboard.append([InlineKeyboardButton(
        "🔄 Главное меню", callback_data="back_to_main_menu")])

    reply_markup = InlineKeyboardMarkup(keyboard)

    text = "🎯 *Хаб преподавателя*\n\nВыберите раздел для получения информации:"

    if update.callback_query:
        await update.callback_query.message.edit_text(text, reply_markup=reply_markup, parse_mode='Markdown')
    else:
        await update.message.reply_text(text, reply_markup=reply_markup, parse_mode='Markdown')


async def manage_access(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Команда для управления доступом к хабу (только для админов)"""
    user = update.effective_user

    if user.id not in BOT_CONFIG['admin_ids']:
        if update.callback_query:
            await update.callback_query.message.edit_text("❌ У вас нет прав для управления доступом.")
        else:
            await update.message.reply_text("❌ У вас нет прав для управления доступом.")
        return

    keyboard = [
        [InlineKeyboardButton("🔗 Создать ссылку доступа",
                              callback_data="access_create_invite")],
        [InlineKeyboardButton(
            "👥 Список пользователей с доступом", callback_data="access_list")],
        [InlineKeyboardButton("➖ Отозвать доступ по ID",
                              callback_data="access_revoke")],
        [InlineKeyboardButton(
            "◀️ Назад к хабу", callback_data="back_to_teacher_hub")],
        [InlineKeyboardButton(
            "🔄 Главное меню", callback_data="back_to_main_menu")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)

    if update.callback_query:
        await update.callback_query.message.edit_text(
            "🔐 *Управление доступом к Хабу преподавателя*\n\n"
            "Выберите действие:",
            reply_markup=reply_markup,
            parse_mode='Markdown'
        )
    else:
        await update.message.reply_text(
            "🔐 *Управление доступом к Хабу преподавателя*\n\n"
            "Выберите действие:",
            reply_markup=reply_markup,
            parse_mode='Markdown'
        )


def user_has_bot_access(user_id: int) -> bool:
    """Return whether a Telegram user may use bot features."""
    return user_id in BOT_CONFIG['admin_ids'] or access_manager.has_access(user_id)


async def show_access_required(update: Update, invalid_invite: bool = False) -> None:
    """Show a uniform access-denied screen for commands, messages, and callbacks."""
    if invalid_invite:
        text = (
            "❌ Эта ссылка доступа недействительна или уже использована.\n\n"
            "Попросите администратора создать новую ссылку."
        )
    else:
        text = (
            "🔒 Доступ к боту закрыт.\n\n"
            "Чтобы начать работу, откройте персональную ссылку, полученную от администратора."
        )

    if update.callback_query:
        await update.callback_query.answer("Нет доступа", show_alert=True)
        await update.callback_query.message.edit_text(text)
    else:
        await update.effective_message.reply_text(text)


async def handle_teacher_hub_navigation(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обрабатывает всю навигацию в Хабе преподавателя"""
    query = update.callback_query
    await query.answer()

    if query.data == "back_to_teacher_hub":
        await show_teacher_hub(update, context)
        return

    section = hub_manager.get_section_by_id(query.data)

    if not section:
        await show_error_message(update, f"Раздел {query.data} не найден")
        return

    if query.data.startswith("contact_"):
        activity_tracker.log_action(
            update, "TEACHER_HUB_CONTACT",
            f"Открыт контакт: {section.get('title', query.data)}"
        )
    else:
        activity_tracker.log_action(
            update, "TEACHER_HUB_SECTION",
            f"Открыт раздел: {section.get('title', query.data)}"
        )

    buttons = section.get("buttons", [])
    reply_markup = hub_manager.create_keyboard(
        buttons,
        include_back_to_hub=True,
        include_main_menu=False
    )

    text = f"*{section['title']}*\n\n{section['text']}"

    await query.message.edit_text(
        text,
        reply_markup=reply_markup,
        parse_mode='Markdown'
    )


async def handle_repetition_mode(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Обработка режима повторения урока"""
    query = update.callback_query
    await query.answer()

    course_name = context.user_data['selected_course']
    user_id = update.effective_user.id

    current_offset, repetition_count = activity_tracker.get_user_offset(
        user_id, course_name)
    new_repetition_count = repetition_count + 1
    new_offset = current_offset + 1

    activity_tracker.set_user_offset(
        user_id, course_name, new_offset, new_repetition_count)

    context.user_data['current_offset'] = new_offset
    context.user_data['repetition_count'] = new_repetition_count
    context.user_data['is_repetition_mode'] = True
    context.user_data['feedback_view_mode'] = 'regular'

    activity_tracker.log_action(
        update, "РЕЖИМ_ПОВТОРЕНИЯ",
        f"Включен режим повторения. Счетчик: {new_repetition_count}, оффсет: {new_offset}",
        course_name=course_name
    )

    lessons = context.user_data['lessons']
    await show_lesson_navigation(update, context, course_name, lessons, 0)


async def handle_new_lesson_mode(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Обработка режима нового урока"""
    query = update.callback_query
    await query.answer()

    course_name = context.user_data['selected_course']
    context.user_data['is_repetition_mode'] = False
    context.user_data['feedback_view_mode'] = 'regular'

    lessons = context.user_data['lessons']
    await show_lesson_navigation(update, context, course_name, lessons, 0)


async def show_offset_settings(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Показывает настройки смещения"""
    query = update.callback_query
    await query.answer()

    course_name = context.user_data['selected_course']
    lessons = context.user_data['lessons']
    current_offset = context.user_data.get('current_offset', 0)
    total_lessons = len(lessons)

    keyboard = []

    negative_row = []
    for offset in range(-total_lessons, 0):
        if offset == current_offset:
            button_text = f"•{offset}•"
        else:
            button_text = f"{offset}"

        negative_row.append(InlineKeyboardButton(
            button_text, callback_data=f"set_offset_{offset}"))

        if len(negative_row) == 7:
            keyboard.append(negative_row)
            negative_row = []

    if negative_row:
        keyboard.append(negative_row)

    zero_button_text = f"•0•" if current_offset == 0 else "0"
    keyboard.append([InlineKeyboardButton(
        zero_button_text, callback_data="set_offset_0")])

    positive_row = []
    for offset in range(1, total_lessons + 1):
        if offset == current_offset:
            button_text = f"•{offset}•"
        else:
            button_text = f"{offset}"

        positive_row.append(InlineKeyboardButton(
            button_text, callback_data=f"set_offset_{offset}"))

        if len(positive_row) == 7:
            keyboard.append(positive_row)
            positive_row = []

    if positive_row:
        keyboard.append(positive_row)

    keyboard.append([
        InlineKeyboardButton("❌ Сбросить оффсет",
                             callback_data="reset_offset"),
        InlineKeyboardButton("↩️ Назад к уроку",
                             callback_data="back_to_lesson_nav")
    ])

    reply_markup = InlineKeyboardMarkup(keyboard)

    text = (
        f"⚙️ Настройка номера урока\n\n"
        f"Курс: {course_name}\n"
        f"Уроков в курсе: {total_lessons}\n"
        f"Текущее смещение: {current_offset}\n"
        f"Фактический номер урока будет: выбранный_урок + {current_offset}\n\n"
        f"Выберите новое значение смещения (от -{total_lessons} до +{total_lessons}):"
    )

    await query.message.edit_text(
        text,
        reply_markup=reply_markup
    )


async def handle_reset_offset(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Обрабатывает сброс оффсета"""
    query = update.callback_query
    await query.answer()

    user_id = update.effective_user.id
    course_name = context.user_data['selected_course']
    current_offset = context.user_data.get('current_offset', 0)

    if current_offset != 0:
        activity_tracker.set_user_offset(user_id, course_name, 0, 0)
        context.user_data['current_offset'] = 0
        context.user_data['repetition_count'] = 0
        context.user_data['is_repetition_mode'] = False

        await show_offset_settings(update, context)

        activity_tracker.log_action(
            update, "СБРОС_ОФФСЕТА",
            f"Сброшен оффсет с {current_offset} на 0",
            course_name=course_name
        )
    else:
        await query.answer("Оффсет уже сброшен", show_alert=False)


async def show_error_message(update: Update, error_text: str):
    """Показывает сообщение об ошибке"""
    keyboard = [
        [InlineKeyboardButton(
            "◀️ Назад к хабу", callback_data="back_to_teacher_hub")],
        [InlineKeyboardButton(
            "🔄 Главное меню", callback_data="back_to_main_menu")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)

    if update.callback_query:
        await update.callback_query.message.edit_text(
            f"❌ {error_text}",
            reply_markup=reply_markup
        )
    else:
        await update.message.reply_text(
            f"❌ {error_text}",
            reply_markup=reply_markup
        )


async def handle_back_to_lesson_nav(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Возвращает к навигации по урокам"""
    query = update.callback_query
    await query.answer()

    course_name = context.user_data['selected_course']
    lessons = context.user_data['lessons']
    current_index = context.user_data.get('current_lesson_index', 0)

    await show_lesson_navigation(update, context, course_name, lessons, current_index)


async def show_access_list(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Показывает список пользователей с доступом"""
    query = update.callback_query
    await query.answer()

    users = access_manager.get_access_list()

    if not users:
        text = "👥 *Список пользователей с доступом*\n\nНет пользователей с доступом."
    else:
        text = "👥 *Список пользователей с доступом*\n\n"
        for i, user_data in enumerate(users, 1):
            user_id = user_data['user_id']
            username = user_data['username']
            first_name = user_data['first_name']
            last_name = user_data['last_name']
            granted_at = user_data['granted_at']
            source = user_data.get('source', 'unknown')

            if username and username != 'unknown' and username != 'config_user':
                display_name = f"@{username}"
            else:
                display_name = f"ID: {user_id}"

            if first_name and first_name != 'Из':
                display_name += f" ({first_name}"
                if last_name and last_name != 'config':
                    display_name += f" {last_name}"
                display_name += ")"

            text += f"{i}. {display_name}\n"
            text += f"   🆔 ID: `{user_id}`\n"

            if source == 'config':
                text += f"   📌 Источник: системный доступ\n"
            else:
                text += f"   📅 Доступ предоставлен: {granted_at[:16]}\n"

            text += "\n"

        text += f"*Всего пользователей с доступом: {len(users)}*"

    keyboard = [
        [InlineKeyboardButton("🔗 Создать ссылку доступа",
                              callback_data="access_create_invite")],
        [InlineKeyboardButton("➖ Отозвать доступ",
                              callback_data="access_revoke")],
        [InlineKeyboardButton("🔐 Управление доступом",
                              callback_data="access_manage")],
        [InlineKeyboardButton(
            "◀️ Назад к хабу", callback_data="back_to_teacher_hub")],
        [InlineKeyboardButton(
            "🔄 Главное меню", callback_data="back_to_main_menu")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)

    await query.message.edit_text(text, reply_markup=reply_markup, parse_mode='Markdown')


async def create_access_invite(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Create a one-time deep link that grants access to its first user."""
    query = update.callback_query
    await query.answer()

    admin_user = update.effective_user
    if admin_user.id not in BOT_CONFIG['admin_ids']:
        await query.message.edit_text("❌ У вас нет прав для создания ссылок доступа.")
        return

    try:
        invite_token = access_manager.create_invite(admin_user.id)
        bot_username = context.bot.username
        if not bot_username:
            bot_username = (await context.bot.get_me()).username
        invite_link = f"https://t.me/{bot_username}?start=access_{invite_token}"
    except Exception:
        logger.exception("Failed to create an access invite")
        await query.message.edit_text(
            "❌ Не удалось создать ссылку. Попробуйте ещё раз."
        )
        return

    keyboard = [
        [InlineKeyboardButton("🔗 Создать новую ссылку",
                              callback_data="access_create_invite")],
        [InlineKeyboardButton("👥 Пользователи с доступом",
                              callback_data="access_list")],
        [InlineKeyboardButton("◀️ Управление доступом",
                              callback_data="access_manage")],
    ]
    await query.message.edit_text(
        "✅ Одноразовая ссылка доступа создана.\n\n"
        f"{invite_link}\n\n"
        "Перешлите её сотруднику. Первый человек, который откроет ссылку, "
        "получит доступ к боту; повторно использовать её нельзя.",
        reply_markup=InlineKeyboardMarkup(keyboard),
    )

    activity_tracker.log_action(
        update,
        "ACCESS_INVITE_CREATED",
        "Создана одноразовая ссылка доступа",
        course_name="SYSTEM",
    )


async def request_grant_access(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Запрашивает ID пользователя для предоставления доступа"""
    query = update.callback_query
    await query.answer()

    await query.message.edit_text(
        "➕ *Предоставление доступа*\n\n"
        "Введите ID пользователя, которому нужно предоставить доступ:",
        parse_mode='Markdown'
    )
    context.user_data['awaiting_grant_access'] = True


async def handle_grant_access(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обрабатывает предоставление доступа"""
    user_input = update.message.text.strip()
    admin_user = update.effective_user
    context.user_data['awaiting_grant_access'] = False

    try:
        user_id = int(user_input)

        if access_manager.has_access(user_id):
            users = access_manager.get_access_list()
            existing_user = next(
                (u for u in users if u['user_id'] == user_id), None)

            if existing_user:
                username = existing_user.get('username', 'unknown')
                if username and username not in ['unknown', 'config_user']:
                    user_display = f"@{username}"
                else:
                    user_display = f"пользователь с ID `{user_id}`"

                await update.message.reply_text(
                    f"ℹ️ {user_display} уже имеет доступ к Хабу преподавателя",
                    parse_mode='Markdown'
                )
            else:
                await update.message.reply_text(
                    f"ℹ️ Пользователь с ID `{user_id}` уже имеет доступ",
                    parse_mode='Markdown'
                )
            return

        success = access_manager.grant_access(
            user_id=user_id,
            username="unknown",
            first_name="",
            last_name="",
            granted_by=admin_user.id
        )

        if success:
            keyboard = [
                [InlineKeyboardButton(
                    "👥 Посмотреть список", callback_data="access_list")],
                [InlineKeyboardButton(
                    "➕ Добавить ещё", callback_data="access_grant")],
                [InlineKeyboardButton(
                    "🔐 Управление доступом", callback_data="access_manage")]
            ]
            reply_markup = InlineKeyboardMarkup(keyboard)

            await update.message.reply_text(
                f"✅ *Доступ успешно предоставлен!*\n\n"
                f"Пользователь с ID `{user_id}` теперь имеет доступ к Хабу преподавателя.\n\n"
                f"*Что дальше?*",
                reply_markup=reply_markup,
                parse_mode='Markdown'
            )

            activity_tracker.log_action(
                update, "ACCESS_GRANTED",
                f"Предоставлен доступ пользователю {user_id}",
                course_name="SYSTEM"
            )
        else:
            await update.message.reply_text(
                "❌ *Ошибка при предоставлении доступа*\n\n"
                "Попробуйте еще раз или обратитесь к разработчику.",
                parse_mode='Markdown'
            )

    except ValueError:
        await update.message.reply_text(
            "❌ *Неверный формат ID*\n\n"
            "Введите числовой ID пользователя. Пример: `123456789`",
            parse_mode='Markdown'
        )


async def request_revoke_access(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Запрашивает ID пользователя для отзыва доступа"""
    query = update.callback_query
    await query.answer()

    await query.message.edit_text(
        "➖ *Отзыв доступа*\n\n"
        "Введите ID пользователя, у которого нужно отозвать доступ:",
        parse_mode='Markdown'
    )
    context.user_data['awaiting_revoke_access'] = True


async def handle_revoke_access(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обрабатывает отзыв доступа"""
    user_input = update.message.text.strip()
    admin_user = update.effective_user
    context.user_data['awaiting_revoke_access'] = False

    try:
        user_id = int(user_input)

        if not access_manager.has_access(user_id):
            await update.message.reply_text(
                f"❌ У пользователя с ID `{user_id}` нет доступа к Хабу преподавателя",
                parse_mode='Markdown'
            )
            return

        success = access_manager.revoke_access(user_id)

        if success:
            keyboard = [
                [InlineKeyboardButton(
                    "👥 Посмотреть список", callback_data="access_list")],
                [InlineKeyboardButton(
                    "➖ Отозвать ещё", callback_data="access_revoke")],
                [InlineKeyboardButton(
                    "🔐 Управление доступом", callback_data="access_manage")]
            ]
            reply_markup = InlineKeyboardMarkup(keyboard)

            await update.message.reply_text(
                f"✅ *Доступ успешно отозван!*\n\n"
                f"Пользователь с ID `{user_id}` больше не имеет доступа к Хабу преподавателя.",
                reply_markup=reply_markup,
                parse_mode='Markdown'
            )

            activity_tracker.log_action(
                update, "ACCESS_REVOKED",
                f"Отозван доступ у пользователя {user_id}",
                course_name="SYSTEM"
            )
        else:
            await update.message.reply_text(
                "❌ *Ошибка при отзыве доступа*\n\n"
                "Попробуйте еще раз или обратитесь к разработчику.",
                parse_mode='Markdown'
            )

    except ValueError:
        await update.message.reply_text(
            "❌ *Неверный формат ID*\n\n"
            "Введите числовой ID пользователя. Пример: `123456789`",
            parse_mode='Markdown'
        )


async def handle_my_work_file(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Показывает меню управления рабочим файлом пользователя"""
    query = update.callback_query
    await query.answer()

    user_id = update.effective_user.id

    current_work_file = access_manager.get_work_file(user_id)

    if current_work_file:
        display_url = current_work_file
        if len(current_work_file) > 40:
            display_url = current_work_file[:37] + "..."

        text = (
            "📊 *Ваш рабочий файл*\n\n"
            f"🔗 Текущая ссылка:\n`{display_url}`\n\n"
            "Вы можете открыть файл или изменить ссылку:"
        )

        keyboard = [
            [InlineKeyboardButton("🔗 Открыть рабочий файл",
                                  url=current_work_file)],
            [InlineKeyboardButton("✏️ Изменить ссылку",
                                  callback_data="work_file_edit")],
            [InlineKeyboardButton("🗑️ Удалить ссылку",
                                  callback_data="work_file_delete")],
            [InlineKeyboardButton("◀️ Назад к таблицам",
                                  callback_data="hub_tables_docs")],
            [InlineKeyboardButton(
                "◀️ Назад к хабу", callback_data="back_to_teacher_hub")]
        ]
    else:
        text = (
            "📊 *Мой рабочий файл*\n\n"
            "У вас еще не настроен персональный рабочий файл.\n\n"
            "Вы можете добавить ссылку на ваш Google Sheets или другой документ, "
            "чтобы быстро открывать его прямо из бота."
        )

        keyboard = [
            [InlineKeyboardButton("➕ Добавить рабочий файл",
                                  callback_data="work_file_add")],
            [InlineKeyboardButton("◀️ Назад к таблицам",
                                  callback_data="hub_tables_docs")],
            [InlineKeyboardButton(
                "◀️ Назад к хабу", callback_data="back_to_teacher_hub")]
        ]

    reply_markup = InlineKeyboardMarkup(keyboard)

    await query.message.edit_text(
        text,
        reply_markup=reply_markup,
        parse_mode='Markdown'
    )


async def request_work_file_url(update: Update, context: ContextTypes.DEFAULT_TYPE, edit_mode=False):
    """Запрашивает ссылку на рабочий файл у пользователя"""
    query = update.callback_query
    await query.answer()

    action_text = "изменить" if edit_mode else "добавить"

    text = (
        f"🔗 *{action_text.capitalize()} рабочий файл*\n\n"
        "Пожалуйста, отправьте ссылку на ваш рабочий файл.\n\n"
        "*Пример:*\n"
        "`https://docs.google.com/spreadsheets/d/...`\n\n"
        "Ссылка должна начинаться с http:// или https://"
    )

    keyboard = [
        [InlineKeyboardButton("◀️ Отмена", callback_data="my_work_file")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)

    try:
        await query.message.edit_text(
            text,
            reply_markup=reply_markup,
            parse_mode='Markdown'
        )
    except Exception as e:
        logger.error(f"Ошибка при редактировании сообщения: {e}")
        await query.message.reply_text(
            text,
            reply_markup=reply_markup,
            parse_mode='Markdown'
        )

    context.user_data['awaiting_work_file_url'] = True
    context.user_data['work_file_edit_mode'] = edit_mode


async def handle_work_file_url_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обрабатывает ввод ссылки на рабочий файл"""
    if not context.user_data.get('awaiting_work_file_url'):
        return

    work_file_url = update.message.text.strip()
    user_id = update.effective_user.id
    edit_mode = context.user_data.get('work_file_edit_mode', False)

    context.user_data['awaiting_work_file_url'] = False
    context.user_data['work_file_edit_mode'] = False

    if not (work_file_url.startswith('http://') or work_file_url.startswith('https://')):
        await update.message.reply_text(
            "❌ *Неверный формат ссылки!*\n\n"
            "Ссылка должна начинаться с http:// или https://\n\n"
            "Пожалуйста, попробуйте еще раз:",
            parse_mode='Markdown'
        )
        return

    success = access_manager.set_work_file(user_id, work_file_url)

    if success:
        action_text = "обновлен" if edit_mode else "добавлен"

        activity_tracker.log_action(
            update,
            "WORK_FILE_UPDATED" if edit_mode else "WORK_FILE_ADDED",
            f"Рабочий файл {action_text}",
            course_name="TEACHER_HUB"
        )

        display_url = work_file_url
        if len(work_file_url) > 50:
            display_url = work_file_url[:47] + "..."

        keyboard = [
            [InlineKeyboardButton(
                "🔗 Открыть рабочий файл", url=work_file_url)],
            [InlineKeyboardButton("✏️ Изменить ссылку",
                                  callback_data="work_file_edit")],
            [InlineKeyboardButton("📊 Мой рабочий файл",
                                  callback_data="my_work_file")],
            [InlineKeyboardButton(
                "◀️ Назад к хабу", callback_data="back_to_teacher_hub")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)

        success_message = (
            f"✅ *Рабочий файл успешно {action_text}!*\n\n"
            f"🔗 Ссылка: `{display_url}`\n\n"
            "Теперь вы можете быстро открывать его из меню бота."
        )

        await update.message.reply_text(
            success_message,
            reply_markup=reply_markup,
            parse_mode='Markdown'
        )
    else:
        await update.message.reply_text(
            "❌ *Ошибка при сохранении ссылки!*\n\n"
            "Пожалуйста, попробуйте позже или обратитесь к администратору.",
            parse_mode='Markdown'
        )


async def handle_delete_work_file(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обрабатывает удаление рабочего файла"""
    query = update.callback_query
    await query.answer()

    user_id = update.effective_user.id

    keyboard = [
        [InlineKeyboardButton(
            "✅ Да, удалить", callback_data="work_file_delete_confirm")],
        [InlineKeyboardButton("❌ Отмена", callback_data="my_work_file")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)

    await query.message.edit_text(
        "🗑️ *Удаление рабочего файла*\n\n"
        "Вы уверены, что хотите удалить ссылку на ваш рабочий файл?\n\n"
        "После удаления вам нужно будет снова добавить ссылку для использования этой функции.",
        reply_markup=reply_markup,
        parse_mode='Markdown'
    )


async def handle_delete_work_file_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Подтверждение удаления рабочего файла"""
    query = update.callback_query
    await query.answer()

    user_id = update.effective_user.id

    success = access_manager.delete_work_file(user_id)

    if success:
        activity_tracker.log_action(
            update,
            "WORK_FILE_DELETED",
            "Рабочий файл удален",
            course_name="TEACHER_HUB"
        )

        keyboard = [
            [InlineKeyboardButton("➕ Добавить рабочий файл",
                                  callback_data="work_file_add")],
            [InlineKeyboardButton(
                "◀️ Назад к хабу", callback_data="back_to_teacher_hub")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)

        await query.message.edit_text(
            "✅ *Рабочий файл успешно удален!*\n\n"
            "Вы можете добавить новую ссылку в любое время.",
            reply_markup=reply_markup,
            parse_mode='Markdown'
        )
    else:
        keyboard = [
            [InlineKeyboardButton("📊 Мой рабочий файл",
                                  callback_data="my_work_file")],
            [InlineKeyboardButton(
                "◀️ Назад к хабу", callback_data="back_to_teacher_hub")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)

        await query.message.edit_text(
            "❌ *Ошибка при удалении рабочего файла!*\n\n"
            "Пожалуйста, попробуйте позже или обратитесь к администратору.",
            reply_markup=reply_markup,
            parse_mode='Markdown'
        )


async def handle_callback_general_routes(update: Update, context: ContextTypes.DEFAULT_TYPE, data: str) -> bool:
    if data == "back_to_main_menu":
        await show_main_menu(update, context)
        return True
    if data == "mode_feedback":
        await show_courses(update, context)
        return True
    return False


async def handle_callback_teacher_hub_routes(update: Update, context: ContextTypes.DEFAULT_TYPE, data: str) -> bool:
    if data == "mode_teacher_hub" or data == "back_to_teacher_hub":
        await show_teacher_hub(update, context)
        return True

    if data.startswith("hub_") or data == "hub_other_resources":
        await handle_teacher_hub_navigation(update, context)
        return True

    if data in ["contact_admin", "contact_support_bot", "url_online_rooms", "url_algovscode"]:
        await handle_teacher_hub_navigation(update, context)
        return True

    if data.startswith("url_"):
        activity_tracker.log_action(
            update, "TEACHER_HUB_URL",
            f"Переход по ссылке: {data}"
        )
        return True

    return False


async def handle_callback_access_routes(update: Update, context: ContextTypes.DEFAULT_TYPE, data: str) -> bool:
    if data == "access_manage":
        await manage_access(update, context)
        return True
    if data == "access_list":
        await show_access_list(update, context)
        return True
    if data == "access_create_invite":
        await create_access_invite(update, context)
        return True
    if data == "access_grant":
        await request_grant_access(update, context)
        return True
    if data == "access_revoke":
        await request_revoke_access(update, context)
        return True
    return False


async def handle_callback_auto_feedback_routes(update: Update, context: ContextTypes.DEFAULT_TYPE, data: str) -> bool:
    query = update.callback_query

    if data == "auto_feedback_menu":
        await show_auto_feedback_menu(update, context)
        return True
    if data == "af_add_group_start":
        await start_add_group_wizard(update, context)
        return True
    if data == "af_course_page_noop":
        return True
    if data.startswith("af_course_page_"):
        wizard = context.user_data.get('af_wizard')
        if not wizard or wizard.get("step") != "course_pick":
            await query.message.edit_text("❌ Сессия настройки истекла. Начните заново.")
            return True
        page = int(data.replace("af_course_page_", ""))
        await query.message.edit_text(
            "Выберите курс из встроенной клавиатуры:",
            reply_markup=build_course_picker_keyboard(page=page)
        )
        return True
    if data.startswith("af_course_pick_"):
        wizard = context.user_data.get('af_wizard')
        if not wizard or wizard.get("step") != "course_pick":
            await query.message.edit_text("❌ Сессия настройки истекла. Начните заново.")
            return True
        idx = int(data.replace("af_course_pick_", ""))
        course_names = list(COURSES.keys())
        if idx < 0 or idx >= len(course_names):
            await query.message.edit_text("❌ Курс не найден.")
            return True
        wizard["data"]["course_name"] = course_names[idx]
        wizard["step"] = "lesson_offset"
        await query.message.edit_text(
            f"✅ Курс выбран: {course_names[idx]}\n\nВведите смещение номера урока (например: 0, 1, -1):"
        )
        return True
    if data.startswith("af_group_"):
        group_id = int(data.replace("af_group_", ""))
        await show_group_details(update, context, group_id)
        return True
    if data.startswith("af_delete_confirm_"):
        group_id = int(data.replace("af_delete_confirm_", ""))
        group = teacher_group_manager.get_group(group_id)
        if group and group["teacher_id"] == update.effective_user.id:
            teacher_group_manager.delete_group(group_id)
            await log_auto_feedback_csv(
                action="auto_feedback_group_deleted",
                group_row=group,
                lesson_date_str="",
                manual_trigger=True,
                status="deleted",
                details="",
                triggered_by_user_id=update.effective_user.id,
                triggered_by_username=update.effective_user.username or "",
                triggered_by_name=_get_user_full_name(update.effective_user),
                source="ui_button",
                actor_role="teacher",
                actor_chat_id=update.effective_chat.id,
            )
        await show_auto_feedback_menu(update, context)
        return True
    if data.startswith("af_delete_cancel_"):
        group_id = int(data.replace("af_delete_cancel_", ""))
        group = teacher_group_manager.get_group(group_id)
        if group and group["teacher_id"] == update.effective_user.id:
            await log_auto_feedback_csv(
                action="auto_feedback_group_delete_cancelled",
                group_row=group,
                lesson_date_str="",
                manual_trigger=True,
                status="cancelled",
                details="delete_cancelled_by_user",
                triggered_by_user_id=update.effective_user.id,
                triggered_by_username=update.effective_user.username or "",
                triggered_by_name=_get_user_full_name(update.effective_user),
                source="ui_button",
                actor_role="teacher",
                actor_chat_id=update.effective_chat.id,
            )
            await show_group_details(update, context, group_id)
        else:
            await show_auto_feedback_menu(update, context)
        return True
    if data.startswith("af_delete_"):
        group_id = int(data.replace("af_delete_", ""))
        group = teacher_group_manager.get_group(group_id)
        if not group or group["teacher_id"] != update.effective_user.id:
            await query.message.edit_text("❌ Группа не найдена.")
            return True
        await query.message.edit_text(
            f"🗑 Удалить группу «{group['group_name']}»?\nЭто действие нельзя отменить.",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("✅ Да, удалить", callback_data=f"af_delete_confirm_{group_id}")],
                [InlineKeyboardButton("❌ Отмена", callback_data=f"af_delete_cancel_{group_id}")],
                [InlineKeyboardButton("◀️ К группе", callback_data=f"af_group_{group_id}")]
            ])
        )
        return True
    if data.startswith("af_toggle_"):
        group_id = int(data.replace("af_toggle_", ""))
        group = teacher_group_manager.get_group(group_id)
        if group and group["teacher_id"] == update.effective_user.id:
            old_state = int(group["is_active"])
            new_state = 0 if old_state else 1
            teacher_group_manager.update_group_field(group_id, "is_active", new_state)
            updated_group = teacher_group_manager.get_group(group_id)
            await log_auto_feedback_csv(
                action="auto_feedback_group_updated",
                group_row=updated_group,
                lesson_date_str="",
                manual_trigger=True,
                status="is_active_set",
                details=f"field=is_active; old={old_state}; new={new_state}",
                triggered_by_user_id=update.effective_user.id,
                triggered_by_username=update.effective_user.username or "",
                triggered_by_name=_get_user_full_name(update.effective_user),
                source="ui_button",
                actor_role="teacher",
                actor_chat_id=update.effective_chat.id,
            )
        await show_group_details(update, context, group_id)
        return True
    if data.startswith("af_next_lesson_"):
        group_id = int(data.replace("af_next_lesson_", ""))
        group = teacher_group_manager.get_group(group_id)
        if group and group["teacher_id"] == update.effective_user.id:
            old_lesson_number = int(group["current_lesson_number"])
            total_lessons = len(get_lessons_in_order(group["course_name"]))
            max_lesson_number = max(1, total_lessons)
            if old_lesson_number < max_lesson_number:
                new_lesson_number = old_lesson_number + 1
                teacher_group_manager.update_group_field(
                    group_id,
                    "current_lesson_number",
                    new_lesson_number
                )
                updated_group = teacher_group_manager.get_group(group_id)
                await log_auto_feedback_csv(
                    action="auto_feedback_group_updated",
                    group_row=updated_group,
                    lesson_date_str="",
                    manual_trigger=True,
                    status="lesson_number_incremented",
                    details=f"field=current_lesson_number; old={old_lesson_number}; new={new_lesson_number}",
                    triggered_by_user_id=update.effective_user.id,
                    triggered_by_username=update.effective_user.username or "",
                    triggered_by_name=_get_user_full_name(update.effective_user),
                    source="ui_button",
                    actor_role="teacher",
                    actor_chat_id=update.effective_chat.id,
                )
            else:
                await log_auto_feedback_csv(
                    action="auto_feedback_group_update_blocked",
                    group_row=group,
                    lesson_date_str="",
                    manual_trigger=True,
                    status="lesson_number_max_reached",
                    details=f"field=current_lesson_number; old={old_lesson_number}; max={max_lesson_number}",
                    triggered_by_user_id=update.effective_user.id,
                    triggered_by_username=update.effective_user.username or "",
                    triggered_by_name=_get_user_full_name(update.effective_user),
                    source="ui_button",
                    actor_role="teacher",
                    actor_chat_id=update.effective_chat.id,
                )
                await query.message.reply_text(
                    f"⚠️ Уже установлен последний урок для курса: {max_lesson_number}."
                )
        await show_group_details(update, context, group_id)
        return True
    if data.startswith("af_set_lesson_"):
        group_id = int(data.replace("af_set_lesson_", ""))
        context.user_data['af_wizard'] = {
            "step": "set_lesson_manual",
            "data": {"group_id": group_id}
        }
        await query.message.edit_text(
            "Введите номер последнего прошедшего занятия "
            "(например: 0 если занятий еще не было, 5 если последним был урок №5):"
        )
        return True
    if data.startswith("af_set_offset_"):
        group_id = int(data.replace("af_set_offset_", ""))
        context.user_data['af_wizard'] = {
            "step": "set_offset_manual",
            "data": {"group_id": group_id}
        }
        await query.message.edit_text("Введите новое смещение номера урока (целое число):")
        return True
    if data.startswith("af_send_now_"):
        group_id = int(data.replace("af_send_now_", ""))
        group = teacher_group_manager.get_group(group_id)
        if not group or group["teacher_id"] != update.effective_user.id:
            await query.message.edit_text("❌ Группа не найдена.")
            return True
        lesson_date = teacher_group_manager.get_weekly_feedback_date_for_group(group)
        send_ok, sent_message_ids = await send_auto_feedback_for_group(
            context.application,
            group,
            lesson_date,
            manual_trigger=True,
            triggered_by_user_id=update.effective_user.id,
            triggered_by_username=update.effective_user.username or "",
            triggered_by_name=_get_user_full_name(update.effective_user),
            return_message_ids=True,
            source="ui_button",
            actor_role="teacher",
            actor_chat_id=update.effective_chat.id,
        )

        if send_ok and sent_message_ids:
            now_dt = datetime.now()
            cleanup_store = context.user_data.setdefault("af_test_cleanup", {})
            cleanup_store[group_id] = {
                "chat_id": group["teacher_chat_id"],
                "message_ids": sent_message_ids,
                "sent_at": now_dt.isoformat(timespec="seconds"),
                "expires_at": (now_dt + timedelta(hours=AUTO_FEEDBACK_TEST_CLEANUP_TTL_HOURS)).isoformat(timespec="seconds"),
            }

        result_text = "✅ Тестовая ОС отправлена в ваш чат." if send_ok else "⚠️ Не удалось полностью отправить тестовую ОС."
        keyboard = []
        if get_valid_test_cleanup_data(context, group_id):
            keyboard.append([InlineKeyboardButton("🧹 Очистить тестовую ОС", callback_data=f"af_clear_test_{group_id}")])
        keyboard.extend([
            [InlineKeyboardButton("◀️ Назад к группе", callback_data=f"af_group_{group_id}")],
            [InlineKeyboardButton("◀️ К списку групп", callback_data="auto_feedback_menu")]
        ])
        await query.message.edit_text(
            result_text,
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
        return True
    if data.startswith("af_clear_test_"):
        group_id = int(data.replace("af_clear_test_", ""))
        group = teacher_group_manager.get_group(group_id)
        if not group or group["teacher_id"] != update.effective_user.id:
            await query.message.edit_text("❌ Группа не найдена.")
            return True

        cleanup_store = context.user_data.setdefault("af_test_cleanup", {})
        raw_cleanup_data = cleanup_store.get(group_id)
        cleanup_data = get_valid_test_cleanup_data(context, group_id)
        if not cleanup_data:
            if raw_cleanup_data and raw_cleanup_data.get("expires_at"):
                await log_auto_feedback_csv(
                    action="auto_feedback_test_messages_cleared",
                    group_row=group,
                    lesson_date_str="",
                    manual_trigger=True,
                    status="expired",
                    details=(
                        f"sent_at={raw_cleanup_data.get('sent_at', '')}; "
                        f"expires_at={raw_cleanup_data.get('expires_at', '')}"
                    ),
                    triggered_by_user_id=update.effective_user.id,
                    triggered_by_username=update.effective_user.username or "",
                    triggered_by_name=_get_user_full_name(update.effective_user),
                    source="ui_button",
                    actor_role="teacher",
                    actor_chat_id=update.effective_chat.id,
                )
                await query.message.edit_text(
                    "ℹ️ Срок очистки тестовой ОС истек. Отправьте новый тест, если нужна кнопка очистки.",
                    reply_markup=InlineKeyboardMarkup([
                        [InlineKeyboardButton("◀️ Назад к группе", callback_data=f"af_group_{group_id}")],
                        [InlineKeyboardButton("◀️ К списку групп", callback_data="auto_feedback_menu")]
                    ])
                )
                return True
            await query.message.edit_text(
                "ℹ️ Нет сохраненных сообщений тестовой ОС для удаления.",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("◀️ Назад к группе", callback_data=f"af_group_{group_id}")],
                    [InlineKeyboardButton("◀️ К списку групп", callback_data="auto_feedback_menu")]
                ])
            )
            return True

        deleted_count = 0
        failed_count = 0
        chat_id = cleanup_data.get("chat_id", group["teacher_chat_id"])
        for message_id in cleanup_data.get("message_ids", []):
            result = await telegram_api_call(
                call_factory=lambda chat_id=chat_id, message_id=message_id: context.application.bot.delete_message(
                    chat_id=chat_id,
                    message_id=message_id,
                ),
                operation_name=f"delete_test_feedback_message_{message_id}",
                retries=2,
                timeout_sec=10.0,
            )
            if result:
                deleted_count += 1
            else:
                failed_count += 1

        cleanup_store.pop(group_id, None)
        await log_auto_feedback_csv(
            action="auto_feedback_test_messages_cleared",
            group_row=group,
            lesson_date_str="",
            manual_trigger=True,
            status="cleared",
            details=f"deleted={deleted_count}; failed={failed_count}",
            triggered_by_user_id=update.effective_user.id,
            triggered_by_username=update.effective_user.username or "",
            triggered_by_name=_get_user_full_name(update.effective_user),
            source="ui_button",
            actor_role="teacher",
            actor_chat_id=update.effective_chat.id,
        )

        await query.message.edit_text(
            f"🧹 Очистка завершена. Удалено: {deleted_count}, не удалено: {failed_count}.",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("◀️ Назад к группе", callback_data=f"af_group_{group_id}")],
                [InlineKeyboardButton("◀️ К списку групп", callback_data="auto_feedback_menu")]
            ])
        )
        return True
    if data.startswith("af_weekday_"):
        wizard = context.user_data.get('af_wizard')
        if not wizard or wizard.get("step") != "weekday":
            await query.message.edit_text("❌ Сессия настройки истекла. Начните заново.")
            return True
        weekday = int(data.replace("af_weekday_", ""))
        wizard["data"]["weekday"] = weekday
        wizard["step"] = "lesson_time"
        await query.message.edit_text(
            f"Выбран день: {WEEKDAY_NAMES[weekday]}\n\nВведите время занятия в формате ЧЧ:ММ:"
        )
        return True
    if data == "af_mode_group":
        wizard = context.user_data.get('af_wizard')
        if not wizard or wizard.get("step") != "lesson_mode":
            await query.message.edit_text("❌ Сессия настройки истекла. Начните заново.")
            return True
        wizard["data"]["lesson_mode"] = "group"
        wizard["step"] = "lesson_place"
        await query.message.edit_text(
            "Выберите формат проведения:",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("Онлайн", callback_data="af_place_online")],
                [InlineKeyboardButton("Очно", callback_data="af_place_offline")]
            ])
        )
        return True
    if data == "af_mode_individual":
        wizard = context.user_data.get('af_wizard')
        if not wizard or wizard.get("step") != "lesson_mode":
            await query.message.edit_text("❌ Сессия настройки истекла. Начните заново.")
            return True
        wizard["data"]["lesson_mode"] = "individual"
        wizard["step"] = "lesson_place"
        await query.message.edit_text(
            "Выберите формат проведения:",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("Онлайн", callback_data="af_place_online")],
                [InlineKeyboardButton("Очно", callback_data="af_place_offline")]
            ])
        )
        return True
    if data == "af_place_online":
        wizard = context.user_data.get('af_wizard')
        if not wizard or wizard.get("step") != "lesson_place":
            await query.message.edit_text("❌ Сессия настройки истекла. Начните заново.")
            return True
        wizard["data"]["lesson_place"] = "online"
        await finalize_add_group_wizard(update, context)
        return True
    if data == "af_place_offline":
        wizard = context.user_data.get('af_wizard')
        if not wizard or wizard.get("step") != "lesson_place":
            await query.message.edit_text("❌ Сессия настройки истекла. Начните заново.")
            return True
        wizard["data"]["lesson_place"] = "offline"
        await finalize_add_group_wizard(update, context)
        return True

    return False


async def handle_callback_learning_routes(update: Update, context: ContextTypes.DEFAULT_TYPE, data: str) -> bool:
    if data.startswith("course_"):
        await handle_course_selection(update, context)
        return True
    if data.startswith("lesson_"):
        await handle_lesson_selection(update, context)
        return True
    if data.startswith("date_"):
        await handle_date_selection(update, context)
        return True
    if data in ["next_lesson", "prev_lesson", "select_date", "back_to_lesson"]:
        await handle_navigation(update, context)
        return True
    if data == "back_to_courses":
        await change_course(update, context)
        return True
    if data == "back_to_lesson_nav":
        await handle_back_to_lesson_nav(update, context)
        return True
    if data == "add_absent":
        await handle_absent_students(update, context)
        return True
    if data == "clear_absent":
        await handle_clear_absent_students(update, context)
        return True
    if data == "online_individual":
        await handle_online_individual(update, context)
        return True
    if data == "back_to_regular":
        await handle_back_to_regular(update, context)
        return True
    if data == "new_lesson":
        await handle_new_lesson_mode(update, context)
        return True
    if data == "repetition_mode":
        await handle_repetition_mode(update, context)
        return True
    if data == "offset_settings":
        await show_offset_settings(update, context)
        return True
    if data.startswith("set_offset_"):
        offset_value = int(data.replace("set_offset_", ""))
        user_id = update.effective_user.id
        course_name = context.user_data['selected_course']
        activity_tracker.set_user_offset(
            user_id, course_name, offset_value)
        context.user_data['current_offset'] = offset_value
        await show_offset_settings(update, context)
        return True
    if data == "reset_offset":
        await handle_reset_offset(update, context)
        return True
    if data == "back_to_course_menu":
        await handle_course_selection(update, context)
        return True
    if data == "start_lesson":
        await handle_start_lesson(update, context)
        return True
    return False


async def handle_callback_stats_backup_routes(update: Update, context: ContextTypes.DEFAULT_TYPE, data: str) -> bool:
    if data == "stats_main":
        await stats_command(update, context)
        return True
    if data.startswith("stats_user_"):
        user_id = int(data.replace("stats_user_", ""))
        await show_user_stats(update, context, user_id=user_id)
        return True
    if data.startswith("stats_"):
        await handle_stats_navigation(update, context)
        return True
    if data == "backup_menu":
        await show_backup_menu(update, context)
        return True
    if data.startswith("backup_"):
        await handle_backup_management(update, context)
        return True
    return False


async def handle_callback_work_file_routes(update: Update, context: ContextTypes.DEFAULT_TYPE, data: str) -> bool:
    if data == "my_work_file":
        await handle_my_work_file(update, context)
        return True
    if data == "work_file_add":
        await request_work_file_url(update, context)
        return True
    if data == "work_file_edit":
        await request_work_file_url(update, context, edit_mode=True)
        return True
    if data == "work_file_delete":
        await handle_delete_work_file(update, context)
        return True
    if data == "work_file_back":
        await handle_my_work_file(update, context)
        return True
    if data == "work_file_delete_confirm":
        await handle_delete_work_file_confirm(update, context)
        return True
    return False


_callback_router: CallbackRouter | None = None


def get_callback_router() -> CallbackRouter:
    global _callback_router
    if _callback_router is not None:
        return _callback_router

    router = CallbackRouter()

    for key in ["back_to_main_menu", "mode_feedback"]:
        router.add_exact(key, handle_callback_general_routes)

    for key in [
        "mode_teacher_hub",
        "back_to_teacher_hub",
        "hub_other_resources",
        "contact_admin",
        "contact_support_bot",
        "url_online_rooms",
        "url_algovscode",
    ]:
        router.add_exact(key, handle_callback_teacher_hub_routes)
    router.add_prefix("hub_", handle_callback_teacher_hub_routes)
    router.add_prefix("url_", handle_callback_teacher_hub_routes)

    for key in [
        "access_manage",
        "access_list",
        "access_create_invite",
        "access_revoke",
    ]:
        router.add_exact(key, handle_callback_access_routes)

    for key in [
        "auto_feedback_menu",
        "af_add_group_start",
        "af_course_page_noop",
        "af_mode_group",
        "af_mode_individual",
        "af_place_online",
        "af_place_offline",
    ]:
        router.add_exact(key, handle_callback_auto_feedback_routes)
    for prefix in [
        "af_course_page_",
        "af_course_pick_",
        "af_group_",
        "af_delete_",
        "af_toggle_",
        "af_next_lesson_",
        "af_set_lesson_",
        "af_set_offset_",
        "af_send_now_",
        "af_clear_test_",
        "af_weekday_",
    ]:
        router.add_prefix(prefix, handle_callback_auto_feedback_routes)

    for key in [
        "next_lesson",
        "prev_lesson",
        "select_date",
        "back_to_lesson",
        "back_to_courses",
        "back_to_lesson_nav",
        "add_absent",
        "clear_absent",
        "online_individual",
        "back_to_regular",
        "new_lesson",
        "repetition_mode",
        "offset_settings",
        "reset_offset",
        "back_to_course_menu",
        "start_lesson",
    ]:
        router.add_exact(key, handle_callback_learning_routes)
    for prefix in ["course_", "lesson_", "date_", "set_offset_"]:
        router.add_prefix(prefix, handle_callback_learning_routes)

    for key in ["stats_main", "backup_menu"]:
        router.add_exact(key, handle_callback_stats_backup_routes)
    for prefix in ["stats_user_", "stats_", "backup_"]:
        router.add_prefix(prefix, handle_callback_stats_backup_routes)

    for key in [
        "my_work_file",
        "work_file_add",
        "work_file_edit",
        "work_file_delete",
        "work_file_back",
        "work_file_delete_confirm",
    ]:
        router.add_exact(key, handle_callback_work_file_routes)

    _callback_router = router
    return _callback_router


async def handle_callback_query(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик всех callback запросов"""
    query = update.callback_query
    data = query.data

    try:
        if not user_has_bot_access(update.effective_user.id):
            await show_access_required(update)
            return
        await query.answer()
        route_handler = get_callback_router().resolve(data)
        if route_handler:
            await route_handler(update, context, data)
            return
        await query.message.edit_text(
            build_error_text("Команда не распознана. Вернитесь в меню и повторите действие."),
            reply_markup=build_error_keyboard(menu_callback="back_to_main_menu"),
        )

    except Exception as e:
        logger.error(f"Ошибка в обработчике callback: {e}")
        logger.error(f"Трассировка ошибки: {traceback.format_exc()}")

        try:
            await query.message.edit_text(
                build_error_text("Если ошибка повторяется, нажмите «Главное меню»."),
                reply_markup=build_error_keyboard(menu_callback="back_to_main_menu")
            )
        except Exception as edit_error:
            logger.error(f"Не удалось редактировать сообщение: {edit_error}")
            await query.message.reply_text(
                build_error_text("Если ошибка повторяется, нажмите «Главное меню»."),
                reply_markup=build_error_keyboard(menu_callback="back_to_main_menu")
            )


async def handle_all_messages(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Обработчик всех текстовых сообщений."""
    if not user_has_bot_access(update.effective_user.id):
        await show_access_required(update)
        return

    if await handle_auto_feedback_text_input(update, context):
        return

    if context.user_data.get('awaiting_custom_date'):
        await handle_custom_date_input(update, context)
    elif context.user_data.get('awaiting_user_search'):
        await handle_stats_user_search(update, context)
    elif context.user_data.get('awaiting_date_stats'):
        await update.message.reply_text("Функция в разработке")
        context.user_data['awaiting_date_stats'] = False
    elif context.user_data.get('awaiting_absent_students'):
        await handle_absent_students_input(update, context)
    elif context.user_data.get('awaiting_grant_access'):
        await handle_grant_access(update, context)
    elif context.user_data.get('awaiting_revoke_access'):
        await handle_revoke_access(update, context)
    elif context.user_data.get('awaiting_work_file_url'):
        await handle_work_file_url_input(update, context)
    else:
        await show_main_menu(update, context)


def main() -> None:
    """Запуск бота"""
    application = Application.builder().token(BOT_CONFIG['token']).build()

    if application.job_queue and BOT_CONFIG.get('auto_feedback_enabled', False):
        application.job_queue.run_repeating(auto_feedback_scheduler, interval=60, first=30)
        logger.info("Автоматическая обратная связь включена")
    else:
        logger.info("Автоматическая обратная связь отключена")

    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("stats", stats_command))
    application.add_handler(CallbackQueryHandler(handle_callback_query))
    application.add_handler(MessageHandler(
        filters.TEXT & ~filters.COMMAND, handle_all_messages))
    application.add_handler(CommandHandler("access", manage_access))

    application.add_error_handler(error_handler)

    logger.info("Бот запущен с поддержкой Хаба преподавателя")
    application.run_polling()


async def error_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Глобальный обработчик ошибок"""
    logger.error(f"Exception while handling an update: {context.error}")
    if context.error:
        tb_text = "".join(
            traceback.format_exception(
                None, context.error, context.error.__traceback__)
        )
    else:
        tb_text = "No traceback available"
    logger.error("Traceback:\n%s", tb_text)

    try:
        if update and update.effective_message:
            await update.effective_message.reply_text(
                build_error_text("Попробуйте еще раз или начните заново с /start."),
                reply_markup=build_error_keyboard(menu_callback="back_to_main_menu")
            )
    except Exception as e:
        logger.error(f"Ошибка при отправке сообщения об ошибке: {e}")


if __name__ == '__main__':
    main()




