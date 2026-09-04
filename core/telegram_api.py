import asyncio
import logging
from typing import Awaitable, Callable, TypeVar

from telegram.error import NetworkError, RetryAfter, TimedOut


T = TypeVar("T")
logger = logging.getLogger(__name__)


async def telegram_api_call(
    call_factory: Callable[[], Awaitable[T]],
    operation_name: str,
    retries: int = 2,
    timeout_sec: float = 15.0,
) -> T | None:
    attempt = 0
    while True:
        try:
            return await asyncio.wait_for(call_factory(), timeout=timeout_sec)
        except RetryAfter as exc:
            wait_seconds = int(getattr(exc, "retry_after", 3)) + 1
            logger.warning(
                "RetryAfter during %s, wait %ss, attempt %s/%s",
                operation_name,
                wait_seconds,
                attempt + 1,
                retries + 1,
            )
            await asyncio.sleep(wait_seconds)
        except (TimedOut, NetworkError, asyncio.TimeoutError) as exc:
            if attempt >= retries:
                logger.warning(
                    "Telegram API failed for %s after %s attempts: %s",
                    operation_name,
                    retries + 1,
                    exc,
                )
                return None
            await asyncio.sleep(1 + attempt)
        attempt += 1

