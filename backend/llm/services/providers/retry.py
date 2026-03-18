import asyncio
from collections.abc import Awaitable, Callable

from openai._exceptions import APIStatusError, APITimeoutError, RateLimitError


def is_openai_compatible_transient_error(exc: Exception) -> bool:
    return isinstance(exc, (RateLimitError, APITimeoutError, APIStatusError)) and getattr(
        exc, "status_code", 500
    ) in {408, 409, 429, 500, 502, 503, 504}


async def retry_with_backoff(
    func: Callable[[], Awaitable[object]],
    *,
    max_retries: int = 3,
    base_delay: float = 1.0,
    is_transient_error: Callable[[Exception], bool] | None = None,
):
    transient_checker = is_transient_error or is_openai_compatible_transient_error
    for attempt in range(max_retries + 1):
        try:
            return await func()
        except Exception as exc:
            if attempt >= max_retries or not transient_checker(exc):
                raise
            delay = base_delay * (2**attempt)
            await asyncio.sleep(delay)
