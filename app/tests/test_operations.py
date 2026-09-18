import asyncio
import json
import logging
from unittest.mock import AsyncMock

import pytest

from app.clients.operations import external_call


async def test_transient_failure_retries_and_logs_without_secret(caplog):
    call = AsyncMock(side_effect=[TimeoutError("secret-value"), "ok"])
    with caplog.at_level(logging.INFO, logger="school_ai.external"):
        result = await external_call("fixture", "read", call, attempts=3, backoff=0, retryable=lambda e: isinstance(e, TimeoutError))
    assert result == "ok"
    assert call.await_count == 2
    events = [json.loads(r.message) for r in caplog.records]
    assert [r["status"] for r in events] == ["error", "ok"]
    assert all(r["latency_ms"] >= 0 for r in events)
    assert "secret-value" not in caplog.text


async def test_permanent_failure_is_not_retried():
    call = AsyncMock(side_effect=ValueError("invalid"))
    with pytest.raises(ValueError):
        await external_call("fixture", "read", call, attempts=3, backoff=0)
    assert call.await_count == 1


async def test_retry_budget_is_bounded():
    call = AsyncMock(side_effect=TimeoutError())
    with pytest.raises(TimeoutError):
        await external_call("fixture", "read", call, attempts=3, backoff=0, retryable=lambda _: True)
    assert call.await_count == 3


async def test_cancelled_probe_is_not_logged_as_success_or_retried(caplog):
    call = AsyncMock(side_effect=asyncio.CancelledError())
    with caplog.at_level(logging.INFO, logger="school_ai.external"):
        with pytest.raises(asyncio.CancelledError):
            await external_call("fixture", "health", call, attempts=3, backoff=0, retryable=lambda _: True)
    assert call.await_count == 1
    event = json.loads(caplog.records[-1].message)
    assert event["status"] == "error"
    assert event["error_type"] == "CancelledError"
