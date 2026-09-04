import os
from dataclasses import dataclass
from pathlib import Path


BASE_DIR = Path(__file__).resolve().parent.parent


def _load_env_file(path: Path) -> None:
    if not path.exists():
        return
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        os.environ.setdefault(key, value)


_load_env_file(BASE_DIR / ".env.webapp")


@dataclass(frozen=True)
class Settings:
    base_dir: Path = BASE_DIR
    db_path: Path = BASE_DIR / "logs" / "bot_analytics.db"
    auto_feedback_csv_path: Path = BASE_DIR / "logs" / "auto_feedback_actions.csv"
    user_analytics_csv_path: Path = BASE_DIR / "logs" / "user_analytics.csv"
    courses_path: Path = BASE_DIR / "data" / "courses.json"
    hub_content_path: Path = BASE_DIR / "data" / "hub_content.json"
    logs_path: Path = BASE_DIR / "logs"
    environment: str = os.getenv("WEBAPP_ENV", "local")
    secret_key: str = os.getenv("WEBAPP_SECRET_KEY", "change-me-secret-key")
    session_cookie: str = os.getenv("WEBAPP_SESSION_COOKIE", "algo_bot_session")
    session_https_only: bool = os.getenv("WEBAPP_SESSION_HTTPS_ONLY", "false").lower() in {"1", "true", "yes", "on"}
    session_same_site: str = os.getenv("WEBAPP_SESSION_SAME_SITE", "lax")
    session_max_age: int = int(os.getenv("WEBAPP_SESSION_MAX_AGE", "43200"))
    admin_username: str = os.getenv("WEBAPP_ADMIN_USERNAME", "admin")
    admin_password: str = os.getenv("WEBAPP_ADMIN_PASSWORD", "change-me")
    admin_display_name: str = os.getenv("WEBAPP_ADMIN_DISPLAY_NAME", "Administrator")
    host: str = os.getenv("WEBAPP_HOST", "127.0.0.1")
    port: int = int(os.getenv("WEBAPP_PORT", "8000"))
    allowed_hosts_raw: str = os.getenv("WEBAPP_ALLOWED_HOSTS", "127.0.0.1,localhost")
    extra_allowed_hosts_raw: str = os.getenv("WEBAPP_EXTRA_ALLOWED_HOSTS", "")
    allow_cloudflare_tunnel_hosts: bool = os.getenv("WEBAPP_ALLOW_CLOUDFLARE_TUNNEL_HOSTS", "false").lower() in {
        "1",
        "true",
        "yes",
        "on",
    }
    forwarded_allow_ips: str = os.getenv("WEBAPP_FORWARDED_ALLOW_IPS", "127.0.0.1")
    public_base_url: str = os.getenv("WEBAPP_PUBLIC_BASE_URL", "")
    worker_interval_sec: int = int(os.getenv("WORKER_INTERVAL_SEC", "60"))

    @property
    def allowed_hosts(self) -> list[str]:
        hosts: list[str] = []
        for raw in (self.allowed_hosts_raw, self.extra_allowed_hosts_raw):
            value = (raw or "").strip()
            if not value:
                continue
            if value == "*":
                return ["*"]
            hosts.extend(item.strip() for item in value.split(",") if item.strip())

        if self.allow_cloudflare_tunnel_hosts:
            hosts.extend(["*.trycloudflare.com", "*.cfargotunnel.com"])

        hosts.extend(["127.0.0.1", "localhost"])

        unique_hosts: list[str] = []
        for host in hosts:
            if host not in unique_hosts:
                unique_hosts.append(host)
        return unique_hosts or ["127.0.0.1", "localhost"]


settings = Settings()
