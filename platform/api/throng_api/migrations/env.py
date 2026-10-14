import asyncio

from alembic import context
from alembic.util import CommandError
from sqlalchemy import pool
from sqlalchemy.ext.asyncio import async_engine_from_config

from throng_api.config import Settings
from throng_api.models import Base

config = context.config
if not config.get_main_option("sqlalchemy.url"):
    config.set_main_option("sqlalchemy.url", Settings().database_url.replace("%", "%%"))


def run(connection):
    context.configure(
        connection=connection,
        target_metadata=Base.metadata,
        compare_type=True,
        render_as_batch=connection.dialect.name == "sqlite",
    )
    with context.begin_transaction():
        context.run_migrations()


async def online():
    engine = async_engine_from_config(
        config.get_section(config.config_ini_section), prefix="sqlalchemy.", poolclass=pool.NullPool
    )
    async with engine.connect() as connection:
        await connection.run_sync(run)
    await engine.dispose()


if context.is_offline_mode():
    raise CommandError(
        "Migrations require a live database for resource backfill and schema reflection; "
        "--sql is not supported. Run alembic upgrade head or throng-api migrate."
    )
asyncio.run(online())
