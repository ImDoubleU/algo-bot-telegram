# Algo Bot Telegram

Telegram-бот для работы преподавателей с группами, учебными материалами и обратной связью.

## Локальный запуск

Требуется Python 3.11 или новее.

```powershell
python -m venv bot_env
bot_env\Scripts\python.exe -m pip install -r requirements-telegram.txt
Copy-Item .env.telegram.example .env.telegram
```

Заполните `.env.telegram`, затем запустите бота:

```powershell
bot_env\Scripts\python.exe bot.py
```

Файл `.env.telegram`, рабочая SQLite-база, логи, резервные копии и выгрузки пользователей исключены из Git.

## Проверка

```powershell
bot_env\Scripts\python.exe -m unittest discover -s tests -v
bot_env\Scripts\python.exe scripts\preflight_telegram.py
```

## Развёртывание

Инструкция для Telegram-only сервиса находится в [deploy/linux/README_TELEGRAM_RU.md](deploy/linux/README_TELEGRAM_RU.md).
