"""Repair partial document jobs that failed only on ChunkExtractionFailed."""
import asyncio
import json
from datetime import datetime, timezone

from sqlalchemy import text

from app.config import Settings
from app.db.session import Database


async def main():
    db = Database(Settings())

    async def run(conn):
        tables = (await conn.execute(text(
            "SELECT table_name FROM information_schema.tables "
            "WHERE table_schema='school_ai' ORDER BY 1"
        ))).scalars().all()
        print("tables", list(tables))

        rows = (await conn.execute(text("""
            SELECT id, status, error, payload, result,
                   (SELECT count(*) FROM school_ai.school_raw_facts f
                    WHERE f.document_id = CAST(j.payload->>'document_id' AS uuid)) AS facts
            FROM school_ai.jobs j
            WHERE type = 'extract_document'
              AND status = 'failed'
              AND error->>'type' = 'ChunkExtractionFailed'
            ORDER BY COALESCE(completed_at, created_at) DESC
            LIMIT 10
        """))).mappings().all()

        for row in rows:
            result = dict(row["result"] or {})
            chunks = result.get("chunks") or []
            fact_ids = sum(len(c.get("fact_ids") or []) for c in chunks)
            print(json.dumps({
                "id": str(row["id"]),
                "facts_table": row["facts"],
                "fact_ids_in_result": fact_ids,
                "error": row["error"],
                "failed_chunk_indexes": [
                    c.get("index") for c in chunks if c.get("status") == "failed"
                ],
            }, default=str))

            # Soft-succeed: keep facts, convert failed chunks to skipped.
            for chunk in chunks:
                if chunk.get("status") == "failed":
                    chunk["status"] = "succeeded"
                    chunk["skipped"] = "extraction_failed"
            result["chunks"] = chunks
            result["warnings"] = {
                "type": "ChunkExtractionSkipped",
                "chunk_indexes": [
                    c.get("index") for c in chunks if c.get("skipped") == "extraction_failed"
                ],
                "repaired": True,
            }
            doc_id = row["payload"].get("document_id")
            await conn.execute(text("""
                UPDATE school_ai.jobs
                SET status = 'succeeded', error = NULL, result = CAST(:result AS jsonb),
                    completed_at = COALESCE(completed_at, :now)
                WHERE id = :id
            """), {"id": row["id"], "result": json.dumps(result), "now": datetime.now(timezone.utc)})
            if doc_id:
                await conn.execute(text("""
                    UPDATE school_ai.school_documents
                    SET parsed_status = 'complete', parse_error = NULL,
                        parsed_at = COALESCE(parsed_at, :now)
                    WHERE id = CAST(:doc AS uuid)
                """), {"doc": doc_id, "now": datetime.now(timezone.utc)})
            print("repaired", str(row["id"]))

    await db.transaction("repair_partial_jobs", run)
    await db.close()


if __name__ == "__main__":
    asyncio.run(main())
