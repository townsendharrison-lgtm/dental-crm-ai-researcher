"""Three-tier rubric inference: stated → cross-school → qualitative."""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from decimal import Decimal
import re
from typing import Any, Mapping, Sequence
from uuid import UUID

from app.agents.normalization_service import default_family_weight, school_specific_provisional_weight
from app.clients.llm_client import LLMClient, ExtractionFailed
from app.factor_taxonomy import FactorTaxonomy
from app.schemas.rubric import (
    QUALITATIVE_BUCKET_WEIGHTS,
    RubricFactorDraft,
    RubricWriteRejected,
)

# Parse "avg_gpa: 25%" or "avg_gpa = 0.25" from category *_stated_weights text.
STATED_WEIGHT_RE = re.compile(
    r"(?P<key>[a-z][a-z0-9_]*)\s*[:=]\s*(?P<number>\d+(?:\.\d+)?)\s*(?P<pct>%)?",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class FactView:
    id: str
    factor_key: str
    value: Any
    unit: str | None
    confidence: Decimal
    source_type: str
    source_url: str | None
    document_id: str | None
    raw_text_snippet: str | None


def citation_for(fact: FactView, *, official_url: str | None = None) -> str | None:
    if fact.source_url:
        return fact.source_url.strip()
    if fact.document_id:
        return f"document:{fact.document_id}"
    if official_url:
        return official_url.strip()
    return None


def parse_stated_weights(facts: Sequence[FactView], taxonomy: FactorTaxonomy) -> dict[str, tuple[Decimal, FactView]]:
    """Extract explicit factor weights from *_stated_weights facts only."""
    known = taxonomy.by_key
    found: dict[str, tuple[Decimal, FactView]] = {}
    for fact in facts:
        if not fact.factor_key.endswith("_stated_weights"):
            continue
        text = " ".join(part for part in (str(fact.value or ""), fact.raw_text_snippet or "") if part)
        for match in STATED_WEIGHT_RE.finditer(text):
            key = match.group("key").lower()
            if key not in known or key.endswith("_stated_weights"):
                continue
            number = Decimal(match.group("number"))
            weight = number / Decimal(100) if match.group("pct") else number
            if weight < 0 or weight > 1:
                continue
            prior = found.get(key)
            if prior is None or fact.confidence >= prior[1].confidence:
                found[key] = (weight, fact)
    return found


def best_facts_by_key(facts: Sequence[FactView]) -> dict[str, FactView]:
    best: dict[str, FactView] = {}
    for fact in facts:
        current = best.get(fact.factor_key)
        if current is None or fact.confidence > current.confidence:
            best[fact.factor_key] = fact
    return best


def normalize_category_weights(drafts: list[RubricFactorDraft], taxonomy: FactorTaxonomy) -> list[RubricFactorDraft]:
    """Preserve stated weights; scale inferred weights to fill remaining category mass.

    Each category's weights sum to 1.0 when the category has at least one weighted row.
    Categories are independent (no cross-category reallocation). Documented in SCHEMA.md.
    """
    by_category: dict[str, list[RubricFactorDraft]] = defaultdict(list)
    for draft in drafts:
        by_category[taxonomy.by_key[draft.factor_key].category].append(draft)

    normalized: list[RubricFactorDraft] = []
    for category, rows in by_category.items():
        stated = [row for row in rows if row.weight_source == "stated" and row.weight is not None]
        inferred = [row for row in rows if row.weight_source in {
            "cross_school_inferred", "qualitative_inferred",
        } and row.weight is not None]
        manual = [row for row in rows if row.weight_source == "manual_override"]
        pending = [row for row in rows if row.weight_source == "pending_evidence"]
        others = [
            row for row in rows
            if row.weight is None and row.weight_source not in {
                "stated", "cross_school_inferred", "qualitative_inferred",
                "manual_override", "pending_evidence",
            }
        ]

        stated_sum = sum((row.weight for row in stated), Decimal("0"))
        if stated_sum > 1:
            scale = Decimal("1") / stated_sum
            stated = [row.model_copy(update={"weight": (row.weight * scale).quantize(Decimal("0.00000001"))})
                      for row in stated]
            stated_sum = Decimal("1")
            remaining = Decimal("0")
        else:
            remaining = Decimal("1") - stated_sum

        inferred_sum = sum((row.weight for row in inferred), Decimal("0"))
        if inferred and remaining > 0:
            if inferred_sum <= 0:
                share = remaining / Decimal(len(inferred))
                inferred = [row.model_copy(update={"weight": share.quantize(Decimal("0.00000001"))}) for row in inferred]
            else:
                scale = remaining / inferred_sum
                inferred = [row.model_copy(update={"weight": (row.weight * scale).quantize(Decimal("0.00000001"))})
                            for row in inferred]
        elif inferred and remaining == 0:
            inferred = [row.model_copy(update={"weight": Decimal("0")}) for row in inferred]

        # Pending slots stay at weight 0 until evidence arrives — do not absorb category mass.
        pending = [
            row.model_copy(update={"weight": Decimal("0"), "confidence": Decimal("0")})
            for row in pending
        ]

        normalized.extend(stated + inferred + manual + pending + others)
    return normalized


class RubricInferenceAgent:
    def __init__(self, taxonomy: FactorTaxonomy, llm: LLMClient | None = None):
        self.taxonomy = taxonomy
        self.llm = llm

    def _evidence_urls(self, fact: FactView, *, official_url: str | None) -> list[str]:
        url = citation_for(fact, official_url=official_url)
        return [url] if url else []

    def draft_stated(self, stated: Mapping[str, tuple[Decimal, FactView]], *, official_url: str | None) -> list[RubricFactorDraft]:
        drafts = []
        for key, (weight, fact) in stated.items():
            urls = self._evidence_urls(fact, official_url=official_url)
            if not urls:
                continue
            drafts.append(RubricFactorDraft(
                factor_key=key,
                value=None,
                weight=weight,
                weight_source="stated",
                confidence=min(Decimal("1"), fact.confidence),
                reasoning=f"Explicit stated weight {weight} taken from {fact.factor_key} evidence.",
                source_urls=urls,
            ))
        return drafts

    def draft_cross_school(
        self,
        *,
        best: Mapping[str, FactView],
        stated_keys: set[str],
        stats_by_key: Mapping[str, Mapping[str, Any]],
        school_id: UUID,
        official_url: str | None,
    ) -> list[RubricFactorDraft]:
        drafts = []
        for key, definition in self.taxonomy.by_key.items():
            if definition.value_type != "number" or key in stated_keys:
                continue
            fact = best.get(key)
            if fact is None or fact.value is None:
                continue
            urls = self._evidence_urls(fact, official_url=official_url)
            if not urls:
                continue
            family = default_family_weight(key)
            stats = stats_by_key.get(key) or {}
            by_school = (stats.get("percentiles") or {}).get("by_school") or {}
            school_stats = by_school.get(str(school_id)) or {}
            percentile = school_stats.get("percentile_rank")
            provisional = school_specific_provisional_weight(
                key, percentile_rank=percentile, base=family,
            )
            sample = stats.get("sample_size")
            reasoning_parts = [
                f"Numeric factor with no stated weight; base curve weight "
                f"{family if family is not None else Decimal('0.10')} "
                f"scaled by cross-school distinctiveness → {provisional}.",
            ]
            if percentile is not None:
                reasoning_parts.append(
                    f"School percentile rank among {sample} schools: {percentile} "
                    f"(higher = stricter/rarer → more weight for this school)."
                )
            else:
                reasoning_parts.append(
                    "Cross-school percentile unavailable; using unscaled provisional weight."
                )
            drafts.append(RubricFactorDraft(
                factor_key=key,
                value=fact.value,
                weight=provisional,
                weight_source="cross_school_inferred",
                confidence=min(Decimal("1"), fact.confidence),
                reasoning=" ".join(reasoning_parts),
                source_urls=urls,
            ))
        return drafts

    async def draft_qualitative(
        self,
        *,
        best: Mapping[str, FactView],
        facts: Sequence[FactView],
        occupied_keys: set[str],
        official_url: str | None,
    ) -> list[RubricFactorDraft]:
        if self.llm is None:
            return []
        drafts = []
        for key, definition in self.taxonomy.by_key.items():
            if definition.value_type != "text" or key in occupied_keys or key.endswith("_stated_weights"):
                continue
            if not definition.scoring_eligible:
                continue
            related = [fact for fact in facts if fact.factor_key == key]
            if not related:
                continue
            evidence = []
            allowed_urls = []
            for fact in related:
                url = citation_for(fact, official_url=official_url)
                if not url:
                    continue
                allowed_urls.append(url)
                evidence.append({
                    "factor_key": fact.factor_key,
                    "value": fact.value,
                    "snippet": fact.raw_text_snippet,
                    "source_url": url,
                    "confidence": float(fact.confidence),
                })
            if not evidence:
                continue
            try:
                result = await self.llm.infer_qualitative_weight(
                    factor=definition, evidence=evidence, allowed_source_urls=allowed_urls,
                )
            except ExtractionFailed:
                continue
            # Enforce allow-list of URLs in code.
            urls = [url for url in result.source_urls if url in set(allowed_urls)]
            if not urls:
                continue
            weight = QUALITATIVE_BUCKET_WEIGHTS[result.weight_bucket]
            drafts.append(RubricFactorDraft(
                factor_key=key,
                value=best[key].value if key in best else related[0].value,
                weight=weight,
                weight_source="qualitative_inferred",
                confidence=min(Decimal("0.85"), max(fact.confidence for fact in related)),
                reasoning=result.reasoning.strip(),
                source_urls=urls,
            ))
        return drafts

    async def infer(
        self,
        *,
        school_id: UUID,
        official_url: str | None,
        facts: Sequence[FactView],
        stats_by_key: Mapping[str, Mapping[str, Any]],
        preserve_manual: Mapping[str, RubricFactorDraft] | None = None,
    ) -> list[RubricFactorDraft]:
        preserve_manual = dict(preserve_manual or {})
        stated_map = parse_stated_weights(facts, self.taxonomy)
        # Manual overrides win over everything.
        for key in list(stated_map):
            if key in preserve_manual:
                del stated_map[key]
        best = best_facts_by_key(facts)
        drafts = self.draft_stated(stated_map, official_url=official_url)
        occupied = set(preserve_manual) | {d.factor_key for d in drafts}
        cross = self.draft_cross_school(
            best=best, stated_keys=occupied, stats_by_key=stats_by_key,
            school_id=school_id, official_url=official_url,
        )
        drafts.extend(cross)
        occupied |= {d.factor_key for d in cross}
        qualitative = await self.draft_qualitative(
            best=best, facts=facts, occupied_keys=occupied, official_url=official_url,
        )
        drafts.extend(qualitative)
        # Re-attach manuals after normalization of automated rows only.
        automated = [d for d in drafts if d.factor_key not in preserve_manual]
        normalized = normalize_category_weights(automated, self.taxonomy)
        merged = {d.factor_key: d for d in normalized}
        merged.update(preserve_manual)

        # Always materialize the full client taxonomy (minus meta *_stated_weights slots)
        # so every category/sub-factor appears in the rubric UI. Missing evidence → weight 0.
        for factor in self.taxonomy.factors:
            if factor.key.endswith("_stated_weights"):
                continue
            if factor.key in merged:
                continue
            merged[factor.key] = RubricFactorDraft(
                factor_key=factor.key,
                value=None,
                weight=Decimal("0"),
                weight_source="pending_evidence",
                confidence=Decimal("0"),
                reasoning=(
                    f"No school-specific evidence for {factor.key} yet "
                    f"({factor.category}). Slot reserved from the approved taxonomy; "
                    "weight 0 until documents/web research fill it."
                ),
                source_urls=[],
            )

        # Final validation gate.
        for draft in merged.values():
            if draft.weight_source not in {"manual_override", "pending_evidence"} and not draft.source_urls:
                raise RubricWriteRejected(f"Ungrounded draft for {draft.factor_key}")
        return list(merged.values())
