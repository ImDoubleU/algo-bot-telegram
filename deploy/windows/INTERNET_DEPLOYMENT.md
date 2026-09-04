# Публикация Algo Bot Web в интернет

Этот набор файлов готовит приложение к публикации на домашнем ПК под Windows.

## Что уже подготовлено

1. Приложение умеет читать `.env.webapp`.
2. Добавлены безопасные настройки cookie и trusted hosts.
3. Есть `run_webapp.py` и `run_worker.py`.
4. Есть bootstrap-скрипт `scripts/bootstrap_public_release.py`.
5. Есть шаблон reverse proxy: `deploy/windows/Caddyfile.template`.

## Что сделать по шагам

1. Сгенерировать production-секреты и новый пароль администратора:
   `bot_env\Scripts\python.exe scripts\bootstrap_public_release.py`

2. Открыть `.env.webapp` и заменить:
   `WEBAPP_PUBLIC_BASE_URL`
   на ваш реальный домен

3. При желании ужесточить trusted hosts:
   `WEBAPP_ALLOWED_HOSTS=your-domain.example,www.your-domain.example`

4. Установить Caddy:
   `winget install CaddyServer.Caddy`

5. Скопировать `deploy/windows/Caddyfile.template` в рабочий `Caddyfile`
   и заменить `example.com` на ваш домен.

6. Проверить локальный запуск приложения:
   `powershell -ExecutionPolicy Bypass -File deploy\windows\start-webapp.ps1`

7. Проверить локальный запуск worker:
   `powershell -ExecutionPolicy Bypass -File deploy\windows\start-worker.ps1`

8. На роутере пробросить порты `80` и `443` на этот компьютер.

9. В Windows Firewall открыть входящие `80` и `443` для Caddy:
   `powershell -ExecutionPolicy Bypass -File deploy\windows\open-firewall-ports.ps1`

10. Запустить Caddy с вашим `Caddyfile`.

11. Проверить доступность:
    `https://your-domain.example/health`

12. При желании включить автозапуск webapp и worker:
    `powershell -ExecutionPolicy Bypass -File deploy\windows\register-startup-tasks.ps1`

## Важно

- Не публикуйте `uvicorn` напрямую в интернет.
- Порт `8000` должен слушать только локально.
- Наружу должен смотреть только `Caddy`.
- Перед запуском для пользователей убедитесь, что пароль `admin` уже не `change-me`.
