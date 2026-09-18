"""Generate, override, approve school rubrics. Never deletes production rows."""
from datetime import datetime, timezone
from decimal import Decimal
from uuid import UUID, uuid4

from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert

from app.agents.rubric_inference_agent import FactView, RubricInferenceAgent
from app.clients.llm_client import LLMClient
from app.db.models import CrossSchoolStat, RubricFactor, RubricOverride, School, SchoolRawFact
from app.factor_taxonomy import get_taxonomy
from app.schemas.rubric import RubricFactorDraft, RubricWriteRejected

SCHOOLS = School.__table__
FACTS = SchoolRawFact.__table__
STATS = CrossSchoolStat.__table__
RUBRIC = RubricFactor.__table__
OVERRIDES = RubricOverride.__table__


class RubricNotFound(ValueError):
    pass


class RubricNotApproved(ValueError):
    pass


class RubricService:
    def __init__(self, settings, database, *, taxonomy=None, llm=None):
        self.settings = settings
        self.database = database
        self.taxonomy = taxonomy or get_taxonomy()
        self.llm = llm or LLMClient(settings)
        self.agent = RubricInferenceAgent(self.taxonomy, self.llm)

    async def _load_school(self, school_id: UUID) -> dict:
        async def read(connection):
            row = (await connection.execute(select(SCHOOLS).where(SCHOOLS.c.id == school_id))).mappings().first()
            if row is None:
                raise RubricNotFound("School does not exist")
            return dict(row)
        return await self.database.transaction("read_school_for_rubric", read)

    async def require_approved(self, school_id: UUID) -> dict:
        school = await self._load_school(school_id)
        if school.get("rubric_status") != "approved":
            raise RubricNotApproved("Rubric is not approved for scoring")
        return school

    async def _mark_draft(self, connection, school_id: UUID):
        await connection.execute(update(SCHOOLS).where(SCHOOLS.c.id == school_id).values(
            rubric_status="draft", rubric_approved_at=None, rubric_approved_by=None,
        ))

    async def _load_facts(self, school_id: UUID) -> list[FactView]:
        async def read(connection):
            rows = (await connection.execute(select(FACTS).where(FACTS.c.school_id == school_id))).mappings().all()
            return [
                FactView(
                    id=str(row["id"]), factor_key=row["factor_key"], value=row["value"], unit=row["unit"],
                    confidence=Decimal(str(row["confidence"])), source_type=row["source_type"],
                    source_url=row["source_url"], document_id=str(row["document_id"]) if row["document_id"] else None,
                    raw_text_snippet=row["raw_text_snippet"],
                )
                for row in rows
            ]
        return await self.database.transaction("read_facts_for_rubric", read)

    async def _load_stats(self) -> dict[str, dict]:
        async def read(connection):
            rows = (await connection.execute(select(STATS))).mappings().all()
            return {row["factor_key"]: dict(row) for row in rows}
        return await self.database.transaction("read_stats_for_rubric", read)

    async def _load_manual_overrides(self, school_id: UUID) -> dict[str, RubricFactorDraft]:
        async def read(connection):
            rows = (await connection.execute(select(RUBRIC).where(
                RUBRIC.c.school_id == school_id, RUBRIC.c.weight_source == "manual_override",
            ))).mappings().all()
            drafts = {}
            for row in rows:
                drafts[row["factor_key"]] = RubricFactorDraft(
                    factor_key=row["factor_key"], value=row["value"],
                    weight=Decimal(str(row["weight"])) if row["weight"] is not None else None,
                    weight_source="manual_override",
                    confidence=Decimal(str(row["confidence"])),
                    reasoning=row["reasoning"], source_urls=list(row["source_urls"] or []),
                )
            return drafts
        return await self.database.transaction("read_manual_rubric", read)

    async def persist(self, school_id: UUID, drafts: list[RubricFactorDraft]) -> list[dict]:
        for draft in drafts:
            if draft.weight_source != "manual_override" and not draft.source_urls:
                raise RubricWriteRejected(f"Refusing ungrounded write for {draft.factor_key}")

        async def write(connection):
            saved = []
            for draft in drafts:
                if draft.weight_source == "manual_override":
                    saved.append(draft.model_dump(mode="json"))
                    continue
                await connection.execute(pg_insert(RUBRIC).values(
                    id=uuid4(), school_id=school_id, factor_key=draft.factor_key,
                    value=draft.value, weight=draft.weight, weight_source=draft.weight_source,
                    confidence=draft.confidence, reasoning=draft.reasoning,
                    source_urls=draft.source_urls,
                ).on_conflict_do_update(
                    index_elements=[RUBRIC.c.school_id, RUBRIC.c.factor_key],
                    set_={
                        "value": draft.value, "weight": draft.weight,
                        "weight_source": draft.weight_source, "confidence": draft.confidence,
                        "reasoning": draft.reasoning, "source_urls": draft.source_urls,
                    },
                    where=(RUBRIC.c.weight_source != "manual_override"),
                ))
                saved.append(draft.model_dump(mode="json"))
            await self._mark_draft(connection, school_id)
            return saved
        return await self.database.transaction("upsert_rubric_factors", write)

    async def generate(self, school_id: UUID) -> dict:
        school = await self._load_school(school_id)
        facts = await self._load_facts(school_id)
        stats = await self._load_stats()
        manuals = await self._load_manual_overrides(school_id)
        drafts = await self.agent.infer(
            school_id=school_id, official_url=school["official_url"],
            facts=facts, stats_by_key=stats, preserve_manual=manuals,
        )
        saved = await self.persist(school_id, drafts)
        return {
            "school_id": school_id,
            "factor_count": len(saved),
            "rubric_status": "draft",
            "factors": saved,
        }

    async def list_factors(self, school_id: UUID) -> dict:
        school = await self._load_school(school_id)

        async def read(connection):
            rows = (await connection.execute(select(RUBRIC).where(RUBRIC.c.school_id == school_id)
                                             .order_by(RUBRIC.c.factor_key))).mappings().all()
            return [dict(row) for row in rows]
        factors = await self.database.transaction("list_rubric_factors", read)
        return {
            "school_id": school_id,
            "rubric_status": school.get("rubric_status", "draft"),
            "rubric_approved_at": school.get("rubric_approved_at"),
            "rubric_approved_by": school.get("rubric_approved_by"),
            "factors": factors,
        }

    async def override_factor(
        self, school_id: UUID, factor_key: str, *, value, weight, confidence, reasoning: str,
        editor: str, reason: str, source_urls: list[str] | None = None,
    ) -> dict:
        if factor_key not in self.taxonomy.by_key:
            raise RubricNotFound("Unknown taxonomy factor")
        if not editor.strip() or not reason.strip() or not reasoning.strip():
            raise RubricWriteRejected("editor, reason and reasoning are required")

        async def write(connection):
            current = (await connection.execute(select(RUBRIC).where(
                RUBRIC.c.school_id == school_id, RUBRIC.c.factor_key == factor_key,
            ).with_for_update())).mappings().first()
            if current is None:
                raise RubricNotFound("Rubric factor does not exist; generate the rubric first")
            urls = list(source_urls) if source_urls is not None else list(current["source_urls"] or [])
            if not urls:
                urls = [f"manual:{editor.strip()}"]
            old_value = {
                "value": current["value"], "weight": float(current["weight"]) if current["weight"] is not None else None,
                "confidence": float(current["confidence"]), "weight_source": current["weight_source"],
                "reasoning": current["reasoning"], "source_urls": list(current["source_urls"] or []),
            }
            new_weight = Decimal(str(weight)) if weight is not None else None
            new_confidence = Decimal(str(confidence)) if confidence is not None else Decimal(str(current["confidence"]))
            new_value_payload = {
                "value": value if value is not None else current["value"],
                "weight": float(new_weight) if new_weight is not None else old_value["weight"],
                "confidence": float(new_confidence), "weight_source": "manual_override",
                "reasoning": reasoning.strip(), "source_urls": urls,
            }
            await connection.execute(pg_insert(OVERRIDES).values(
                id=uuid4(), school_id=school_id, factor_key=factor_key,
                old_value=old_value, new_value=new_value_payload,
                editor=editor.strip(), reason=reason.strip(),
            ))
            await connection.execute(update(RUBRIC).where(
                RUBRIC.c.school_id == school_id, RUBRIC.c.factor_key == factor_key,
            ).values(
                value=new_value_payload["value"], weight=new_weight if weight is not None else current["weight"],
                confidence=new_confidence, weight_source="manual_override",
                reasoning=reasoning.strip(), source_urls=urls,
            ))
            await self._mark_draft(connection, school_id)
            return {"factor_key": factor_key, "old_value": old_value, "new_value": new_value_payload,
                    "rubric_status": "draft"}
        return await self.database.transaction("override_rubric_factor", write)

    async def approve(self, school_id: UUID, *, editor: str) -> dict:
        if not editor.strip():
            raise RubricWriteRejected("editor is required to approve a rubric")

        async def write(connection):
            school = (await connection.execute(select(SCHOOLS).where(SCHOOLS.c.id == school_id).with_for_update())).mappings().first()
            if school is None:
                raise RubricNotFound("School does not exist")
            count = await connection.scalar(select(RUBRIC.c.id).where(RUBRIC.c.school_id == school_id).limit(1))
            if count is None:
                raise RubricWriteRejected("Cannot approve an empty rubric; generate factors first")
            approved_at = datetime.now(timezone.utc)
            await connection.execute(update(SCHOOLS).where(SCHOOLS.c.id == school_id).values(
                rubric_status="approved", rubric_approved_at=approved_at, rubric_approved_by=editor.strip(),
            ))
            return {
                "school_id": school_id, "rubric_status": "approved",
                "rubric_approved_at": approved_at, "rubric_approved_by": editor.strip(),
            }
        return await self.database.transaction("approve_rubric", write)

    async def close(self):
        await self.llm.close()
