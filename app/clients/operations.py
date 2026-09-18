"""Safe timing/retry wrapper. Never logs payloads, URLs, credentials or exception text."""
import asyncio
import json
import logging
import time
from collections.abc import Awaitable, Callable
from typing import TypeVar

T = TypeVar("T")
logger = logging.getLogger("school_ai.external")


async def external_call(
    service: str, operation: str, call: Callable[[], Awaitable[T]], *,
    attempts: int = 1, backoff: float = 0.25,
    retryable: Callable[[Exception], bool] = lambda _: False,
    metrics: Callable[[], dict] | None = None,
) -> T:
    for attempt in range(1, attempts + 1):
        started = time.perf_counter()
        error = None
        try:
            return await call()
        except asyncio.CancelledError:
            # A timed-out health probe cancels the call; never record it as success.
            error = "CancelledError"
            raise
        except Exception as exc:
            error = type(exc).__name__
            if attempt == attempts or not retryable(exc):
                raise
        finally:
            logger.info(json.dumps({
                "event": "external_call", "service": service, "operation": operation,
                "attempt": attempt, "latency_ms": round((time.perf_counter() - started) * 1000, 2),
                "status": "error" if error else "ok", "error_type": error,
                "tokens": None, "cost_usd": None,
                **(metrics() if metrics else {}),
            }))
        await asyncio.sleep(backoff * 2 ** (attempt - 1))
    raise RuntimeError("No external call attempts configured")
