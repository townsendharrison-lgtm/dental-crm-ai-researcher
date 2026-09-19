"""Peek compare-related state: rubric approval + recent scores."""
import asyncio
import json

from sqlalchemy import text

from app.config import Settings
from app.db.session import Database


async def main():
    db = Database(Settings())

    async def run(conn):
        schools = (await conn.execute(text("""
            SELECT id::text, name, rubric_status,
                   (SELECT count(*) FROM school_ai.rubric_factors rf WHERE rf.school_id = s.id) AS rubric_rows
            FROM school_ai.schools s
            ORDER BY updated_at DESC NULLS LAST
            LIMIT 10
        """))).mappings().all()
        print("SCHOOLS")
        for row in schools:
            print(json.dumps(dict(row), default=str))

        facts = (await conn.execute(text("""
            SELECT school_id::text, count(*) AS n,
                   count(DISTINCT factor_key) AS keys
            FROM school_ai.school_raw_facts
            GROUP BY school_id
            ORDER BY n DESC
            LIMIT 10
        """))).mappings().all()
        print("FACTS")
        for row in facts:
            print(json.dumps(dict(row), default=str))

    await db.transaction("peek_compare", run)
    await db.close()


if __name__ == "__main__":
    asyncio.run(main())
