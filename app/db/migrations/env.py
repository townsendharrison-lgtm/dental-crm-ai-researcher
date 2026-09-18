import asyncio

from alembic import context
from sqlalchemy import text

from app.config import get_settings
from app.db.models import Base
from app.db.session import make_engine

settings = context.config.attributes.get("settings") or get_settings()


def include_name(name, type_, parent_names):
    if type_ == "schema":
        return name == settings.db_schema
    return True


def migrate(connection):
    connection.execute(text(f'CREATE SCHEMA IF NOT EXISTS "{settings.db_schema}"'))
    context.configure(connection=connection, target_metadata=Base.metadata,
                      version_table_schema=settings.db_schema, include_schemas=True,
                      include_name=include_name)
    with context.begin_transaction():
        context.run_migrations()


async def online():
    engine = make_engine(settings)
    try:
        async with engine.begin() as connection:
            await connection.run_sync(migrate)
    finally:
        await engine.dispose()


if context.is_offline_mode():
    context.configure(url="postgresql://", target_metadata=Base.metadata,
                      literal_binds=True, version_table_schema=settings.db_schema)
    with context.begin_transaction():
        context.execute(f'CREATE SCHEMA IF NOT EXISTS "{settings.db_schema}"')
        context.run_migrations()
elif context.config.attributes.get("connection") is not None:
    # Tests supply a transaction-owned connection; Alembic must never commit it.
    migrate(context.config.attributes["connection"])
else:
    asyncio.run(online())
