#!/usr/bin/env bash
set -euo pipefail

if [[ "${EUID}" -ne 0 ]]; then
    echo "Run as root." >&2
    exit 1
fi

source_dir="${1:-}"
project_dir=/opt/algo-bot

if [[ -z "$source_dir" ]]; then
    echo "Usage: $0 CODE_BUNDLE_DIR" >&2
    exit 1
fi

source_dir="$(realpath "$source_dir")"
if [[ ! -e "$project_dir/.telegram-installed" ]]; then
    echo "No completed installation found in $project_dir." >&2
    exit 1
fi
for required in bot.py requirements-telegram.txt scripts/preflight_telegram.py; do
    if [[ ! -f "$source_dir/$required" ]]; then
        echo "Missing bundle file: $required" >&2
        exit 1
    fi
done

backup_dir="/var/backups/algo-bot"
timestamp="$(date -u +%Y%m%dT%H%M%SZ)"
install -d -o root -g root -m 0700 "$backup_dir"
"$project_dir/.venv/bin/python" - \
    "$project_dir/logs/bot_analytics.db" \
    "$backup_dir/data_${timestamp}.db" <<'PY'
import sqlite3
import sys
from contextlib import closing

with closing(sqlite3.connect(sys.argv[1], timeout=30)) as source:
    with closing(sqlite3.connect(sys.argv[2])) as target:
        source.backup(target)
PY
tar -czf "$backup_dir/code_${timestamp}.tar.gz" \
    --exclude='.venv' \
    --exclude='logs' \
    --exclude='backups' \
    --exclude='exports' \
    --exclude='data' \
    --exclude='images' \
    -C "$project_dir" .

systemctl stop algo-bot-telegram.service
rsync -a --delete \
    --exclude='.venv/' \
    --exclude='.telegram-installed' \
    --exclude='logs/' \
    --exclude='backups/' \
    --exclude='exports/' \
    --exclude='images/' \
    --exclude='data/' \
    --chown=algobot:algobot \
    "$source_dir/" "$project_dir/"
"$project_dir/.venv/bin/pip" install -r "$project_dir/requirements-telegram.txt"
install -o root -g root -m 0644 \
    "$project_dir/deploy/linux/algo-bot-telegram.service" \
    /etc/systemd/system/algo-bot-telegram.service
systemctl daemon-reload
systemctl start algo-bot-telegram.service

if ! systemctl is-active --quiet algo-bot-telegram.service; then
    systemctl --no-pager --full status algo-bot-telegram.service || true
    journalctl -u algo-bot-telegram.service -n 100 --no-pager || true
    echo "Update failed. Code backup: $backup_dir/code_${timestamp}.tar.gz" >&2
    exit 1
fi

echo "UPDATE_OK code_backup=$backup_dir/code_${timestamp}.tar.gz data_backup=$backup_dir/data_${timestamp}.db"
systemctl --no-pager --full status algo-bot-telegram.service
