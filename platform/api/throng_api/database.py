from contextlib import asynccontextmanager
from pathlib import Path

from sqlalchemy import event, select, update
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from throng_api.models import Coordination


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
    async def read(self):
        async with self.sessions() as session:
            yield session

    @asynccontextmanager
    async def write(self):
        async with self.sessions() as session, session.begin():
            # This MUST precede reads: avoids SQLite read-to-write upgrade races and
            # also takes a PostgreSQL row lock. No network IO while this lock is held.
            result = await session.execute(
                update(Coordination)
                .where(Coordination.id == 1)
                .values(revision=Coordination.revision + 1)
            )
            if result.rowcount != 1:
                raise RuntimeError("Database is not initialized; run throng-api migrate")
            yield session

    async def check(self):
        async with self.read() as session:
            if await session.scalar(select(Coordination.id).where(Coordination.id == 1)) != 1:
                raise RuntimeError("Database is not initialized; run throng-api migrate")

    async def close(self):
        await self.engine.dispose()
