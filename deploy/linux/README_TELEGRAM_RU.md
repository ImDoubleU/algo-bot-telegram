# Развёртывание только Telegram-бота

Схема запуска: один процесс `bot.py` использует Telegram long polling. Webapp,
Nginx, публичные порты и отдельный worker не нужны. Автоматическая обратная связь
по умолчанию выключена. После проверки расписания её можно включить через
`AUTO_FEEDBACK_ENABLED=true` в `/etc/algo-bot/telegram.env` и перезапуск службы.

## Перед первым запуском

1. Отозвать старый токен у BotFather, потому что раньше он находился в исходнике
   и мог попасть в журналы.
2. Получить новый токен.
3. Создать локальный файл `telegram.env` по образцу `.env.telegram.example`.
   Этот файл нельзя добавлять в архив или Git.
4. Собрать пакет с текущей базой:

   ```powershell
   bot_env\Scripts\python.exe scripts\build_telegram_bundle.py --include-state
   ```

## Установка на VPS

Скопировать каталог `dist/algo_bot_telegram_bundle` и отдельно `telegram.env` на
сервер, затем выполнить от root:

```bash
bash /tmp/algo_bot_telegram_bundle/deploy/linux/install_telegram.sh \
  /tmp/algo_bot_telegram_bundle /tmp/telegram.env
```

Установщик создаёт отдельного пользователя `algobot`, виртуальное окружение и
службу `algo-bot-telegram.service`. Данные хранятся в `/opt/algo-bot`, секреты —
в `/etc/algo-bot/telegram.env`. Служба не занимает TCP-порт и не конфликтует с
`algo_bot_max`.

## Проверка

```bash
systemctl is-active algo-bot-telegram.service
journalctl -u algo-bot-telegram.service -n 100 --no-pager
sudo -u algobot /opt/algo-bot/.venv/bin/python \
  /opt/algo-bot/scripts/preflight_telegram.py
```

До запуска службы нужно остановить все другие экземпляры этого же бота:
Telegram разрешает только один long-polling процесс на один токен.

## Обновление кода

Для обновления используется code-only bundle без `logs/`, SQLite и CSV:

```bash
bash /tmp/algo_bot_telegram_bundle/deploy/linux/update_telegram.sh \
  /tmp/algo_bot_telegram_bundle
```

Скрипт не заменяет рабочую базу, данные и секреты. Перед обновлением он сохраняет
архив предыдущего кода и согласованный SQLite-снимок в `/var/backups/algo-bot`.
