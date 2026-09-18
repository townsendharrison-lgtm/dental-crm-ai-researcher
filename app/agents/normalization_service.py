"""Cross-school numeric normalization. Pure math — no LLM calls."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from math import sqrt
from typing import Any, Iterable, Mapping, Sequence
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from app.db.models import CrossSchoolStat, SchoolRawFact
from app.factor_taxonomy import FactorTaxonomy, get_taxonomy

STATS = CrossSchoolStat.__table__
FACTS = SchoolRawFact.__table__

METHOD = "phase4_empirical_v1"

# Relative rubric weights within a min_/avg_/max_ family when a school states no weights.
# Documented in SCHEMA.md. Sum is 1.0.
DEFAULT_FAMILY_WEIGHTS = {"min": Decimal("0.05"), "avg": Decimal("0.90"), "max": Decimal("0.05")}

# Maps a numeric applicant/school value along a school's published min/avg/max:
# at min → ~5%, at avg → peak (1.0), at max → ~95%. Used by later scoring/inference.
DEFAULT_POSITION_ANCHORS = {"at_min": Decimal("0.05"), "at_avg": Decimal("1.00"), "at_max": Decimal("0.95")}


@dataclass(frozen=True)
class SchoolValue:
    school_id: UUID
    value: Decimal
    fact_ids: tuple[str, ...]


@dataclass(frozen=True)
class FactorDistribution:
    factor_key: str
    unit: str | None
    sample_size: int
    min_value: Decimal
    max_value: Decimal
    mean_value: Decimal
    stddev_value: Decimal
    percentiles: dict[str, Any]
    provenance: dict[str, Any]
    method: str = METHOD


def _as_decimal(value: Any) -> Decimal | None:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, Decimal):
        return value
    if isinstance(value, (int, float)):
        return Decimal(str(value))
    if isinstance(value, str):
        try:
            return Decimal(value)
        except Exception:
            return None
    return None


def family_role(factor_key: str) -> str | None:
    if factor_key.startswith("min_"):
        return "min"
    if factor_key.startswith("avg_"):
        return "avg"
    if factor_key.startswith("max_"):
        return "max"
    return None


def default_family_weight(factor_key: str, *, overrides: Mapping[str, Decimal] | None = None) -> Decimal | None:
    """Return the documented default weight for a min_/avg_/max_ factor, or None if not in a family."""
    role = family_role(factor_key)
    if role is None:
        return None
    weights = {**DEFAULT_FAMILY_WEIGHTS, **(overrides or {})}
    return Decimal(weights[role])


def score_along_min_avg_max(
    value: Decimal | float | int,
    minimum: Decimal | float | int,
    average: Decimal | float | int,
    maximum: Decimal | float | int,
    *,
    anchors: Mapping[str, Decimal] | None = None,
) -> Decimal:
    """Piecewise-linear curve: min→~0.05, avg→1.0, max→~0.95.

    Outside [min, max] the score is clamped to the nearer endpoint anchor.
    Requires min ≤ avg ≤ max. Deterministic; no LLM.
    """
    value = Decimal(str(value))
    minimum = Decimal(str(minimum))
    average = Decimal(str(average))
    maximum = Decimal(str(maximum))
    if not (minimum <= average <= maximum):
        raise ValueError("Require min <= avg <= max")
    points = {**DEFAULT_POSITION_ANCHORS, **(anchors or {})}
    at_min, at_avg, at_max = Decimal(points["at_min"]), Decimal(points["at_avg"]), Decimal(points["at_max"])
    if value <= minimum:
        return at_min
    if value >= maximum:
        return at_max
    if value == average:
        return at_avg
    if value < average:
        span = average - minimum
        if span == 0:
            return at_avg
        t = (value - minimum) / span
        return at_min + t * (at_avg - at_min)
    span = maximum - average
    if span == 0:
        return at_avg
    t = (value - average) / span
    return at_avg + t * (at_max - at_avg)


def percentile_rank(value: Decimal, values: Sequence[Decimal]) -> Decimal:
    """Average-rank percentile in 0–100. Empty input is invalid."""
    if not values:
        raise ValueError("values required")
    n = len(values)
    below = sum(1 for item in values if item < value)
    equal = sum(1 for item in values if item == value)
    return (Decimal(below) + Decimal(equal) / Decimal(2)) * Decimal(100) / Decimal(n)


def z_score(value: Decimal, mean: Decimal, stddev: Decimal) -> Decimal:
    if stddev == 0:
        return Decimal("0")
    return (value - mean) / stddev


def _quantile(sorted_values: Sequence[Decimal], q: float) -> Decimal:
    if not sorted_values:
        raise ValueError("values required")
    if len(sorted_values) == 1:
        return sorted_values[0]
    position = (len(sorted_values) - 1) * q
    low = int(position)
    high = min(low + 1, len(sorted_values) - 1)
    weight = Decimal(str(position - low))
    return sorted_values[low] * (1 - weight) + sorted_values[high] * weight


def select_school_values(rows: Iterable[Mapping[str, Any]]) -> list[SchoolValue]:
    """Pick the best numeric fact per school (highest confidence, then latest extracted_at)."""
    best: dict[UUID, tuple[Decimal, Decimal, datetime, list[str]]] = {}
    for row in rows:
        number = _as_decimal(row.get("value"))
        if number is None:
            continue
        school_id = row["school_id"]
        confidence = Decimal(str(row.get("confidence") or 0))
        extracted = row.get("extracted_at") or datetime.min.replace(tzinfo=timezone.utc)
        fact_id = str(row["id"])
        current = best.get(school_id)
        if current is None or confidence > current[0] or (confidence == current[0] and extracted > current[2]):
            best[school_id] = (confidence, number, extracted, [fact_id])
        elif confidence == current[0] and extracted == current[2] and number == current[1]:
            current[3].append(fact_id)
    return [SchoolValue(school_id=school_id, value=item[1], fact_ids=tuple(item[3]))
            for school_id, item in sorted(best.items(), key=lambda pair: str(pair[0]))]


def compute_distribution(factor_key: str, unit: str | None, school_values: Sequence[SchoolValue]) -> FactorDistribution:
    if len(school_values) < 1:
        raise ValueError("At least one school value is required")
    values = [item.value for item in school_values]
    n = len(values)
    mean = sum(values) / Decimal(n)
    variance = sum((value - mean) ** 2 for value in values) / Decimal(n)
    stddev = Decimal(str(sqrt(float(variance))))
    ordered = sorted(values)
    by_school = {
        str(item.school_id): {
            "value": float(item.value),
            "percentile_rank": float(percentile_rank(item.value, values)),
            "z_score": float(z_score(item.value, mean, stddev)),
            "fact_ids": list(item.fact_ids),
        }
        for item in school_values
    }
    return FactorDistribution(
        factor_key=factor_key,
        unit=unit,
        sample_size=n,
        min_value=min(values),
        max_value=max(values),
        mean_value=mean,
        stddev_value=stddev,
        percentiles={
            "p10": float(_quantile(ordered, 0.10)),
            "p25": float(_quantile(ordered, 0.25)),
            "p50": float(_quantile(ordered, 0.50)),
            "p75": float(_quantile(ordered, 0.75)),
            "p90": float(_quantile(ordered, 0.90)),
            "by_school": by_school,
        },
        provenance={
            "raw_fact_ids": [fact_id for item in school_values for fact_id in item.fact_ids],
            "school_ids": [str(item.school_id) for item in school_values],
            "selection": "highest_confidence_then_latest_per_school",
        },
    )


def distributions_from_facts(
    facts: Sequence[Mapping[str, Any]],
    taxonomy: FactorTaxonomy | None = None,
) -> list[FactorDistribution]:
    taxonomy = taxonomy or get_taxonomy()
    numeric_keys = {factor.key for factor in taxonomy.factors if factor.value_type == "number"}
    grouped: dict[tuple[str, str | None], list[Mapping[str, Any]]] = {}
    for row in facts:
        key = row["factor_key"]
        if key not in numeric_keys:
            continue
        unit = row.get("unit")
        grouped.setdefault((key, unit), []).append(row)
    results = []
    for (factor_key, unit), rows in sorted(grouped.items()):
        school_values = select_school_values(rows)
        if school_values:
            results.append(compute_distribution(factor_key, unit, school_values))
    return results


class NormalizationService:
    def __init__(self, settings, database, *, taxonomy: FactorTaxonomy | None = None):
        self.settings = settings
        self.database = database
        self.taxonomy = taxonomy or get_taxonomy()

    async def load_numeric_facts(self) -> list[dict]:
        async def read(connection):
            rows = (await connection.execute(select(FACTS))).mappings().all()
            return [dict(row) for row in rows]
        return await self.database.transaction("read_facts_for_normalization", read)

    async def recompute(self) -> dict:
        facts = await self.load_numeric_facts()
        distributions = distributions_from_facts(facts, self.taxonomy)
        written = await self.persist(distributions)
        return {
            "method": METHOD,
            "factor_count": len(written),
            "factors": [
                {
                    "factor_key": item.factor_key,
                    "unit": item.unit or "",
                    "sample_size": item.sample_size,
                    "min_value": float(item.min_value),
                    "max_value": float(item.max_value),
                    "mean_value": float(item.mean_value),
                    "stddev_value": float(item.stddev_value),
                }
                for item in written
            ],
        }

    async def persist(self, distributions: Sequence[FactorDistribution]) -> list[FactorDistribution]:
        async def write(connection):
            for item in distributions:
                await connection.execute(pg_insert(STATS).values(
                    id=uuid4(),
                    factor_key=item.factor_key,
                    unit=item.unit or "",
                    sample_size=item.sample_size,
                    min_value=item.min_value,
                    max_value=item.max_value,
                    mean_value=item.mean_value,
                    stddev_value=item.stddev_value,
                    percentiles=item.percentiles,
                    method=item.method,
                    provenance=item.provenance,
                    computed_at=datetime.now(timezone.utc),
                ).on_conflict_do_update(
                    index_elements=[STATS.c.factor_key, STATS.c.unit],
                    set_={
                        "sample_size": item.sample_size,
                        "min_value": item.min_value,
                        "max_value": item.max_value,
                        "mean_value": item.mean_value,
                        "stddev_value": item.stddev_value,
                        "percentiles": item.percentiles,
                        "method": item.method,
                        "provenance": item.provenance,
                        "computed_at": datetime.now(timezone.utc),
                    },
                ))
            return list(distributions)
        return await self.database.transaction("upsert_cross_school_stats", write)

    async def get_stat(self, factor_key: str, unit: str = "") -> dict | None:
        async def read(connection):
            row = (await connection.execute(select(STATS).where(
                STATS.c.factor_key == factor_key, STATS.c.unit == unit,
            ))).mappings().first()
            return dict(row) if row else None
        return await self.database.transaction("read_cross_school_stat", read)
