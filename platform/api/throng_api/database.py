import time
from contextlib import asynccontextmanager
from pathlib import Path

from sqlalchemy import Uuid, event, literal, select, text, update
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from throng_api.models import Coordination
from throng_api.transport import db_wait


def company_query_id(session, company_id):
    """Let PostgreSQL plan a collection query for this company's actual cardinality."""
    # A generic plan can otherwise reuse a global scan warmed by a large company.
    # SQLAlchemy renders the UUID safely and keeps a cached compilation template.
    return literal(
        str(company_id),
        type_=Uuid(as_uuid=False),
        literal_execute=session.bind.dialect.name == "postgresql",
    )


class Database:
    def __init__(self, url: str):
        parsed = make_url(url)
        if parsed.get_backend_name() == "sqlite" and parsed.database not in (None, "", ":memory:"):
            Path(parsed.database).parent.mkdir(parents=True, exist_ok=True)
        self.engine = create_async_engine(url, pool_pre_ping=True, hide_parameters=True)
        self.sessions = async_sessionmaker(self.engine, expire_on_commit=False)
        if parsed.get_backend_name() == "sqlite":

            @event.listens_for(self.engine.sync_engine, "connect")
            def configure_sqlite(connection, _):
                cursor = connection.cursor()
                cursor.execute("PRAGMA foreign_keys=ON")
                cursor.execute("PRAGMA busy_timeout=10000")
                cursor.execute("PRAGMA journal_mode=WAL")
                cursor.close()

    @asynccontextmanager
    async def read(self, *, snapshot=False):
        async with self.sessions() as session:
            if snapshot:
                if self.engine.dialect.name == "sqlite":
                    # SQLite's default driver mode does not begin a transaction for SELECTs.
                    await session.execute(text("BEGIN"))
                else:
                    await session.connection(
                        execution_options={"isolation_level": "REPEATABLE READ"}
                    )
            yield session

    @asynccontextmanager
    async def write(self):
        async with self.sessions() as session, session.begin():
            # This MUST precede reads: avoids SQLite read-to-write upgrade races and
            # also takes a PostgreSQL row lock. No network IO while this lock is held.
            started = time.monotonic()
            timing = db_wait.get()
            try:
                result = await session.execute(
                    update(Coordination)
                    .where(Coordination.id == 1)
                    .values(revision=Coordination.revision + 1)
                )
            finally:
                # Lock failure/cancellation still explains request latency.
                if timing is not None:
                    timing[0] += time.monotonic() - started
            if timing is not None:
                timing[1] += 1
            if result.rowcount != 1:
                raise RuntimeError("Database is not initialized; run throng-api migrate")
            yield session

    async def check(self):
        async with self.read() as session:
            if await session.scalar(select(Coordination.id).where(Coordination.id == 1)) != 1:
                raise RuntimeError("Database is not initialized; run throng-api migrate")

    async def close(self):
        await self.engine.dispose()
