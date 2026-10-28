"""Alembic environment for the seed catalog schema."""

import asyncio
import os

from alembic import context
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import create_async_engine

config = context.config


def run_offline() -> None:
    context.configure(
        url=os.environ["DATABASE_URL"],
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def run_online() -> None:
    def do_run_migrations(connection: Connection) -> None:
        context.configure(connection=connection)
        with context.begin_transaction():
            context.run_migrations()

    async def migrate() -> None:
        engine = create_async_engine(os.environ["DATABASE_URL"])
        async with engine.connect() as connection:
            await connection.run_sync(do_run_migrations)
        await engine.dispose()

    asyncio.run(migrate())


if context.is_offline_mode():
    run_offline()
else:
    run_online()
