#!/usr/bin/env bash
set -euo pipefail

if [[ "${EUID}" -ne 0 ]]; then
    echo "Run as root." >&2
    exit 1
fi

source_dir="${1:-}"
env_source="${2:-}"
project_dir=/opt/algo-bot
env_dir=/etc/algo-bot
env_target="$env_dir/telegram.env"

if [[ -z "$source_dir" || -z "$env_source" ]]; then
    echo "Usage: $0 BUNDLE_DIR TELEGRAM_ENV_FILE" >&2
    exit 1
fi

source_dir="$(realpath "$source_dir")"
env_source="$(realpath "$env_source")"

for required in bot.py requirements-telegram.txt scripts/preflight_telegram.py deploy/linux/algo-bot-telegram.service logs/bot_analytics.db; do
    if [[ ! -f "$source_dir/$required" ]]; then
        echo "Missing bundle file: $required" >&2
        exit 1
    fi
done

if ! tr -d '\r' < "$env_source" | grep -Eq '^TELEGRAM_BOT_TOKEN=[0-9]{6,12}:[A-Za-z0-9_-]{30,}$'; then
    echo "TELEGRAM_BOT_TOKEN is missing or malformed in $env_source" >&2
    exit 1
fi
if ! tr -d '\r' < "$env_source" | grep -Eq '^TELEGRAM_ADMIN_IDS=[0-9]+(,[0-9]+)*$'; then
    echo "TELEGRAM_ADMIN_IDS is missing or malformed in $env_source" >&2
    exit 1
fi

if [[ -e "$project_dir/.telegram-installed" ]]; then
    echo "$project_dir already contains a completed installation; use update_telegram.sh." >&2
    exit 1
fi

id -u algobot >/dev/null 2>&1 || useradd --system --home-dir "$project_dir" --shell /usr/sbin/nologin algobot
install -d -o algobot -g algobot -m 0750 "$project_dir"
install -d -o root -g algobot -m 0750 "$env_dir"
rsync -a --exclude '.env.telegram' --chown=algobot:algobot "$source_dir/" "$project_dir/"
install -o root -g algobot -m 0640 "$env_source" "$env_target"
sed -i 's/\r$//' "$env_target"

python3 -m venv "$project_dir/.venv"
"$project_dir/.venv/bin/pip" install --upgrade pip
"$project_dir/.venv/bin/pip" install -r "$project_dir/requirements-telegram.txt"
chown -R algobot:algobot "$project_dir/.venv"

install -o root -g root -m 0644 \
    "$project_dir/deploy/linux/algo-bot-telegram.service" \
    /etc/systemd/system/algo-bot-telegram.service
systemctl daemon-reload
systemctl enable --now algo-bot-telegram.service

if ! systemctl is-active --quiet algo-bot-telegram.service; then
    systemctl --no-pager --full status algo-bot-telegram.service || true
    journalctl -u algo-bot-telegram.service -n 100 --no-pager || true
    exit 1
fi

touch "$project_dir/.telegram-installed"
chown algobot:algobot "$project_dir/.telegram-installed"
systemctl --no-pager --full status algo-bot-telegram.service
