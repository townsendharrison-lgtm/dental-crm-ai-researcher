"""Per-school daily call/cost guardrails and usage metering."""
from __future__ import annotations

from collections import defaultdict
from contextvars import ContextVar
from datetime import date, datetime, timezone
from decimal import Decimal
from uuid import UUID, uuid4

from sqlalchemy import insert

current_school_id: ContextVar[UUID | None] = ContextVar("current_school_id", default=None)


class BudgetExceeded(RuntimeError):
    pass


class UsageGuard:
    """In-memory daily counters plus optional durable event rows (append-only)."""

    def __init__(self, settings, database=None):
        self.settings = settings
        self.database = database
        self._memory: dict[tuple, dict] = defaultdict(
            lambda: {"calls": 0, "tokens": 0, "cost_usd": Decimal("0")}
        )

    @staticmethod
    def _day() -> date:
        return datetime.now(timezone.utc).date()

    def _key(self, service: str, school_id: UUID | None):
        return (self._day().isoformat(), str(school_id or "global"), service)

    def _limits(self, service: str) -> tuple[int, Decimal]:
        if service == "openai":
            return self.settings.openai_daily_calls_per_school, Decimal(str(self.settings.openai_daily_cost_usd_per_school))
        if service == "tavily":
            return self.settings.tavily_daily_calls_per_school, Decimal(str(self.settings.tavily_daily_cost_usd_per_school))
        return 10_000, Decimal("1000")

    async def authorize(self, service: str, school_id: UUID | None = None):
        school_id = school_id if school_id is not None else current_school_id.get()
        key = self._key(service, school_id)
        bucket = self._memory[key]
        call_limit, cost_limit = self._limits(service)
        if bucket["calls"] >= call_limit:
            raise BudgetExceeded(f"Daily {service} call cap reached for school scope")
        if bucket["cost_usd"] >= cost_limit:
            raise BudgetExceeded(f"Daily {service} cost cap reached for school scope")

    async def record(
        self, service: str, operation: str, *, school_id: UUID | None = None,
        tokens: int | None = None, cost_usd: float | None = None, latency_ms: float | None = None,
    ):
        school_id = school_id if school_id is not None else current_school_id.get()
        key = self._key(service, school_id)
        bucket = self._memory[key]
        bucket["calls"] += 1
        if tokens:
            bucket["tokens"] += int(tokens)
        if cost_usd:
            bucket["cost_usd"] += Decimal(str(cost_usd))

        if self.database is None:
            return
        # Durable append-only event; never deletes or truncates usage history.
        try:
            from app.db.models import ProviderUsageEvent
            table = ProviderUsageEvent.__table__

            async def write(connection):
                await connection.execute(insert(table).values(
                    id=uuid4(),
                    school_id=school_id,
                    service=service,
                    operation=operation,
                    tokens=tokens,
                    cost_usd=Decimal(str(cost_usd)) if cost_usd is not None else None,
                    latency_ms=latency_ms,
                    usage_day=self._day(),
                ))
            await self.database.transaction("record_provider_usage", write)
        except Exception:
            # Metering must not break primary workflows; counters above still apply.
            return

    def snapshot(self, school_id: UUID | None = None) -> list[dict]:
        prefix = str(school_id or "global")
        rows = []
        for (day, scope, service), bucket in sorted(self._memory.items()):
            if school_id is not None and scope != prefix:
                continue
            rows.append({
                "day": day, "school_id": None if scope == "global" else scope, "service": service,
                "calls": bucket["calls"], "tokens": bucket["tokens"],
                "cost_usd": float(bucket["cost_usd"]),
            })
        return rows


_guard: UsageGuard | None = None


def get_usage_guard(settings=None, database=None) -> UsageGuard:
    global _guard
    if _guard is None:
        from app.config import get_settings
        _guard = UsageGuard(settings or get_settings(), database)
    elif database is not None and _guard.database is None:
        _guard.database = database
    return _guard


def reset_usage_guard_for_tests():
    global _guard
    _guard = None
