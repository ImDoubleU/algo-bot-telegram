from __future__ import annotations

import os
import secrets
import sqlite3
import sys
from pathlib import Path


BASE_DIR = Path(__file__).resolve().parent.parent
ENV_PATH = BASE_DIR / ".env.webapp"
DB_PATH = BASE_DIR / "logs" / "bot_analytics.db"

if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))


def parse_env(path: Path) -> dict[str, str]:
    if not path.exists():
        return {}
    result: dict[str, str] = {}
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        result[key.strip()] = value.strip().strip('"').strip("'")
    return result


def resolve_admin_display_name(default: str) -> str:
    if not DB_PATH.exists():
        return default
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute(
        """
        SELECT display_name
        FROM web_users
        WHERE username = 'admin'
        LIMIT 1
        """
    )
    row = cur.fetchone()
    conn.close()
    if row and row[0]:
        return str(row[0])
    return default


def write_env(path: Path, values: dict[str, str]) -> None:
    lines = [
        "# Public web release configuration",
        "# Replace WEBAPP_PUBLIC_BASE_URL and WEBAPP_ALLOWED_HOSTS before opening the site to everyone.",
        "",
    ]
    for key, value in values.items():
        lines.append(f'{key}="{value}"')
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> None:
    existing = parse_env(ENV_PATH)
    admin_password = existing.get("WEBAPP_ADMIN_PASSWORD")
    if not admin_password or admin_password in {"change-me", "admin", "password"}:
        admin_password = secrets.token_urlsafe(18)

    secret_key = existing.get("WEBAPP_SECRET_KEY")
    if not secret_key or secret_key == "change-me-secret-key":
        secret_key = secrets.token_urlsafe(48)

    values = {
        "WEBAPP_ENV": existing.get("WEBAPP_ENV", "production"),
        "WEBAPP_SECRET_KEY": secret_key,
        "WEBAPP_SESSION_COOKIE": existing.get("WEBAPP_SESSION_COOKIE", "algo_bot_session"),
        "WEBAPP_SESSION_HTTPS_ONLY": existing.get("WEBAPP_SESSION_HTTPS_ONLY", "true"),
        "WEBAPP_SESSION_SAME_SITE": existing.get("WEBAPP_SESSION_SAME_SITE", "lax"),
        "WEBAPP_SESSION_MAX_AGE": existing.get("WEBAPP_SESSION_MAX_AGE", "43200"),
        "WEBAPP_ADMIN_USERNAME": existing.get("WEBAPP_ADMIN_USERNAME", "admin"),
        "WEBAPP_ADMIN_PASSWORD": admin_password,
        "WEBAPP_ADMIN_DISPLAY_NAME": existing.get("WEBAPP_ADMIN_DISPLAY_NAME", resolve_admin_display_name("Administrator")),
        "WEBAPP_HOST": existing.get("WEBAPP_HOST", "127.0.0.1"),
        "WEBAPP_PORT": existing.get("WEBAPP_PORT", "8000"),
        "WEBAPP_ALLOWED_HOSTS": existing.get("WEBAPP_ALLOWED_HOSTS", "*"),
        "WEBAPP_EXTRA_ALLOWED_HOSTS": existing.get("WEBAPP_EXTRA_ALLOWED_HOSTS", ""),
        "WEBAPP_ALLOW_CLOUDFLARE_TUNNEL_HOSTS": existing.get("WEBAPP_ALLOW_CLOUDFLARE_TUNNEL_HOSTS", "false"),
        "WEBAPP_FORWARDED_ALLOW_IPS": existing.get("WEBAPP_FORWARDED_ALLOW_IPS", "127.0.0.1"),
        "WEBAPP_PUBLIC_BASE_URL": existing.get("WEBAPP_PUBLIC_BASE_URL", "https://REPLACE_WITH_DOMAIN"),
        "WORKER_INTERVAL_SEC": existing.get("WORKER_INTERVAL_SEC", "60"),
    }

    write_env(ENV_PATH, values)
    for key, value in values.items():
        os.environ[key] = value

    from app.core.web_auth import WebAuthService

    auth = WebAuthService()
    username = values["WEBAPP_ADMIN_USERNAME"]
    display_name = values["WEBAPP_ADMIN_DISPLAY_NAME"]
    existing_user = auth.get_user_by_username(username)
    if existing_user:
        auth.update_user_password(username, values["WEBAPP_ADMIN_PASSWORD"])
        auth.update_user_profile(username, display_name=display_name, role="admin", is_active=1)
    else:
        auth.create_user(username, values["WEBAPP_ADMIN_PASSWORD"], display_name, "admin")

    print(f"env_path={ENV_PATH}")
    print(f"admin_username={username}")
    print(f"admin_password={values['WEBAPP_ADMIN_PASSWORD']}")


if __name__ == "__main__":
    main()
