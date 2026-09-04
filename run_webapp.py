import uvicorn

from app.settings import settings


if __name__ == "__main__":
    uvicorn.run(
        "webapp.main:app",
        host=settings.host,
        port=settings.port,
        proxy_headers=True,
        forwarded_allow_ips=settings.forwarded_allow_ips,
    )
