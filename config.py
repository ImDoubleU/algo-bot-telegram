import os
from datetime import time


def _parse_admin_ids(raw_value):
    values = [value.strip() for value in raw_value.split(",") if value.strip()]
    try:
        return [int(value) for value in values]
    except ValueError as exc:
        raise RuntimeError("TELEGRAM_ADMIN_IDS must contain comma-separated integers") from exc


def require_admin_ids():
    raw_value = os.getenv("TELEGRAM_ADMIN_IDS", "").strip()
    if not raw_value:
        raise RuntimeError("TELEGRAM_ADMIN_IDS is required")
    admin_ids = _parse_admin_ids(raw_value)
    if not admin_ids:
        raise RuntimeError("TELEGRAM_ADMIN_IDS must contain at least one ID")
    return admin_ids


def require_telegram_token():
    token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    if not token:
        raise RuntimeError("TELEGRAM_BOT_TOKEN is required")
    return token


def _env_bool(name, default=False):
    raw_value = os.getenv(name)
    if raw_value is None:
        return default
    return raw_value.strip().lower() in {'1', 'true', 'yes', 'on'}

# Настройки резервного копирования
BACKUP_CONFIG = {
    'db_path': 'logs/bot_analytics.db',
    'backup_dir': 'backups',
    'export_dir': 'exports',
    'keep_months': 1,  # Сколько месяцев данных хранить в основной БД
    'keep_backups': 6,  # Сколько резервных копий хранить
    'maintenance_time': time(2, 0)  # Время выполнения обслуживания (2:00 ночи)
}

# Настройки бота
BOT_CONFIG = {
    'token': require_telegram_token(),
    'admin_ids': require_admin_ids(),
    'auto_feedback_enabled': _env_bool('AUTO_FEEDBACK_ENABLED', False),
    'auto_feedback_max_concurrent': int(os.getenv('AUTO_FEEDBACK_MAX_CONCURRENT', '5')),
    'auto_feedback_send_spacing_sec': float(os.getenv('AUTO_FEEDBACK_SEND_SPACING_SEC', '0.05')),
    'auto_feedback_test_cleanup_ttl_hours': int(os.getenv('AUTO_FEEDBACK_TEST_CLEANUP_TTL_HOURS', '2')),
}
