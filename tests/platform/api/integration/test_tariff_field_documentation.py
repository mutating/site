"""Localized tariff field documentation on the migrated billing schema."""

import asyncio
import gc
import os
import pickle
from contextlib import nullcontext
from dataclasses import replace
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import Mock
from uuid import UUID, uuid4
from weakref import ref

import anyio
import asyncpg
import pytest
from alembic import command as alembic
from alembic.util import CommandError
from sqlalchemy import (
    Column,
    Index,
    MetaData,
    Table,
    Text,
    Uuid,
    create_mock_engine,
    delete,
    event,
    insert,
    inspect,
    literal,
    select,
    text,
    true,
    union_all,
    update,
)
from sqlalchemy.dialects import postgresql, sqlite
from sqlalchemy.engine import Engine, default, make_url
from sqlalchemy.exc import DBAPIError, IntegrityError, StatementError
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import Session, aliased, load_only, make_transient_to_detached, raiseload
from sqlalchemy.pool import AsyncAdaptedQueuePool
from sqlalchemy.schema import (
    CreateIndex,
    CreateSchema,
    CreateTable,
    DropSchema,
    DropTable,
)

from tests.platform.api.integration.test_first_tariff_assignments import (
    wait_for_postgres_blocker,
)
from throng_api import tariff_documentation_schema as documentation_schema
from throng_api.account_tariff_schema import TRIGGER_NAME
from throng_api.cli import migration_config
from throng_api.config import Settings
from throng_api.database import Database
from throng_api.first_tariff_schema import postgres_catalog_ddl
from throng_api.migrations.billing import billing_migration_schema
from throng_api.models import (
    Base,
    Coordination,
    PaymentAccount,
    PaymentAccountTariffPlan,
    TariffFieldDocumentation,
    TariffPlan,
    TariffPlanType,
    User,
    remember_documentation_schema,
)

pytestmark = pytest.mark.anyio
TABLE = TariffFieldDocumentation.__table__


@pytest.fixture
async def plan_type(migration_database):
    """Create a tariff schema with a field that can be documented."""
    row = TariffPlanType(name="Storage", field_types={"limit": "int"})
    async with migration_database.write() as session:
        session.add(row)
    return row


@pytest.fixture
async def sqlite_file_catalogue(tmp_path):
    """Provide a file-backed SQLite catalogue with more than one physical connection."""
    database = Database(f"sqlite+aiosqlite:///{tmp_path / 'tariffs.sqlite'}?uri=true")
    try:
        async with database.engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
            await connection.execute(insert(Coordination).values(id=1, revision=0))
        yield database
    finally:
        await database.close()


async def test_field_documentation_round_trip_and_edit(migration_database):
    """Insert a type with its documentation and keep exact Unicode, language and markup."""
    key = "  Объём хранилища 📦 "
    kind = TariffPlanType(name="Storage", field_types={key: "int"})
    russian = TariffFieldDocumentation(
        plan_type=kind,
        field_name=key,
        language_code="ru",
        comment="Лимит в ГиБ.\n\n**Важно**",
    )
    portuguese = TariffFieldDocumentation(
        plan_type=kind, field_name=key, language_code="pt-BR", comment=""
    )
    async with migration_database.write() as session:
        session.add_all([russian, portuguese])
    assert UUID(russian.id).version == UUID(portuguese.id).version == 4
    assert russian.id != portuguese.id
    updated = "Capacidade em GiB.\n" * 500

    async with migration_database.write() as session:
        rows = {
            row.language_code: row
            for row in await session.scalars(
                select(TariffFieldDocumentation).where(TABLE.c.tariff_plan_type_id == kind.id)
            )
        }
        assert set(rows) == {"ru", "pt-BR"}
        assert rows["ru"].field_name == key
        assert rows["ru"].plan_type.field_types == {key: "int"}
        assert rows["ru"].comment == "Лимит в ГиБ.\n\n**Важно**"
        assert rows["pt-BR"].comment == ""
        rows["pt-BR"].comment = updated
        rows["pt-BR"].language_code = "pt-PT"
        await session.delete(rows["ru"])

    async with migration_database.read() as session:
        assert await session.get(TariffFieldDocumentation, russian.id) is None
        row = await session.get(TariffFieldDocumentation, portuguese.id)
        assert (row.field_name, row.language_code, row.comment) == (
            key,
            "pt-PT",
            updated,
        )


async def create_documentation_in_two_schemas(connection, plan_type):
    """Store two different documents with the same UUID in separate schemas."""
    identifier = str(uuid4())
    values = {
        "id": identifier,
        "tariff_plan_type_id": plan_type.id,
        "field_name": "limit",
        "language_code": "en",
    }
    translated = {None: "alternate_tariffs"}
    translated_execution = {"schema_translate_map": translated}
    types = TariffPlanType.__table__
    await connection.execute(insert(TABLE).values(**values, comment="Main"))
    if connection.dialect.name == "postgresql":
        await connection.execute(CreateSchema("alternate_tariffs"))
    else:
        await connection.exec_driver_sql("ATTACH DATABASE ':memory:' AS alternate_tariffs")
    for table in (types, TABLE):
        await connection.execute(CreateTable(table), execution_options=translated_execution)
    await connection.execute(
        insert(types).values(id=plan_type.id, name="Another type", field_types={}),
        execution_options=translated_execution,
    )
    await connection.execute(
        insert(TABLE).values(**values, comment="Alternate"),
        execution_options=translated_execution,
    )
    return identifier, translated


@pytest.mark.parametrize("pass_execute_options", [False, True])
@pytest.mark.parametrize("merge_alternate", [False, True])
async def test_cascade_keeps_loaded_documentation_from_another_schema(
    migration_database, plan_type, pass_execute_options, merge_alternate
):
    """Keep ORM identities separate when the same document UUID exists in two schemas."""
    async with migration_database.engine.begin() as connection:
        identifier, translated = await create_documentation_in_two_schemas(connection, plan_type)
        statement = select(TariffFieldDocumentation).options(raiseload("*"))
        options = {"schema_translate_map": translated, "identity_token": "alternate"}
        if not pass_execute_options:
            statement = statement.execution_options(**options)
            options = {}
        async with AsyncSession(bind=connection, expire_on_commit=False) as session:
            primary = await session.get(
                TariffFieldDocumentation,
                identifier,
                execution_options={"schema_translate_map": {}} if pass_execute_options else {},
            )
            session.expunge(primary)
            primary = await session.merge(primary, load=False)
            alternate = await session.scalar(statement, execution_options=options)
            if merge_alternate:
                session.expunge(alternate)
                alternate = await session.merge(alternate, load=False)
            assert (primary.comment, alternate.comment) == ("Main", "Alternate")
            await session.delete(await session.get(TariffPlanType, plan_type.id))
            await session.flush()
            assert inspect(primary).deleted and inspect(alternate).persistent
            assert await session.scalar(statement, execution_options=options) is alternate
            assert await session.get(TariffFieldDocumentation, identifier) is None


@pytest.mark.parametrize("textual", [False, True])
async def test_cascade_keeps_documentation_loaded_from_a_different_statement_source(
    migration_database, plan_type, textual
):
    """Keep a document loaded from an alternate table through ORM from_statement()."""
    async with migration_database.engine.begin() as connection:
        identifier, _ = await create_documentation_in_two_schemas(connection, plan_type)
        metadata = MetaData()
        TariffPlanType.__table__.to_metadata(metadata, schema="alternate_tariffs")
        alternate = TABLE.to_metadata(metadata, schema="alternate_tariffs")
        source = select(alternate)
        if textual:
            source = text(f"SELECT * FROM {alternate.fullname}").columns(*TABLE.c)
        statement = (
            select(TariffFieldDocumentation)
            .options(raiseload("*"))
            .from_statement(source)
            .execution_options(identity_token="alternate")
        )
        async with AsyncSession(bind=connection) as session:
            primary = await session.get(TariffFieldDocumentation, identifier)
            document = await session.scalar(statement)
            assert (primary.comment, document.comment) == ("Main", "Alternate")
            await session.delete(await session.get(TariffPlanType, plan_type.id))
            await session.flush()
            assert inspect(primary).deleted
            assert inspect(document).persistent
            assert await session.scalar(statement) is document


@pytest.mark.parametrize("mixed_sources", [True, False])
async def test_documentation_from_a_union_respects_all_statement_sources(
    migration_database, plan_type, mixed_sources
):
    """A compound query's first SELECT cannot establish the source of every document."""
    async with migration_database.engine.begin() as connection:
        primary_id, _ = await create_documentation_in_two_schemas(connection, plan_type)
        metadata = MetaData()
        TariffPlanType.__table__.to_metadata(metadata, schema="alternate_tariffs")
        alternate = TABLE.to_metadata(metadata, schema="alternate_tariffs")
        alternate_id = str(uuid4())
        await connection.execute(update(alternate).values(id=alternate_id))
        source = union_all(select(TABLE), select(alternate if mixed_sources else TABLE))
        statement = select(TariffFieldDocumentation).from_statement(source).options(raiseload("*"))
        async with AsyncSession(bind=connection) as session:
            rows = {row.id: row for row in await session.scalars(statement)}
            assert set(rows) == ({primary_id, alternate_id} if mixed_sources else {primary_id})
            await session.delete(await session.get(TariffPlanType, plan_type.id))
            await session.flush()
            assert await connection.scalar(select(TABLE.c.id)) is None
            if mixed_sources:
                assert inspect(rows[alternate_id]).persistent
                assert await session.scalar(statement) is rows[alternate_id]
            else:
                assert inspect(rows[primary_id]).deleted


async def test_merge_with_loading_keeps_the_schema_of_the_destination(
    migration_database, plan_type
):
    """Track the row that a normal ORM merge loads and updates in the destination session."""
    async with migration_database.engine.begin() as connection:
        identifier, translated = await create_documentation_in_two_schemas(connection, plan_type)
        translated_execution = {"schema_translate_map": translated}
        async with AsyncSession(bind=connection) as source_session:
            source = await source_session.get(
                TariffFieldDocumentation,
                identifier,
                options=[raiseload("*")],
                execution_options=translated_execution,
            )
            assert source.comment == "Alternate"
        async with AsyncSession(bind=connection) as session:
            merged = await session.merge(source)
            await session.flush()
            assert await connection.scalar(select(TABLE.c.comment)) == "Alternate"
            await session.delete(await session.get(TariffPlanType, plan_type.id))
            await session.flush()
            assert await connection.scalar(select(TABLE.c.id)) is None
            assert (
                await connection.scalar(select(TABLE.c.id), execution_options=translated_execution)
                == identifier
            )
            assert inspect(merged).deleted
            assert await session.get(TariffFieldDocumentation, identifier) is None


@pytest.mark.parametrize("routing", ["engine", "connection", "search_path"])
async def test_documentation_merge_retains_the_effective_connection_schema(
    migration_database, plan_type, routing
):
    """Keep the same UUID distinct when schema selection is configured outside the query."""
    engine = migration_database.engine
    if routing == "search_path" and engine.dialect.name != "postgresql":
        pytest.skip("PostgreSQL can change unqualified name lookup for a transaction")
    async with engine.begin() as connection:
        identifier, translated = await create_documentation_in_two_schemas(connection, plan_type)

    async def load_alternate(bind):
        """Detach the alternate instance after loading it through the configured bind."""
        async with AsyncSession(bind=bind) as session:
            return await session.get(
                TariffFieldDocumentation,
                identifier,
                identity_token="alternate",
                options=[raiseload("*")],
            )

    if routing == "engine":
        source = await load_alternate(engine.execution_options(schema_translate_map=translated))
    else:
        async with engine.begin() as connection:
            if routing == "search_path":
                await connection.exec_driver_sql(
                    "SET LOCAL search_path TO alternate_tariffs, public"
                )
            else:
                await connection.execution_options(schema_translate_map=translated)
            source = await load_alternate(connection)
    assert source.comment == "Alternate"
    async with AsyncSession(bind=engine) as session:
        primary = await session.get(TariffFieldDocumentation, identifier)
        alternate = await session.merge(source, load=False)
        await session.delete(await session.get(TariffPlanType, plan_type.id))
        await session.flush()
        assert inspect(primary).deleted
        assert inspect(alternate).persistent
        assert await session.get(TariffFieldDocumentation, identifier) is None
        assert (
            await session.get(
                TariffFieldDocumentation,
                identifier,
                identity_token="alternate",
                execution_options={"schema_translate_map": translated},
            )
            is alternate
        )


async def test_documentation_merge_keeps_an_unqualified_source_with_a_translated_destination(
    migration_database, plan_type
):
    """Retain the main document when the destination connection cascades in an attached schema."""
    engine = migration_database.engine
    async with engine.begin() as connection:
        identifier, translated = await create_documentation_in_two_schemas(connection, plan_type)
    async with AsyncSession(bind=engine) as session:
        source = await session.get(
            TariffFieldDocumentation, identifier, identity_token="main", options=[raiseload("*")]
        )
        assert source.comment == "Main"
    alternate = engine.execution_options(schema_translate_map=translated)
    async with AsyncSession(bind=alternate) as session:
        primary = await session.merge(source, load=False)
        document = await session.get(TariffFieldDocumentation, identifier)
        assert document.comment == "Alternate"
        await session.delete(await session.get(TariffPlanType, plan_type.id))
        await session.flush()
        assert inspect(document).deleted
        assert inspect(primary).persistent
        assert (
            await session.get(
                TariffFieldDocumentation,
                identifier,
                identity_token="main",
                execution_options={"schema_translate_map": {}},
            )
            is primary
        )


async def test_documentation_merge_keeps_the_source_engines_default_schema(
    migration_database, plan_type
):
    """Resolve the default-schema alias before moving an instance to a different engine."""
    engine = migration_database.engine
    if engine.dialect.name != "postgresql":
        pytest.skip(
            "PostgreSQL engines can discover different default schemas in the same database"
        )
    async with engine.begin() as connection:
        identifier, translated = await create_documentation_in_two_schemas(connection, plan_type)
    other_engine = create_async_engine(
        engine.url,
        connect_args={"server_settings": {"search_path": "alternate_tariffs, public"}},
        execution_options={"schema_translate_map": {None: None}},
    )
    try:
        async with AsyncSession(bind=other_engine) as session:
            source = await session.get(
                TariffFieldDocumentation,
                identifier,
                identity_token="alternate",
                options=[raiseload("*")],
            )
            assert source.comment == "Alternate"
    finally:
        await other_engine.dispose()
    async with AsyncSession(bind=engine) as session:
        primary = await session.get(TariffFieldDocumentation, identifier)
        alternate = await session.merge(source, load=False)
        await session.delete(await session.get(TariffPlanType, plan_type.id))
        await session.flush()
        assert inspect(primary).deleted
        assert inspect(alternate).persistent
        assert (
            await session.get(
                TariffFieldDocumentation,
                identifier,
                identity_token="alternate",
                execution_options={"schema_translate_map": translated},
            )
            is alternate
        )


async def test_documentation_merge_without_source_schema_uses_the_destination_binding(
    migration_database, plan_type
):
    """Use the current binding for a detached snapshot created from plain field values."""
    values = {
        "id": str(uuid4()),
        "tariff_plan_type_id": plan_type.id,
        "field_name": "limit",
        "language_code": "en",
        "comment": "GiB",
    }
    async with migration_database.engine.begin() as connection:
        await connection.execute(insert(TABLE).values(**values))
        snapshot = TariffFieldDocumentation(**values)
        make_transient_to_detached(snapshot)
        async with AsyncSession(bind=connection) as session:
            merged = await session.merge(snapshot, load=False)
            await session.delete(await session.get(TariffPlanType, plan_type.id))
            await session.flush()
            assert inspect(merged).deleted


async def test_documentation_merge_of_a_plain_snapshot_keeps_the_cached_destinations_schema(
    migration_database, plan_type
):
    """Preserve a loaded target's physical schema when its merge source has no provenance."""
    async with migration_database.engine.begin() as connection:
        identifier, translated = await create_documentation_in_two_schemas(connection, plan_type)
        options = {"schema_translate_map": translated}
        async with AsyncSession(bind=connection) as session:
            alternate = await session.get(
                TariffFieldDocumentation,
                identifier,
                execution_options=options,
                options=[raiseload("*")],
            )
            assert alternate.comment == "Alternate"
            snapshot = TariffFieldDocumentation(
                id=identifier,
                tariff_plan_type_id=plan_type.id,
                field_name="limit",
                language_code="en",
                comment="Alternate",
            )
            make_transient_to_detached(snapshot)
            assert await session.merge(snapshot, load=False) is alternate
            await session.delete(await session.get(TariffPlanType, plan_type.id))
            await session.flush()
            assert inspect(alternate).persistent
            assert (
                await session.get(TariffFieldDocumentation, identifier, execution_options=options)
                is alternate
            )


async def test_cascade_keeps_documents_from_another_database_binding(
    migration_database, plan_type, memory_database
):
    """Only reconcile rows in the database that actually executed the cascading delete."""
    primary = TariffFieldDocumentation(
        tariff_plan_type_id=plan_type.id, field_name="limit", language_code="en", comment="Primary"
    )
    secondary = TariffFieldDocumentation(
        plan_type=TariffPlanType(name="Another database", field_types={"limit": "int"}),
        field_name="limit",
        language_code="en",
        comment="Secondary",
    )
    async with migration_database.write() as session:
        session.add(primary)
    async with memory_database.write() as session:
        session.add(secondary)
    async with migration_database.write() as session:
        cached_primary = await session.get(TariffFieldDocumentation, primary.id)
        other_statement = (
            select(TariffFieldDocumentation)
            .where(TariffFieldDocumentation.id == secondary.id)
            .options(raiseload("*"))
        )
        other_bind = {"bind": memory_database.engine.sync_engine}
        cached_secondary = await session.scalar(other_statement, bind_arguments=other_bind)
        assert cached_secondary.comment == "Secondary"
        await session.delete(await session.get(TariffPlanType, plan_type.id))
        await session.flush()
        assert inspect(cached_primary).deleted
        assert inspect(cached_secondary).persistent
        assert await session.scalar(other_statement, bind_arguments=other_bind) is cached_secondary


async def test_cascade_uses_the_bind_that_a_routed_documentation_select_actually_used(
    migration_database, memory_database, plan_type
):
    """An ORM load observes the bind already chosen by a dynamic Session router."""
    archive = memory_database.engine.sync_engine
    primary = migration_database.engine.sync_engine
    document = TariffFieldDocumentation(
        plan_type=TariffPlanType(name="Archive", field_types={"limit": "int"}),
        field_name="limit",
        language_code="en",
        comment="Archive",
    )
    async with memory_database.write() as session:
        session.add(document)
    chosen = []

    class RoutingSession(Session):
        def get_bind(self, mapper=None, *, clause=None, **options):
            if clause is not None and clause.get_execution_options().get("use_catalogue_reader"):
                bind = primary if chosen else archive
                chosen.append(bind)
                return bind
            return super().get_bind(mapper, clause=clause, **options)

    statement = (
        select(TariffFieldDocumentation)
        .where(TariffFieldDocumentation.id == document.id)
        .options(raiseload("*"))
    )
    async with AsyncSession(
        bind=migration_database.engine, sync_session_class=RoutingSession
    ) as session:
        archived = await session.scalar(statement.execution_options(use_catalogue_reader=True))
        assert archived.comment == "Archive"
        await session.delete(await session.get(TariffPlanType, plan_type.id))
        await session.flush()
        assert inspect(archived).persistent
        assert await session.scalar(statement, bind_arguments={"bind": archive}) is archived
        assert chosen == [archive]


@pytest.mark.parametrize("dispose_pool", [False, True])
async def test_cascade_works_after_a_routed_option_engine_is_collected(
    migration_database, plan_type, dispose_pool
):
    """A transient SQLAlchemy OptionEngine and its parent keep sharing the same live pool."""
    database = migration_database
    async with database.write() as session:
        session.add(
            TariffFieldDocumentation(
                tariff_plan_type_id=plan_type.id,
                field_name="limit",
                language_code="en",
                comment="Shared connection pool",
            )
        )
    bindings = []

    class RoutingSession(Session):
        def get_bind(self, mapper=None, *, clause=None, **options):
            bind = super().get_bind(mapper, clause=clause, **options)
            if clause is not None and clause.get_execution_options().get("use_catalogue_reader"):
                bind = bind.execution_options(schema_translate_map={})
                bindings.append(ref(bind))
            return bind

    async with AsyncSession(
        bind=database.engine, sync_session_class=RoutingSession, expire_on_commit=False
    ) as session:
        statement = (
            select(TariffFieldDocumentation)
            .options(raiseload("*"))
            .execution_options(use_catalogue_reader=True)
        )
        document = await session.scalar(statement)
        await session.commit()
        gc.collect()
        assert len(bindings) == 1 and bindings[0]() is None
        if dispose_pool:
            await database.engine.dispose()
        await session.delete(await session.get(TariffPlanType, plan_type.id))
        await session.flush()
        assert await session.scalar(select(TABLE.c.id)) is None
        assert inspect(document).deleted
        assert await session.get(TariffFieldDocumentation, document.id) is None


@pytest.mark.parametrize("shared", [False, True])
async def test_sqlite_cascade_distinguishes_independent_main_memory_databases(shared):
    """Use the database behind `main` instead of equating connections from one pool."""
    filename = f"file:{uuid4().hex}?mode=memory&cache=shared&uri=true" if shared else ":memory:"
    engine = create_async_engine(f"sqlite+aiosqlite:///{filename}", poolclass=AsyncAdaptedQueuePool)
    other_bind = engine.execution_options(schema_translate_map={})
    kind = str(uuid4())
    identifier = str(uuid4())
    try:
        async with engine.connect() as primary, other_bind.connect() as secondary:
            for connection in (primary, secondary):
                async with connection.begin():
                    await connection.exec_driver_sql("PRAGMA foreign_keys=ON")
                    if connection is secondary and shared:
                        continue
                    await connection.run_sync(Base.metadata.create_all)
                    await connection.execute(
                        insert(TariffPlanType).values(id=kind, name="Memory", field_types={})
                    )
                    await connection.execute(
                        insert(TABLE).values(
                            id=identifier,
                            tariff_plan_type_id=kind,
                            field_name="limit",
                            language_code="en",
                            comment="Memory",
                        )
                    )
            async with AsyncSession(bind=primary, expire_on_commit=False) as session:
                document = await session.scalar(
                    select(TariffFieldDocumentation).options(raiseload("*")),
                    bind_arguments={"bind": secondary.sync_connection},
                )
                await session.delete(await session.get(TariffPlanType, kind))
                await session.flush()
                assert inspect(document).deleted is shared
                assert inspect(document).persistent is not shared
                await session.commit()
            assert await secondary.scalar(select(TABLE.c.id)) == (None if shared else identifier)
    finally:
        await engine.dispose()


@pytest.mark.parametrize("drop_source", [False, True])
async def test_sqlite_cascade_skips_unavailable_temporary_documentation(
    sqlite_file_catalogue, drop_source
):
    """Skip a temporary relation after it is dropped or from a different connection."""
    database = sqlite_file_catalogue
    unrelated = TariffPlanType(name="Unrelated", field_types={})
    document = TariffFieldDocumentation(
        plan_type=TariffPlanType(name="Persistent", field_types={"limit": "int"}),
        field_name="limit",
        language_code="en",
        comment="Main",
    )
    async with database.write() as session:
        session.add_all([document, unrelated])
    engine = database.engine
    other_bind = engine.execution_options(schema_translate_map={})
    async with engine.connect() as primary, other_bind.connect() as secondary:
        async with secondary.begin():
            await secondary.exec_driver_sql(
                f"CREATE TEMP TABLE {TABLE.name} AS SELECT * FROM main.{TABLE.name}"
            )
            await secondary.exec_driver_sql(f"UPDATE temp.{TABLE.name} SET comment = 'Temporary'")
        bound = secondary if drop_source else primary
        async with AsyncSession(bind=bound, expire_on_commit=False) as session:
            temporary = await session.scalar(
                select(TariffFieldDocumentation).options(raiseload("*")),
                bind_arguments={"bind": secondary.sync_connection},
            )
            assert temporary.comment == "Temporary"
            if drop_source:
                await session.commit()
                await secondary.exec_driver_sql(f"DROP TABLE temp.{TABLE.name}")
            await session.delete(await session.get(TariffPlanType, unrelated.id))
            await session.flush()
            assert inspect(temporary).persistent
            assert await primary.scalar(select(TABLE.c.comment)) == "Main"


@pytest.mark.parametrize(
    ("source", "explicit_schema"),
    [("kept", False), ("dropped", False), ("dropped", True), ("other_connection", True)],
)
async def test_postgres_cascade_skips_unavailable_temporary_documentation(
    migration_database, plan_type, source, explicit_schema
):
    """Resolve pg_temp on its owning connection and skip unavailable temporary tables."""
    database = migration_database
    engine = database.engine
    if engine.dialect.name != "postgresql":
        pytest.skip("PostgreSQL gives each connection a separate temporary schema")
    unrelated = TariffPlanType(name="Unrelated", field_types={})
    document = TariffFieldDocumentation(
        tariff_plan_type_id=plan_type.id, field_name="limit", language_code="en", comment="Main"
    )
    async with database.write() as session:
        session.add_all([document, unrelated])
    other_bind = engine.execution_options(schema_translate_map={})
    async with engine.connect() as primary, other_bind.connect() as secondary:
        async with secondary.begin():
            await secondary.exec_driver_sql(
                f"CREATE TEMP TABLE {TABLE.name} AS SELECT * FROM public.{TABLE.name}"
            )
            await secondary.exec_driver_sql(
                f"UPDATE pg_temp.{TABLE.name} SET comment = 'Temporary'"
            )
        bound = primary if source == "other_connection" else secondary
        options = {"schema_translate_map": {None: "pg_temp"}} if explicit_schema else {}
        async with AsyncSession(bind=bound, expire_on_commit=False) as session:
            temporary = await session.scalar(
                select(TariffFieldDocumentation).options(raiseload("*")),
                execution_options=options,
                bind_arguments={"bind": secondary.sync_connection},
            )
            assert temporary.comment == "Temporary"
            if source == "dropped":
                await session.commit()
                await secondary.exec_driver_sql(f"DROP TABLE pg_temp.{TABLE.name}")
            await session.delete(await session.get(TariffPlanType, unrelated.id))
            await session.flush()
            assert inspect(temporary).persistent
            assert await primary.scalar(select(TABLE.c.comment)) == "Main"


@pytest.mark.parametrize("change", ["drop", "replace", "relocate"])
async def test_postgres_cascade_tracks_the_physical_documentation_relation(
    migration_database, plan_type, change
):
    """An unrelated cascade can proceed while a previously loaded relation changes or disappears."""
    database = migration_database
    if database.engine.dialect.name != "postgresql":
        pytest.skip("PostgreSQL can change a permanent table's name and retain its OID")
    async with database.engine.begin() as connection:
        identifier, translated = await create_documentation_in_two_schemas(connection, plan_type)
    options = {"schema_translate_map": translated}
    async with AsyncSession(bind=database.engine, expire_on_commit=False) as session:
        alternate = await session.scalar(
            select(TariffFieldDocumentation).options(raiseload("*")), execution_options=options
        )
        assert alternate.comment == "Alternate"
        await session.commit()
        async with database.engine.begin() as connection:
            if change == "relocate":
                await connection.execute(CreateSchema("relocated_docs"))
                await connection.exec_driver_sql(
                    f"ALTER TABLE alternate_tariffs.{TABLE.name} SET SCHEMA relocated_docs"
                )
            else:
                await connection.execute(DropTable(TABLE), execution_options=options)
                if change == "replace":
                    await connection.execute(CreateTable(TABLE), execution_options=options)
        await session.delete(await session.get(TariffPlanType, plan_type.id))
        await session.flush()
        assert inspect(alternate).persistent
        assert await session.scalar(select(TABLE.c.id).where(TABLE.c.id == identifier)) is None


@pytest.mark.parametrize("replace", [False, True])
async def test_postgres_cascade_handles_relocation_between_identity_lookup_and_read(
    migration_database, plan_type, replace
):
    """Keep the unrelated delete and a live archive snapshot through a concurrent table move."""
    database = migration_database
    engine = database.engine
    if engine.dialect.name != "postgresql":
        pytest.skip("PostgreSQL keeps relation OIDs across schema changes")
    async with engine.begin() as connection:
        _, translated = await create_documentation_in_two_schemas(connection, plan_type)
        await connection.execute(CreateSchema("relocated_docs"))
    options = {"schema_translate_map": translated}
    async with AsyncSession(bind=engine, expire_on_commit=False) as session:
        archive = await session.scalar(
            select(TariffFieldDocumentation).options(raiseload("*")), execution_options=options
        )
        assert archive.comment == "Alternate"
        await session.commit()
        moved = False

        async def move(_driver):
            """Commit the move on a second connection before the pending SELECT reaches PostgreSQL."""
            async with engine.begin() as ddl:
                await ddl.exec_driver_sql("SET LOCAL lock_timeout = '1s'")
                await ddl.exec_driver_sql(
                    f"ALTER TABLE alternate_tariffs.{TABLE.name} SET SCHEMA relocated_docs"
                )
                if replace:
                    await ddl.execute(CreateTable(TABLE), execution_options=options)

        def before_read(connection, _cursor, statement, *_):
            """Interleave the move after the OID lookup, before reading the physical table."""
            nonlocal moved
            if not moved and f"FROM alternate_tariffs.{TABLE.name}" in statement:
                moved = True
                connection.connection.dbapi_connection.run_async(move)

        event.listen(engine.sync_engine, "before_cursor_execute", before_read)
        try:
            await session.delete(await session.get(TariffPlanType, plan_type.id))
            await session.flush()
        finally:
            event.remove(engine.sync_engine, "before_cursor_execute", before_read)
        assert moved and inspect(archive).persistent
        assert await session.get(TariffPlanType, plan_type.id) is None
        assert await session.scalar(select(TABLE.c.id)) is None
        relocated = {"schema_translate_map": {None: "relocated_docs"}}
        assert await session.scalar(select(TABLE.c.id), execution_options=relocated) == archive.id


@pytest.mark.parametrize("reattach", [False, True])
async def test_sqlite_cascade_tracks_a_file_across_detach(
    sqlite_file_catalogue, tmp_path, reattach
):
    """Resolve a file's current alias and retain its rows while that file is absent."""
    database = sqlite_file_catalogue
    unrelated = TariffPlanType(name="Unrelated", field_types={})
    async with database.write() as session:
        session.add(unrelated)
    filename = str(tmp_path / "archive.sqlite")
    schema = "docs_archive"
    options = {"schema_translate_map": {None: schema}}
    kind = str(uuid4())
    async with database.engine.connect() as connection:
        async with connection.begin():
            await connection.exec_driver_sql(f"ATTACH DATABASE ? AS {schema}", (filename,))
            await connection.execute(
                CreateTable(TariffPlanType.__table__), execution_options=options
            )
            await connection.execute(CreateTable(TABLE), execution_options=options)
            await connection.execute(
                insert(TariffPlanType).values(id=kind, name="Archive", field_types={}),
                execution_options=options,
            )
            await connection.execute(
                insert(TABLE).values(
                    tariff_plan_type_id=kind,
                    field_name="limit",
                    language_code="en",
                    comment="Archive",
                ),
                execution_options=options,
            )
        async with AsyncSession(bind=connection, expire_on_commit=False) as session:
            archived = await session.scalar(
                select(TariffFieldDocumentation).options(raiseload("*")), execution_options=options
            )
            assert archived.comment == "Archive"
            await session.commit()
            await connection.exec_driver_sql(f"DETACH DATABASE {schema}")
            if reattach:
                await connection.exec_driver_sql("ATTACH DATABASE ? AS restored_docs", (filename,))
                await connection.execution_options(schema_translate_map={None: "restored_docs"})
                await session.delete(await session.get(TariffPlanType, kind))
                await session.flush()
                assert inspect(archived).deleted
                assert await session.get(TariffFieldDocumentation, archived.id) is None
                return
            await session.delete(await session.get(TariffPlanType, unrelated.id))
            await session.flush()
            assert inspect(archived).persistent
            await connection.exec_driver_sql(f"ATTACH DATABASE ? AS {schema}", (filename,))
            assert (
                await connection.scalar(select(TABLE.c.comment), execution_options=options)
                == "Archive"
            )


@pytest.mark.parametrize(
    ("replace_attachment", "peer_binding"),
    [(False, "literal"), (False, "named"), (False, "explicit_vfs"), (True, "literal")],
)
async def test_sqlite_cascade_tracks_shared_and_replaced_memory_attachments(
    sqlite_file_catalogue, replace_attachment, peer_binding
):
    """Recognize a shared attachment and distinguish a new database reusing its alias."""
    database = sqlite_file_catalogue
    unrelated = TariffPlanType(name="Unrelated", field_types={})
    async with database.write() as session:
        session.add(unrelated)
    uri = f"file:{uuid4().hex}?mode=memory&cache=shared"
    options = {"schema_translate_map": {None: "shared_docs"}}
    kind = str(uuid4())
    engine = database.engine
    other_bind = engine.execution_options(schema_translate_map={})
    async with engine.connect() as primary, other_bind.connect() as secondary:
        async with primary.begin():
            await primary.exec_driver_sql("ATTACH DATABASE ? AS shared_docs", (uri,))
            for table in (TariffPlanType.__table__, TABLE):
                await primary.execute(CreateTable(table), execution_options=options)
            await primary.execute(
                insert(TariffPlanType).values(id=kind, name="Shared", field_types={}),
                execution_options=options,
            )
            await primary.execute(
                insert(TABLE).values(
                    tariff_plan_type_id=kind,
                    field_name="limit",
                    language_code="en",
                    comment="Shared",
                ),
                execution_options=options,
            )
        async with secondary.begin():
            if peer_binding == "explicit_vfs":
                default_vfs = "win32" if os.name == "nt" else "unix"
                await secondary.exec_driver_sql(
                    "ATTACH DATABASE ? AS shared_docs", (f"{uri}&vfs={default_vfs}",)
                )
            elif peer_binding == "named":
                await secondary.exec_driver_sql("ATTACH DATABASE :uri AS shared_docs", {"uri": uri})
            else:
                await secondary.exec_driver_sql(f"ATTACH '{uri}' AS shared_docs")
        async with AsyncSession(bind=primary, expire_on_commit=False) as session:
            bind = {} if replace_attachment else {"bind": secondary.sync_connection}
            document = await session.scalar(
                select(TariffFieldDocumentation).options(raiseload("*")),
                execution_options=options,
                bind_arguments=bind,
            )
            assert document.comment == "Shared"
            if replace_attachment:
                await session.commit()
                await primary.exec_driver_sql("DETACH shared_docs")
                await primary.exec_driver_sql("ATTACH ':memory:' AS shared_docs")
                for table in (TariffPlanType.__table__, TABLE):
                    await primary.execute(CreateTable(table), execution_options=options)
                await session.delete(await session.get(TariffPlanType, unrelated.id))
                await session.flush()
                assert inspect(document).persistent
                assert (
                    await secondary.scalar(select(TABLE.c.comment), execution_options=options)
                    == "Shared"
                )
            else:
                await primary.execution_options(**options)
                await session.delete(await session.get(TariffPlanType, kind))
                await session.flush()
                assert inspect(document).deleted
                assert await primary.scalar(select(TABLE.c.id)) is None


async def test_sqlite_cascade_keeps_non_ascii_attachment_names_distinct(
    sqlite_file_catalogue, tmp_path
):
    """Use SQLite's ASCII-only case folding when resolving an attached schema."""
    database = sqlite_file_catalogue
    kind = str(uuid4())
    async with database.engine.begin() as connection:
        for schema, filename in (("Ä", "first.sqlite"), ("ä", "second.sqlite")):
            await connection.exec_driver_sql(
                "ATTACH DATABASE ? AS ?", (str(tmp_path / filename), schema)
            )
            options = {"schema_translate_map": {None: schema}}
            for table in (TariffPlanType.__table__, TABLE):
                await connection.execute(CreateTable(table), execution_options=options)
            await connection.execute(
                insert(TariffPlanType).values(id=kind, name="Archive", field_types={}),
                execution_options=options,
            )
        lower = {"schema_translate_map": {None: "ä"}}
        await connection.execute(
            insert(TABLE).values(
                tariff_plan_type_id=kind,
                field_name="limit",
                language_code="en",
                comment="Lowercase attachment",
            ),
            execution_options=lower,
        )
        async with AsyncSession(bind=connection) as session:
            document = await session.scalar(
                select(TariffFieldDocumentation).options(raiseload("*")), execution_options=lower
            )
            await connection.execution_options(schema_translate_map={None: "Ä"})
            await session.delete(await session.get(TariffPlanType, kind))
            await session.flush()
            assert inspect(document).persistent
            assert (
                await connection.scalar(select(TABLE.c.comment), execution_options=lower)
                == "Lowercase attachment"
            )


@pytest.mark.parametrize(
    "uri", ["file:?mode=memory&cache=shared", "file:%00hidden?mode=memory&cache=shared"]
)
async def test_sqlite_cascade_keeps_unnamed_memory_attachments_separate(sqlite_file_catalogue, uri):
    """An empty memory URI name creates private databases even with cache=shared."""
    engine = sqlite_file_catalogue.engine
    other_bind = engine.execution_options(schema_translate_map={})
    options = {"schema_translate_map": {None: "unnamed_docs"}}
    parameters = {"uri": uri, "schema": "unnamed_docs"}
    primary_type, secondary_type = str(uuid4()), str(uuid4())
    async with engine.connect() as primary, other_bind.connect() as secondary:
        for connection, kind in ((primary, primary_type), (secondary, secondary_type)):
            async with connection.begin():
                await connection.exec_driver_sql("ATTACH DATABASE :uri AS :schema", parameters)
                for table in (TariffPlanType.__table__, TABLE):
                    await connection.execute(CreateTable(table), execution_options=options)
                await connection.execute(
                    insert(TariffPlanType).values(id=kind, name="Local", field_types={}),
                    execution_options=options,
                )
        async with secondary.begin():
            await secondary.execute(
                insert(TABLE).values(
                    tariff_plan_type_id=secondary_type,
                    field_name="limit",
                    language_code="en",
                    comment="Independent",
                ),
                execution_options=options,
            )
        await primary.execution_options(**options)
        async with AsyncSession(bind=primary) as session:
            document = await session.scalar(
                select(TariffFieldDocumentation).options(raiseload("*")),
                execution_options=options,
                bind_arguments={"bind": secondary.sync_connection},
            )
            await session.delete(await session.get(TariffPlanType, primary_type))
            await session.flush()
            assert inspect(document).persistent
            assert (
                await secondary.scalar(select(TABLE.c.comment), execution_options=options)
                == "Independent"
            )


@pytest.mark.parametrize("release", ["detach", "dispose"])
async def test_sqlite_cascade_keeps_a_snapshot_from_a_closed_shared_memory_attachment(
    sqlite_file_catalogue, release
):
    """A closed named memory database and its replacement have separate ORM identities."""
    database = sqlite_file_catalogue
    engine = database.engine
    unrelated = TariffPlanType(name="Unrelated", field_types={})
    async with database.write() as session:
        session.add(unrelated)
    uri = f"file:{uuid4().hex}?mode=memory&cache=shared"
    options = {"schema_translate_map": {None: "archive"}}
    async with engine.connect() as connection:
        async with connection.begin():
            await connection.exec_driver_sql("ATTACH DATABASE ? AS archive", (uri,))
            for table in (TariffPlanType.__table__, TABLE):
                await connection.execute(CreateTable(table), execution_options=options)
            kind = str(uuid4())
            await connection.execute(
                insert(TariffPlanType).values(id=kind, name="Archive", field_types={}),
                execution_options=options,
            )
            await connection.execute(
                insert(TABLE).values(
                    tariff_plan_type_id=kind,
                    field_name="limit",
                    language_code="en",
                    comment="Original attachment",
                ),
                execution_options=options,
            )
        async with AsyncSession(bind=connection, expire_on_commit=False) as session:
            old = await session.scalar(
                select(TariffFieldDocumentation).options(raiseload("*")), execution_options=options
            )
            assert old.comment == "Original attachment"
        if release == "detach":
            await connection.exec_driver_sql("DETACH DATABASE archive")
    if release == "dispose":
        await engine.dispose()
    async with engine.begin() as connection:
        await connection.exec_driver_sql("ATTACH DATABASE ? AS archive", (uri,))
        for table in (TariffPlanType.__table__, TABLE):
            await connection.execute(CreateTable(table), execution_options=options)
        async with AsyncSession(bind=connection) as session:
            snapshot = await session.merge(old, load=False)
            await session.delete(await session.get(TariffPlanType, unrelated.id))
            await session.flush()
            assert inspect(snapshot).persistent
            assert await connection.scalar(select(TABLE.c.id), execution_options=options) is None


@pytest.mark.parametrize(
    "source",
    ["table", "subquery", "labelled_id", "text_id", "uuid_object", "literal", "primary_alias"],
)
async def test_documentation_cascade_tracks_the_actual_source_of_an_orm_alias(
    migration_database, plan_type, source
):
    """Keep aliased ORM results scoped to the relation that supplied their primary key."""
    async with migration_database.engine.begin() as connection:
        identifier, _ = await create_documentation_in_two_schemas(connection, plan_type)
        metadata = MetaData()
        TariffPlanType.__table__.to_metadata(metadata, schema="alternate_tariffs")
        alternate_table = TABLE.to_metadata(metadata, schema="alternate_tariffs")
        if source == "table":
            selectable = alternate_table
        elif source == "subquery":
            selectable = select(alternate_table).subquery()
        elif source in ("labelled_id", "text_id", "uuid_object"):
            key = "document_key" if source == "labelled_id" else "id"
            key_type = Text() if source == "text_id" else Uuid(as_uuid=source == "uuid_object")
            if source == "uuid_object":
                identifier = UUID(identifier)
            labelled = Table(
                "labelled_documentation",
                metadata,
                Column(key, key_type, primary_key=True),
                *(
                    Column(name, Text)
                    for name in ("tariff_plan_type_id", "field_name", "language_code", "comment")
                ),
            )
            await connection.execute(CreateTable(labelled))
            await connection.execute(
                insert(labelled).values(
                    **{key: identifier},
                    tariff_plan_type_id=plan_type.id,
                    field_name="limit",
                    language_code="en",
                    comment="Labelled",
                )
            )
            selectable = select(
                labelled.c[key].label("id"),
                labelled.c.tariff_plan_type_id,
                labelled.c.field_name,
                labelled.c.language_code,
                labelled.c.comment,
            ).subquery()
        elif source == "literal":
            selectable = select(
                literal(identifier).label("id"),
                literal(plan_type.id).label("tariff_plan_type_id"),
                literal("limit").label("field_name"),
                literal("en").label("language_code"),
                literal("Projected").label("comment"),
            ).subquery()
        else:
            selectable = TABLE.alias()
        entity = aliased(TariffFieldDocumentation, selectable, adapt_on_names=True)
        statement = select(entity).where(entity.id == identifier).options(raiseload("*"))
        async with AsyncSession(bind=connection) as session:
            document = await session.scalar(statement)
            assert (
                document.comment
                == {
                    "table": "Alternate",
                    "subquery": "Alternate",
                    "labelled_id": "Labelled",
                    "text_id": "Labelled",
                    "uuid_object": "Labelled",
                    "literal": "Projected",
                    "primary_alias": "Main",
                }[source]
            )
            await session.delete(await session.get(TariffPlanType, plan_type.id))
            await session.flush()
            if source == "primary_alias":
                assert inspect(document).deleted
                assert await session.scalar(statement) is None
            else:
                assert inspect(document).persistent
                assert await session.scalar(statement) is document


async def test_refresh_updates_documentation_provenance_after_an_aliased_load(
    migration_database, plan_type
):
    """A refresh uses the mapped table and replaces the alias source's physical scope."""
    async with migration_database.engine.begin() as connection:
        identifier, _ = await create_documentation_in_two_schemas(connection, plan_type)
        metadata = MetaData()
        TariffPlanType.__table__.to_metadata(metadata, schema="alternate_tariffs")
        alternate_table = TABLE.to_metadata(metadata, schema="alternate_tariffs")
        entity = aliased(TariffFieldDocumentation, alternate_table, adapt_on_names=True)
        statement = select(entity).where(entity.id == identifier).options(raiseload("*"))
        async with AsyncSession(bind=connection) as session:
            document = await session.scalar(statement)
            assert document.comment == "Alternate"
            await session.refresh(document, attribute_names=["comment"])
            assert document.comment == "Main"
            await session.delete(await session.get(TariffPlanType, plan_type.id))
            await session.flush()
            assert inspect(document).deleted
            alternate = await session.scalar(statement)
            assert alternate is not document and alternate.comment == "Alternate"


@pytest.mark.parametrize("query", ["get", "select"])
async def test_populate_existing_can_load_documentation_into_a_new_session(
    migration_database, plan_type, query
):
    """Accept populate_existing when SQLAlchemy has no previous loader path."""
    async with migration_database.write() as session:
        document = TariffFieldDocumentation(
            tariff_plan_type_id=plan_type.id, field_name="limit", language_code="en", comment="GiB"
        )
        session.add(document)
    async with migration_database.read() as session:
        if query == "get":
            loaded = await session.get(
                TariffFieldDocumentation, document.id, populate_existing=True
            )
        else:
            loaded = await session.scalar(
                select(TariffFieldDocumentation)
                .where(TABLE.c.id == document.id)
                .execution_options(populate_existing=True)
            )
        assert loaded.comment == "GiB"


async def test_populate_existing_updates_the_schema_of_a_previous_documentation_alias(
    migration_database, plan_type
):
    """Read the current query's table even when ORM keeps the alias's old loader path."""
    async with migration_database.engine.begin() as connection:
        identifier, _ = await create_documentation_in_two_schemas(connection, plan_type)
        metadata = MetaData()
        TariffPlanType.__table__.to_metadata(metadata, schema="alternate_tariffs")
        alternate_table = TABLE.to_metadata(metadata, schema="alternate_tariffs")
        entity = aliased(TariffFieldDocumentation, alternate_table, adapt_on_names=True)
        async with AsyncSession(bind=connection) as session:
            document = await session.scalar(select(entity).options(raiseload("*")))
            assert document.comment == "Alternate"
            assert (
                await session.scalar(
                    select(TariffFieldDocumentation)
                    .where(TABLE.c.id == identifier)
                    .execution_options(populate_existing=True)
                )
                is document
            )
            assert document.comment == "Main"
            await session.delete(await session.get(TariffPlanType, plan_type.id))
            await session.flush()
            assert inspect(document).deleted


@pytest.mark.parametrize("populate_existing", [False, True])
async def test_documentation_cascade_handles_two_alias_sources_in_one_result(
    migration_database, plan_type, populate_existing
):
    """Keep unrelated aliases when their source cannot be distinguished from old loader paths."""
    async with migration_database.engine.begin() as connection:
        primary_id, translated = await create_documentation_in_two_schemas(connection, plan_type)
        alternate_id = str(uuid4())
        await connection.execute(
            TABLE.update().where(TABLE.c.id == primary_id).values(id=alternate_id),
            execution_options={"schema_translate_map": translated},
        )
        metadata = MetaData()
        TariffPlanType.__table__.to_metadata(metadata, schema="alternate_tariffs")
        alternate_table = TABLE.to_metadata(metadata, schema="alternate_tariffs")
        entity = aliased(TariffFieldDocumentation, alternate_table, adapt_on_names=True)
        statement = (
            select(TariffFieldDocumentation, entity)
            .join_from(TABLE, alternate_table, true())
            .options(raiseload("*"))
            .execution_options(populate_existing=populate_existing)
        )
        async with AsyncSession(bind=connection) as session:
            primary, alternate = (await session.execute(statement)).one()
            assert (primary.id, alternate.id) == (primary_id, alternate_id)
            await session.delete(await session.get(TariffPlanType, plan_type.id))
            await session.flush()
            if not populate_existing:
                assert inspect(primary).deleted
            assert inspect(alternate).persistent


@pytest.mark.parametrize("preload", [False, True])
@pytest.mark.parametrize("explicit_schema", [False, True])
async def test_documentation_self_join_reconciles_a_cascade_with_populate_existing(
    migration_database, plan_type, preload, explicit_schema
):
    """Treat two aliases of one physical table as a single source during identity reconciliation."""
    documents = [
        TariffFieldDocumentation(
            tariff_plan_type_id=plan_type.id, field_name=key, language_code="en", comment=key
        )
        for key in ("first", "second")
    ]
    async with migration_database.write() as session:
        session.add_all(documents)
    first, second = [document.id for document in documents]
    async with migration_database.write() as session:
        if preload:
            assert await session.get(TariffFieldDocumentation, first) is not None
            assert await session.get(TariffFieldDocumentation, second) is not None
        if explicit_schema:
            schema = "main" if migration_database.engine.dialect.name == "sqlite" else "public"
            metadata = MetaData()
            TariffPlanType.__table__.to_metadata(metadata, schema=schema)
            table_alias = TABLE.to_metadata(metadata, schema=schema).alias()
            other = aliased(TariffFieldDocumentation, table_alias, adapt_on_names=True)
        else:
            other = aliased(TariffFieldDocumentation)
        statement = (
            select(TariffFieldDocumentation, other)
            .join(other, other.id == second)
            .where(TABLE.c.id == first)
            .execution_options(populate_existing=True)
        )
        primary, sibling = (await session.execute(statement)).one()
        await session.delete(await session.get(TariffPlanType, plan_type.id))
        await session.flush()
        assert inspect(primary).deleted and inspect(sibling).deleted
        assert await session.get(TariffFieldDocumentation, first) is None
        assert await session.get(TariffFieldDocumentation, second) is None


async def test_documentation_cascade_reconciles_reflected_and_mapped_uuid_keys(
    migration_database, plan_type
):
    """Retain live rows when result aliases decode the same physical SQL type differently."""
    database = migration_database
    postgres = database.engine.dialect.name == "postgresql"
    schema = "public" if postgres else "main"
    another = TariffPlanType(name="Unrelated", field_types={})
    first, second = [
        TariffFieldDocumentation(
            tariff_plan_type_id=plan_type.id, field_name=name, language_code="en", comment=name
        )
        for name in ("first", "second")
    ]
    async with database.write() as session:
        session.add_all([another, first, second])
    async with database.write() as session:
        connection = await session.connection()
        reflected = await connection.run_sync(
            lambda sync: Table(TABLE.name, MetaData(), schema=schema, autoload_with=sync).alias()
        )
        other = aliased(TariffFieldDocumentation, reflected, adapt_on_names=True)
        reflected_id = UUID(second.id) if postgres else second.id.replace("-", "")
        statement = (
            select(TariffFieldDocumentation, other)
            .join(other, other.id == reflected_id)
            .where(TABLE.c.id == first.id)
            .options(raiseload("*"))
            .execution_options(populate_existing=True)
        )
        primary, sibling = (await session.execute(statement)).one()
        assert (primary.id, sibling.id) == (first.id, reflected_id)
        await session.delete(await session.get(TariffPlanType, another.id))
        await session.flush()
        assert inspect(primary).persistent and inspect(sibling).persistent
        assert await session.get(TariffPlanType, another.id) is None


@pytest.mark.parametrize(("reload", "legacy"), [(False, False), (True, False), (False, True)])
async def test_documentation_persistent_state_can_be_serialized(
    migration_database, plan_type, reload, legacy
):
    """Serialize a detached ORM row after insert or load and merge it into another session."""
    async with migration_database.write() as session:
        document = TariffFieldDocumentation(
            tariff_plan_type_id=plan_type.id, field_name="limit", language_code="en", comment="GiB"
        )
        session.add(document)
    if reload:
        async with migration_database.read() as session:
            document = await session.get(
                TariffFieldDocumentation, document.id, options=[raiseload("*")]
            )
    if legacy:
        state = inspect(document)
        key = "billing_documentation_schema_scope"
        state.info[key] = replace(state.info[key], identifier_type=None)
    restored = pickle.loads(pickle.dumps(document))
    assert inspect(restored).detached
    assert (restored.id, restored.comment) == (document.id, "GiB")
    async with migration_database.write() as session:
        merged = await session.merge(restored, load=False)
        if legacy:
            another = TariffPlanType(name="Unrelated", field_types={})
            session.add(another)
            await session.flush()
            await session.delete(another)
            await session.flush()
            assert inspect(merged).persistent
        await session.delete(await session.get(TariffPlanType, plan_type.id))
        await session.flush()
        assert inspect(merged).deleted


@pytest.mark.parametrize("routing", ["map", "search_path"])
async def test_inserted_documentation_keeps_its_schema_after_the_binding_changes(
    migration_database, plan_type, routing
):
    """Keep a freshly flushed document in its own identity map scope across a cascade."""
    engine = migration_database.engine
    if routing == "search_path" and engine.dialect.name != "postgresql":
        pytest.skip("PostgreSQL can change unqualified lookup through search_path")
    async with engine.begin() as connection:
        _, translated = await create_documentation_in_two_schemas(connection, plan_type)
        if routing == "map":
            await connection.execution_options(schema_translate_map=translated)
        else:
            await connection.exec_driver_sql("SET LOCAL search_path TO alternate_tariffs, public")
        kind = str(uuid4())
        await connection.execute(
            insert(TariffPlanType).values(id=kind, name="Independent", field_types={"limit": "int"})
        )
        document = TariffFieldDocumentation(
            tariff_plan_type_id=kind,
            field_name="limit",
            language_code="en",
            comment="Stored in alternate",
        )
        async with AsyncSession(bind=connection) as session:
            session.add(document)
            await session.flush()
            if routing == "map":
                await connection.execution_options(schema_translate_map={})
            else:
                await connection.exec_driver_sql("SET LOCAL search_path TO public")
            await session.delete(await session.get(TariffPlanType, plan_type.id))
            await session.flush()
            assert (
                await connection.scalar(
                    select(TABLE.c.comment).where(TABLE.c.id == document.id),
                    execution_options={"schema_translate_map": translated},
                )
                == "Stored in alternate"
            )
            assert inspect(document).persistent


async def test_field_documentation_supports_drafting_and_uuid_inputs(migration_database, plan_type):
    """Keep a draft key before its definition and support explicit normalized UUIDs."""
    identifier = uuid4()
    row = TariffFieldDocumentation(
        id=str(identifier).upper(),
        tariff_plan_type_id=UUID(plan_type.id),
        field_name="future_key",
        language_code="zh-Hant-TW",
        comment="繁體中文",
    )
    async with migration_database.write() as session:
        session.add(row)
    assert row.id == str(identifier)
    assert row.tariff_plan_type_id == plan_type.id

    async with migration_database.write() as session:
        kind = await session.get(TariffPlanType, plan_type.id)
        kind.field_types["future_key"] = "bool"
    async with migration_database.read() as session:
        row = await session.get(TariffFieldDocumentation, identifier)
        assert row.plan_type.field_types["future_key"] == "bool"
        assert row.language_code == "zh-Hant-TW"


@pytest.mark.parametrize(
    "overrides",
    [
        {"tariff_plan_type_id": None},
        {"field_name": None},
        {"language_code": None},
        {"comment": None},
        {"field_name": ""},
        {"field_name": " \t\n\u2003"},
        {"language_code": ""},
        {"language_code": "\t\u00a0\u3000"},
    ],
)
async def test_documentation_required_columns_and_nonblank_keys(
    migration_database, plan_type, overrides
):
    """Enforce required fields even when bypassing the ORM with Core."""
    values = {
        "tariff_plan_type_id": plan_type.id,
        "field_name": "limit",
        "language_code": "en",
        "comment": "Storage limit",
    }
    with pytest.raises(IntegrityError):
        async with migration_database.engine.begin() as connection:
            await connection.execute(insert(TABLE).values(values | overrides))


async def test_language_code_has_no_implicit_default(migration_database, plan_type):
    """Require callers to supply a language code explicitly."""
    with pytest.raises(IntegrityError):
        async with migration_database.write() as session:
            session.add(
                TariffFieldDocumentation(
                    tariff_plan_type_id=plan_type.id,
                    field_name="limit",
                    comment="Storage limit",
                )
            )


@pytest.mark.parametrize("column", ["field_name", "language_code", "comment"])
@pytest.mark.parametrize("value", [123, "before\0after"])
async def test_documentation_stores_only_valid_text(migration_database, plan_type, column, value):
    """Reject coercion of Python values and text containing an embedded NUL."""
    values = {"field_name": "limit", "language_code": "en", "comment": "Storage limit"}
    with pytest.raises(StatementError, match="must be text|storage|byte sequence"):
        async with migration_database.write() as session:
            session.add(TariffFieldDocumentation(plan_type=plan_type, **(values | {column: value})))


async def test_documentation_primary_and_foreign_keys(migration_database, plan_type):
    """Keep UUIDs unique and reject a document pointing to an unknown tariff type."""
    identifier = str(uuid4())
    values = {
        "id": identifier,
        "tariff_plan_type_id": plan_type.id,
        "field_name": "limit",
        "language_code": "en",
        "comment": "Storage limit",
    }
    async with migration_database.engine.begin() as connection:
        await connection.execute(insert(TABLE).values(values))
    for patch in (
        {"language_code": "ru"},
        {"id": str(uuid4()), "tariff_plan_type_id": str(uuid4())},
    ):
        with pytest.raises(IntegrityError):
            async with migration_database.engine.begin() as connection:
                await connection.execute(insert(TABLE).values(values | patch))
    async with migration_database.read() as session:
        assert (await session.scalars(select(TABLE.c.id))).all() == [identifier]


async def test_sqlite_rejects_invalid_physical_document_uuid(migration_database, plan_type):
    """Enforce UUID storage on SQLite as PostgreSQL does with its native type."""
    if migration_database.engine.dialect.name != "sqlite":
        pytest.skip("PostgreSQL uses a native UUID column")
    with pytest.raises(IntegrityError, match="id_canonical_uuid"):
        async with migration_database.engine.begin() as connection:
            await connection.exec_driver_sql(
                "INSERT INTO tariff_field_documentation "
                "(id, tariff_plan_type_id, field_name, language_code, comment) "
                "VALUES (?, ?, 'limit', 'en', 'Storage limit')",
                (str(uuid4()).upper(), UUID(plan_type.id).hex),
            )


async def test_sqlite_rejects_binary_comment_in_raw_sql(migration_database, plan_type):
    """Prevent direct SQLite statements from storing binary data as a comment."""
    if migration_database.engine.dialect.name != "sqlite":
        pytest.skip("PostgreSQL uses native text storage")
    with pytest.raises(IntegrityError, match="comment_storage"):
        async with migration_database.engine.begin() as connection:
            await connection.exec_driver_sql(
                "INSERT INTO tariff_field_documentation "
                "(id, tariff_plan_type_id, field_name, language_code, comment) "
                "VALUES (?, ?, 'limit', 'en', X'00FF')",
                (uuid4().hex, UUID(plan_type.id).hex),
            )


async def test_deleting_tariff_type_cascades_only_its_documentation(migration_database, plan_type):
    """Delete documents of the removed type and keep documents of other types."""
    other = TariffPlanType(name="Cloud", field_types={"limit": "int"})
    rows = [
        TariffFieldDocumentation(
            plan_type=kind, field_name="limit", language_code=lang, comment="x"
        )
        for kind, lang in ((plan_type, "en"), (plan_type, "ru"), (other, "en"))
    ]
    async with migration_database.write() as session:
        session.add_all(rows)
    async with migration_database.engine.begin() as connection:
        await connection.execute(delete(TariffPlanType).where(TariffPlanType.id == plan_type.id))
    async with migration_database.read() as session:
        remaining = (await session.scalars(select(TariffFieldDocumentation))).one()
        assert remaining.id == rows[2].id
        assert remaining.plan_type.id == other.id


async def test_a_tariff_plan_still_prevents_deleting_its_type(migration_database, plan_type):
    """If an existing tariff rejects type deletion, its documentation stays in place."""
    plan = TariffPlan(name="Storage", plan_type=plan_type, field_values={"limit": 3})
    doc = TariffFieldDocumentation(
        plan_type=plan_type, field_name="limit", language_code="en", comment="GiB"
    )
    async with migration_database.write() as session:
        session.add_all([plan, doc])
    with pytest.raises(IntegrityError):
        async with migration_database.write() as session:
            await session.delete(await session.get(TariffPlanType, plan_type.id))
    async with migration_database.read() as session:
        assert (await session.get(TariffFieldDocumentation, doc.id)).plan_type.id == plan_type.id
        assert (await session.get(TariffPlan, plan.id)).field_values == {"limit": 3}


@pytest.mark.parametrize("defer_type", [False, True])
async def test_loaded_documentation_follows_type_deletion_and_savepoint_rollback(
    migration_database, plan_type, defer_type
):
    """Evict a cascaded document immediately and restore the same object on rollback."""
    document = TariffFieldDocumentation(
        plan_type=plan_type, field_name="limit", language_code="en", comment="GiB"
    )
    async with migration_database.write() as session:
        session.add(document)

    async with migration_database.write() as session:
        query = select(TariffFieldDocumentation).where(TABLE.c.id == document.id)
        if defer_type:
            query = query.options(load_only(TariffFieldDocumentation.id), raiseload("*"))
        loaded = (await session.scalars(query)).one()
        alias = await session.get(TariffFieldDocumentation, document.id, identity_token="alias")
        assert alias is not loaded
        if defer_type:
            assert "tariff_plan_type_id" in inspect(loaded).unloaded
        async with session.begin_nested() as savepoint:
            await session.delete(await session.get(TariffPlanType, plan_type.id))
            await session.flush()
            assert inspect(loaded).deleted
            assert inspect(alias).deleted
            assert await session.get(TariffFieldDocumentation, document.id) is None
            assert (
                await session.get(TariffFieldDocumentation, document.id, identity_token="alias")
                is None
            )

            replacement = TariffFieldDocumentation(
                id=document.id,
                plan_type=TariffPlanType(id=plan_type.id, name="Replacement", field_types={}),
                field_name="new",
                language_code="en",
                comment="Replacement",
            )
            session.add(replacement)
            await session.flush()
            assert await session.get(TariffFieldDocumentation, document.id) is replacement
            await savepoint.rollback()

        restored = await session.get(TariffFieldDocumentation, document.id)
        assert restored is loaded
        assert restored.comment == "GiB"
        assert (
            await session.get(TariffFieldDocumentation, document.id, identity_token="alias")
            is alias
        )
        assert alias.comment == "GiB"
        assert inspect(replacement).transient


async def test_documentation_moved_in_the_same_flush_survives_type_deletion(
    migration_database, plan_type
):
    """Keep documentation whose foreign key moved before the old type was deleted."""
    other = TariffPlanType(name="Replacement", field_types={})
    document = TariffFieldDocumentation(
        plan_type=plan_type, field_name="limit", language_code="en", comment="GiB"
    )
    async with migration_database.write() as session:
        session.add_all([other, document])
    async with migration_database.write() as session:
        loaded = await session.get(TariffFieldDocumentation, document.id)
        previous = loaded.plan_type
        loaded.plan_type = await session.get(TariffPlanType, other.id)
        await session.delete(previous)
        await session.flush()
        assert inspect(loaded).persistent
        assert loaded.tariff_plan_type_id == other.id


async def test_postgres_documentation_and_publication_can_overlap_a_type_delete(
    migration_database, plan_type
):
    """Order coordination before the type foreign key even in a plain mapped session."""
    if migration_database.engine.dialect.name != "postgresql":
        pytest.skip("SQLite serializes writers with a database-wide lock")
    document = TariffFieldDocumentation(
        plan_type=plan_type, field_name="limit", language_code="en", comment="GiB"
    )
    tariff = TariffPlan(name="Storage", plan_type=plan_type, field_values={"limit": 2})
    starting = asyncio.get_running_loop().create_future()

    async def delete_type():
        """Attempt the conflicting mapped delete from a separate transaction."""
        try:
            async with migration_database.sessions.begin() as session:
                starting.set_result(await session.scalar(text("SELECT pg_backend_pid()")))
                await session.delete(await session.get(TariffPlanType, plan_type.id))
        except DBAPIError as error:
            return error.orig.sqlstate
        return None

    deleting = None
    try:
        async with migration_database.sessions.begin() as editor:
            holder = await editor.scalar(text("SELECT pg_backend_pid()"))
            editor.add(document)
            await editor.flush()
            deleting = asyncio.create_task(delete_type())
            waiting = await asyncio.wait_for(starting, 10)
            await wait_for_postgres_blocker(migration_database, holder, waiting)
            editor.add(tariff)
            await asyncio.wait_for(editor.flush(), 10)
    finally:
        if deleting is not None:
            result = await asyncio.wait_for(deleting, 10)
    assert result == "23503"
    async with migration_database.read() as session:
        assert (await session.get(TariffFieldDocumentation, document.id)).comment == "GiB"
        assert (await session.get(TariffPlan, tariff.id)).field_values == {"limit": 2}


async def test_postgres_documentation_reload_fails_fast_and_can_retry(
    migration_database, plan_type
):
    """Roll back a conflicting documentation reset even when it next writes the catalogue."""
    if migration_database.engine.dialect.name != "postgresql":
        pytest.skip("PostgreSQL takes separate table locks for TRUNCATE")
    document = TariffFieldDocumentation(
        plan_type=plan_type, field_name="limit", language_code="en", comment="GiB"
    )
    async with migration_database.write() as session:
        session.add(document)
    replacement = TariffPlanType(name="New type", field_types={})
    replacement_doc = TariffFieldDocumentation(
        plan_type=replacement,
        field_name="plan_size",
        language_code="en",
        comment="Bytes",
    )

    async def reload():
        """Rebuild the documentation in an independent transaction."""
        async with migration_database.sessions.begin() as session:
            await session.execute(text("SET LOCAL statement_timeout = '2s'"))
            await session.execute(text("TRUNCATE tariff_field_documentation"))
            session.add(replacement_doc)
            await session.flush()

    async with migration_database.write():
        with pytest.raises(DBAPIError) as rejected:
            await reload()
        assert rejected.value.orig.sqlstate == "55P03"
    async with migration_database.read() as session:
        assert (await session.get(TariffFieldDocumentation, document.id)).comment == "GiB"
    await reload()
    async with migration_database.read() as session:
        assert await session.get(TariffFieldDocumentation, document.id) is None
        assert (
            await session.get(TariffFieldDocumentation, replacement_doc.id)
        ).plan_type.name == "New type"


async def test_documentation_metadata_can_create_just_the_documentation_graph(
    migration_database,
):
    """Permit a standalone type and documentation schema when the remaining billing tables are absent."""
    if migration_database.engine.dialect.name != "postgresql":
        pytest.skip("The optional PostgreSQL write guard belongs to the full billing schema")
    tables = [TariffPlanType.__table__, TABLE]
    async with migration_database.engine.begin() as connection:
        await connection.exec_driver_sql("CREATE SCHEMA docs_only")
        await connection.exec_driver_sql("SET LOCAL search_path TO docs_only")
        await connection.run_sync(lambda sync: Base.metadata.create_all(sync, tables=tables))
        result = await connection.execute(
            insert(TariffPlanType.__table__)
            .values(name="Storage", field_types={})
            .returning(TariffPlanType.id)
        )
        await connection.execute(
            insert(TABLE).values(
                tariff_plan_type_id=result.scalar_one(),
                field_name="limit",
                language_code="en",
                comment="GiB",
            )
        )
        assert await connection.scalar(select(TABLE.c.comment)) == "GiB"
        await connection.run_sync(lambda sync: Base.metadata.drop_all(sync, tables=tables))


@pytest.mark.parametrize("shadow_type", [False, True])
async def test_documentation_metadata_guards_follow_the_shared_function(
    migration_database, shadow_type
):
    """Keep the table's triggers active only while its catalogue guard is installed."""
    if migration_database.engine.dialect.name != "postgresql":
        pytest.skip("PostgreSQL uses the shared write guard function")
    tables = [PaymentAccountTariffPlan.__table__]
    trigger_count = text("""
        SELECT count(*) FROM pg_catalog.pg_trigger
        WHERE tgrelid = 'public.tariff_field_documentation'::regclass AND NOT tgisinternal
    """)
    async with migration_database.engine.begin() as connection:
        if shadow_type:
            await connection.exec_driver_sql("CREATE SCHEMA shadow_types")
            await connection.exec_driver_sql(
                "CREATE TABLE shadow_types.tariff_plan_types (id uuid)"
            )
            await connection.exec_driver_sql("SET LOCAL search_path TO shadow_types, public")
        assert await connection.scalar(trigger_count) == 2
        assert await documentation_guard_is_active(connection)
        await connection.run_sync(lambda sync: Base.metadata.drop_all(sync, tables=tables))
        assert await connection.scalar(trigger_count) == 2
        assert not await documentation_guard_is_active(connection)
        if shadow_type:
            await connection.exec_driver_sql("SET LOCAL search_path TO public")
        await connection.run_sync(lambda sync: Base.metadata.create_all(sync, tables=tables))
        assert await connection.scalar(trigger_count) == 2
        assert await documentation_guard_is_active(connection)


async def test_dropping_shadow_documentation_leaves_shared_guards_in_place(
    migration_database,
):
    """Dropping only a shadow table must leave triggers on the real documentation table."""
    if migration_database.engine.dialect.name != "postgresql":
        pytest.skip("PostgreSQL can resolve tables from several schemas")
    async with migration_database.engine.begin() as connection:
        await connection.exec_driver_sql("CREATE SCHEMA shadow_docs")
        await connection.exec_driver_sql(
            "CREATE TABLE shadow_docs.tariff_field_documentation (id uuid)"
        )
        await connection.exec_driver_sql("SET LOCAL search_path TO shadow_docs, public")
        await connection.run_sync(lambda sync: Base.metadata.drop_all(sync, tables=[TABLE]))
        assert (
            await connection.scalar(
                text("SELECT to_regclass('shadow_docs.tariff_field_documentation')")
            )
            is None
        )
        assert (
            await connection.scalar(
                text("""
            SELECT count(*) FROM pg_catalog.pg_trigger
            WHERE tgrelid = 'public.tariff_field_documentation'::regclass AND NOT tgisinternal
        """)
            )
            == 2
        )


WORKSPACE_DOCUMENTATION_GUARDS = text("""
    SELECT count(*) FROM pg_catalog.pg_trigger
    WHERE tgrelid = 'docs_workspace.tariff_field_documentation'::regclass AND NOT tgisinternal
""")


async def documentation_guard_is_active(
    connection, table="tariff_field_documentation", coordination="coordination"
):
    """Check whether a documentation statement versions the shared coordination row."""
    coordination_version = text(f"SELECT ctid::text FROM {coordination} WHERE id = 1")
    previous = await connection.scalar(coordination_version)
    await connection.exec_driver_sql(f"UPDATE {table} SET comment = comment WHERE FALSE")
    return await connection.scalar(coordination_version) != previous


@pytest.fixture
async def documentation_workspace(migration_database):
    """Build a documentation table in a second schema linked to the main catalogue."""
    if migration_database.engine.dialect.name != "postgresql":
        pytest.skip("PostgreSQL allows foreign keys across schemas")
    async with migration_database.engine.begin() as connection:
        await connection.exec_driver_sql("CREATE SCHEMA docs_workspace")
        await connection.exec_driver_sql("SET LOCAL search_path TO docs_workspace, public")
        await connection.run_sync(
            lambda sync: Base.metadata.create_all(sync, tables=[TABLE], checkfirst=False)
        )
    return migration_database


async def test_postgres_cascade_reconciles_a_document_after_its_schema_is_renamed(
    documentation_workspace, plan_type
):
    """Follow a cascaded document's relation when PostgreSQL renames its schema during flush."""
    engine = documentation_workspace.engine
    table_name = f"docs_workspace.{TABLE.name}"
    translated = {"schema_translate_map": {None: "docs_workspace"}}
    async with engine.begin() as connection:
        await connection.execute(
            insert(TABLE).values(
                tariff_plan_type_id=plan_type.id,
                field_name="limit",
                language_code="en",
                comment="Rename during cascade",
            ),
            execution_options=translated,
        )
    async with AsyncSession(bind=engine, expire_on_commit=False) as session:
        document = await session.scalar(
            select(TariffFieldDocumentation).options(raiseload("*")), execution_options=translated
        )
        await session.commit()
        renamed = False

        async def rename(_driver):
            """Rename the schema after PostgreSQL deletes the document through the foreign key."""
            async with engine.begin() as ddl:
                await ddl.exec_driver_sql("SET LOCAL lock_timeout = '1s'")
                await ddl.exec_driver_sql("ALTER SCHEMA docs_workspace RENAME TO moved_workspace")

        def before_read(connection, _cursor, statement, *_):
            """Interleave a second transaction between the location query and the first read."""
            nonlocal renamed
            if not renamed and f"FROM {table_name}" in statement:
                renamed = True
                connection.connection.dbapi_connection.run_async(rename)

        event.listen(engine.sync_engine, "before_cursor_execute", before_read)
        try:
            await session.delete(await session.get(TariffPlanType, plan_type.id))
            await session.flush()
        finally:
            event.remove(engine.sync_engine, "before_cursor_execute", before_read)
        options = {"schema_translate_map": {None: "moved_workspace"}}
        assert (
            renamed and await session.scalar(select(TABLE.c.id), execution_options=options) is None
        )
        assert inspect(document).deleted


async def test_cross_schema_documentation_follows_the_guard_of_its_foreign_key(
    documentation_workspace,
):
    """Keep a document's guard linked to its parent while billing metadata is rebuilt."""
    tables = [TABLE, PaymentAccountTariffPlan.__table__]
    async with documentation_workspace.engine.begin() as connection:
        assert await connection.scalar(
            text("""
            SELECT confrelid = 'public.tariff_plan_types'::regclass FROM pg_catalog.pg_constraint
            WHERE conrelid = 'docs_workspace.tariff_field_documentation'::regclass AND contype = 'f'
        """)
        )
        assert await connection.scalar(WORKSPACE_DOCUMENTATION_GUARDS) == 2
        assert await documentation_guard_is_active(
            connection, "docs_workspace.tariff_field_documentation"
        )
        await connection.run_sync(lambda sync: Base.metadata.drop_all(sync, tables=tables))
        assert await connection.scalar(WORKSPACE_DOCUMENTATION_GUARDS) == 2
        assert not await documentation_guard_is_active(
            connection, "docs_workspace.tariff_field_documentation"
        )
        await connection.run_sync(lambda sync: Base.metadata.create_all(sync, tables=tables))
        assert await connection.scalar(WORKSPACE_DOCUMENTATION_GUARDS) == 2
        assert await documentation_guard_is_active(
            connection, "docs_workspace.tariff_field_documentation"
        )


async def test_cross_schema_documentation_survives_reverting_and_reinstalling_the_guard(
    documentation_workspace, alembic_config, plan_type
):
    """Preserve an extension table while removing and restoring the shared function via Alembic."""
    database = documentation_workspace
    comment = text("SELECT comment FROM docs_workspace.tariff_field_documentation")
    async with database.engine.begin() as connection:
        await connection.exec_driver_sql("SET LOCAL search_path TO docs_workspace, public")
        await connection.execute(
            insert(TABLE).values(
                tariff_plan_type_id=plan_type.id,
                field_name="limit",
                language_code="en",
                comment="GiB",
            )
        )
        assert await connection.scalar(WORKSPACE_DOCUMENTATION_GUARDS) == 2
    for operation, revision, active in (
        (alembic.downgrade, "0021", False),
        (alembic.upgrade, "0022", True),
        (alembic.upgrade, "0023", True),
    ):
        await database.close()
        await anyio.to_thread.run_sync(operation, alembic_config, revision)
        async with database.engine.connect() as connection:
            assert (
                await connection.scalar(text("SELECT version_num FROM alembic_version")) == revision
            )
            assert await connection.scalar(WORKSPACE_DOCUMENTATION_GUARDS) == 2
            assert (
                await documentation_guard_is_active(
                    connection, "docs_workspace.tariff_field_documentation"
                )
            ) is active
            assert await connection.scalar(comment) == "GiB"


@pytest.mark.parametrize("change", ["activation", "rename"])
async def test_cross_schema_documentation_retries_a_snapshot_older_than_the_guard(
    documentation_workspace, alembic_config, plan_type, change
):
    """Prevent an older snapshot from bypassing an active billing guard."""
    database = documentation_workspace
    if change == "activation":
        await database.close()
        await anyio.to_thread.run_sync(alembic.downgrade, alembic_config, "0021")
    async with database.engine.begin() as connection:
        await connection.exec_driver_sql("SET LOCAL search_path TO docs_workspace, public")
        await connection.execute(
            insert(TABLE).values(
                tariff_plan_type_id=plan_type.id,
                field_name="limit",
                language_code="en",
                comment="GiB",
            )
        )
    document = text("SELECT comment FROM docs_workspace.tariff_field_documentation")
    update_doc = text("UPDATE docs_workspace.tariff_field_documentation SET comment = 'Bytes'")
    async with database.engine.connect() as stale:
        await stale.execution_options(isolation_level="REPEATABLE READ")
        assert await stale.scalar(document) == "GiB"
        if change == "activation":
            await anyio.to_thread.run_sync(alembic.upgrade, alembic_config, "0022")
        else:
            async with database.engine.begin() as ddl:
                await ddl.exec_driver_sql(
                    "ALTER FUNCTION public.serialize_billing_writes() RENAME TO revised_billing_guard"
                )
        with pytest.raises(DBAPIError) as snapshot:
            await stale.execute(update_doc)
        assert snapshot.value.orig.sqlstate == "40001"
    async with database.engine.begin() as fresh:
        assert await fresh.scalar(document) == "GiB"
        assert await documentation_guard_is_active(
            fresh, "docs_workspace.tariff_field_documentation"
        )
        await fresh.execute(update_doc)
        assert await fresh.scalar(document) == "Bytes"


@pytest.mark.parametrize("upgrade", [False, True])
async def test_billing_migration_does_not_wait_for_cross_schema_documentation(
    documentation_workspace, alembic_config, upgrade
):
    """Upgrade and downgrade the catalogue while a second schema still holds a doc table lock."""
    database = documentation_workspace
    operation, target, active = alembic.downgrade, "0021", False
    if upgrade:
        await database.close()
        await anyio.to_thread.run_sync(alembic.downgrade, alembic_config, "0021")
        operation, target, active = alembic.upgrade, "0022", True

    def bound_wait(connection, _cursor, statement, *_):
        """Avoid hanging if a migration tries to block on the doc table's lock."""
        if connection.engine.url.database == database.engine.url.database and statement.startswith(
            "SELECT alembic_version.version_num"
        ):
            connection.exec_driver_sql("SET LOCAL statement_timeout = '2s'")

    async with database.engine.begin() as reading:
        await reading.exec_driver_sql("SELECT id FROM docs_workspace.tariff_field_documentation")
        event.listen(Engine, "before_cursor_execute", bound_wait)
        try:
            await anyio.to_thread.run_sync(operation, alembic_config, target)
        finally:
            event.remove(Engine, "before_cursor_execute", bound_wait)
    async with database.engine.begin() as connection:
        assert await connection.scalar(text("SELECT version_num FROM alembic_version")) == target
        assert await connection.scalar(WORKSPACE_DOCUMENTATION_GUARDS) == 2
        assert (
            await documentation_guard_is_active(
                connection, "docs_workspace.tariff_field_documentation"
            )
        ) is active


async def test_documentation_drop_does_not_deadlock_during_a_catalogue_write(
    documentation_workspace, plan_type
):
    """Return a lock conflict while the catalogue writer retains access to the documentation table."""
    database = documentation_workspace
    async with database.write() as writer:
        kind = await writer.get(TariffPlanType, plan_type.id)
        kind.name = "Edited"
        await writer.flush()
        with pytest.raises(DBAPIError) as conflict:
            async with database.engine.begin() as dropping:
                await dropping.exec_driver_sql("SET LOCAL statement_timeout = '2s'")
                await dropping.exec_driver_sql("SET LOCAL search_path TO docs_workspace, public")
                await dropping.run_sync(lambda sync: Base.metadata.drop_all(sync, tables=[TABLE]))
        assert conflict.value.orig.sqlstate == "55P03"
        await writer.execute(text("SET LOCAL search_path TO docs_workspace, public"))
        await writer.execute(
            insert(TABLE).values(
                tariff_plan_type_id=plan_type.id,
                field_name="limit",
                language_code="en",
                comment="GiB",
            )
        )
    async with database.engine.begin() as dropping:
        await dropping.exec_driver_sql("SET LOCAL lock_timeout = '2s'")
        await dropping.exec_driver_sql("SET LOCAL search_path TO docs_workspace, public")
        await dropping.run_sync(lambda sync: Base.metadata.drop_all(sync, tables=[TABLE]))
        assert await dropping.scalar(text("SHOW lock_timeout")) == "2s"


async def test_canceling_a_documentation_drop_preserves_its_guards_in_autocommit(
    documentation_workspace, plan_type
):
    """Preserve the table and its triggers when a standalone PostgreSQL DROP fails."""
    database = documentation_workspace
    async with database.write() as writer:
        kind = await writer.get(TariffPlanType, plan_type.id)
        kind.name = "Edited"
        await writer.flush()
        async with database.engine.connect() as dropping:
            await dropping.execution_options(isolation_level="AUTOCOMMIT")
            await dropping.exec_driver_sql("SET statement_timeout = '2s'")
            await dropping.exec_driver_sql("SET search_path TO docs_workspace, public")
            try:
                with pytest.raises(DBAPIError) as conflict:
                    await dropping.run_sync(
                        lambda sync: Base.metadata.drop_all(sync, tables=[TABLE])
                    )
                assert await dropping.scalar(WORKSPACE_DOCUMENTATION_GUARDS) == 2
                assert conflict.value.orig.sqlstate == "55P03"
            finally:
                await dropping.exec_driver_sql("RESET search_path")
                await dropping.exec_driver_sql("RESET statement_timeout")
    async with database.engine.begin() as connection:
        assert await documentation_guard_is_active(
            connection, "docs_workspace.tariff_field_documentation"
        )


@pytest.mark.parametrize("reverse", [False, True])
async def test_catalogue_and_documentation_autocommit_drop_keeps_guards_when_a_reader_blocks_it(
    migration_database, reverse
):
    """Preserve both tables and their active guards when documentation prevents the first DROP."""
    database = migration_database
    if database.engine.dialect.name != "postgresql":
        pytest.skip("PostgreSQL protects the documentation table during a read")
    assignments = PaymentAccountTariffPlan.__table__
    tables = [TABLE, assignments] if reverse else [assignments, TABLE]
    async with database.engine.begin() as reader, database.engine.connect() as dropping:
        await reader.execute(select(TABLE.c.id))
        await dropping.execution_options(isolation_level="AUTOCOMMIT")
        await dropping.exec_driver_sql("SET statement_timeout = '2s'")
        try:
            with pytest.raises(DBAPIError) as conflict:
                await dropping.run_sync(lambda sync: Base.metadata.drop_all(sync, tables=tables))
            assert conflict.value.orig.sqlstate == "55P03"
        finally:
            await dropping.exec_driver_sql("RESET statement_timeout")
    async with database.engine.begin() as connection:
        assert await connection.run_sync(lambda sync: inspect(sync).has_table(assignments.name))
        assert await documentation_guard_is_active(connection)


async def test_catalogue_autocommit_drop_rolls_back_its_guards_if_assignments_are_referenced(
    migration_database,
):
    """Keep the catalogue active when PostgreSQL refuses to drop the assignment table."""
    database = migration_database
    if database.engine.dialect.name != "postgresql":
        pytest.skip("PostgreSQL enforces dependencies during a table drop")
    assignments = PaymentAccountTariffPlan.__table__
    async with database.engine.begin() as connection:
        await connection.exec_driver_sql("""
            CREATE TABLE assignment_references (
                account_id uuid, type_id uuid,
                FOREIGN KEY (account_id, type_id) REFERENCES payment_account_tariff_plans
            )
        """)
    async with database.engine.connect() as dropping:
        await dropping.execution_options(isolation_level="AUTOCOMMIT")
        with pytest.raises(DBAPIError) as conflict:
            await dropping.run_sync(lambda sync: Base.metadata.drop_all(sync, tables=[assignments]))
        assert conflict.value.orig.sqlstate == "2BP01"
        assert await documentation_guard_is_active(dropping)


async def test_direct_catalogue_drop_fails_fast_during_an_assignment_write(
    migration_database,
):
    """Refuse to take trigger DDL locks while an assignment writer still needs to read plans."""
    database = migration_database
    if database.engine.dialect.name != "postgresql":
        pytest.skip("PostgreSQL explicitly locks all relations touched by trigger DDL")
    assignments = PaymentAccountTariffPlan.__table__
    async with database.write() as writer:
        await writer.execute(
            assignments.update().where(False).values(tariff_plan_id=assignments.c.tariff_plan_id)
        )
        with pytest.raises(DBAPIError) as conflict:
            async with database.engine.begin() as dropping:
                await dropping.exec_driver_sql("SET LOCAL statement_timeout = '1s'")
                await dropping.run_sync(assignments.drop)
        assert conflict.value.orig.sqlstate == "55P03"
        assert (await writer.scalars(select(TariffPlan))).all() == []
    async with database.engine.begin() as connection:
        await connection.run_sync(assignments.drop)
        assert not await documentation_guard_is_active(connection)


@pytest.mark.parametrize("same_schema", [False, True])
async def test_catalogue_drop_keeps_its_original_target_when_a_shadow_table_disappears(
    migration_database,
    same_schema,
):
    """Do not switch to a different physical table after waiting for a concurrent DROP."""
    database = migration_database
    if database.engine.dialect.name != "postgresql":
        pytest.skip("PostgreSQL can resolve one table name through several schemas")
    assignments = PaymentAccountTariffPlan.__table__
    schema = "public" if same_schema else "staged_assignments"
    shadow = f"{schema}.{assignments.name}"
    async with database.engine.begin() as setup:
        if same_schema:
            await setup.run_sync(assignments.drop)
        else:
            await setup.execute(CreateSchema(schema))
        await setup.exec_driver_sql(f"CREATE TABLE {shadow} (id uuid)")

    starting = asyncio.get_running_loop().create_future()

    async def drop_from_search_path():
        """Use the shadow schema first while another connection holds its table lock."""
        try:
            async with database.engine.begin() as connection:
                await connection.exec_driver_sql(f"SET LOCAL search_path TO {schema}, public")
                await connection.exec_driver_sql("SET LOCAL statement_timeout = '10s'")
                starting.set_result(await connection.scalar(text("SELECT pg_backend_pid()")))
                await connection.run_sync(assignments.drop)
        except DBAPIError as error:
            return error.orig.sqlstate
        return None

    dropping = None
    try:
        async with database.engine.begin() as removing:
            holder = await removing.scalar(text("SELECT pg_backend_pid()"))
            await removing.exec_driver_sql(f"DROP TABLE {shadow}")
            if same_schema:
                await removing.run_sync(
                    lambda sync: Base.metadata.create_all(sync, tables=[assignments])
                )
            dropping = asyncio.create_task(drop_from_search_path())
            waiting = await asyncio.wait_for(starting, 10)
            await wait_for_postgres_blocker(database, holder, waiting)
    finally:
        if dropping is not None:
            result = await asyncio.wait_for(dropping, 10)
    assert result == ("55P03" if same_schema else "42P01")
    async with database.engine.begin() as connection:
        assert await connection.run_sync(lambda sync: inspect(sync).has_table(assignments.name))
        assert await documentation_guard_is_active(connection)


async def test_schema_owner_can_drop_an_unguarded_assignment_namesake(
    migration_database,
):
    """Preserve the native PostgreSQL DROP privilege of an owner of the selected schema."""
    database = migration_database
    if database.engine.dialect.name != "postgresql":
        pytest.skip("PostgreSQL grants DROP rights to schema owners")
    assignments = PaymentAccountTariffPlan.__table__
    schema_owner = "schema_owner_" + uuid4().hex
    table_owner = "table_owner_" + uuid4().hex
    async with database.engine.connect() as connection:
        transaction = await connection.begin()
        try:
            if not await connection.scalar(
                text("SELECT rolsuper OR rolcreaterole FROM pg_roles WHERE rolname = current_user")
            ):
                pytest.skip("The test administrator cannot create roles")
            await connection.exec_driver_sql(f'CREATE ROLE "{schema_owner}"')
            await connection.exec_driver_sql(f'CREATE ROLE "{table_owner}"')
            await connection.exec_driver_sql(
                f'CREATE SCHEMA assignment_stage AUTHORIZATION "{schema_owner}"'
            )
            await connection.exec_driver_sql(
                f'GRANT USAGE, CREATE ON SCHEMA assignment_stage TO "{table_owner}"'
            )
            await connection.exec_driver_sql(f'SET LOCAL ROLE "{table_owner}"')
            await connection.exec_driver_sql(
                f"CREATE TABLE assignment_stage.{assignments.name} (id uuid)"
            )
            await connection.exec_driver_sql("RESET ROLE")
            await connection.exec_driver_sql(f'SET LOCAL ROLE "{schema_owner}"')
            await connection.exec_driver_sql("SET LOCAL search_path TO assignment_stage, public")
            await connection.run_sync(assignments.drop)
            await connection.exec_driver_sql("RESET ROLE")
            assert (
                await connection.scalar(
                    text(f"SELECT to_regclass('assignment_stage.{assignments.name}')")
                )
                is None
            )
            assert await documentation_guard_is_active(connection)
        finally:
            await transaction.rollback()


async def test_catalogue_upgrade_retries_if_a_legacy_documentation_write_is_open(
    documentation_workspace, alembic_config, plan_type
):
    """Keep the lock policy steady for transactions that started writing before upgrade."""
    database = documentation_workspace
    await database.close()
    await anyio.to_thread.run_sync(alembic.downgrade, alembic_config, "0021")
    async with database.engine.begin() as seed:
        await seed.exec_driver_sql("SET LOCAL search_path TO docs_workspace, public")
        await seed.execute(
            insert(TABLE).values(
                tariff_plan_type_id=plan_type.id,
                field_name="limit",
                language_code="en",
                comment="GiB",
            )
        )
    async with database.engine.begin() as writer:
        await writer.exec_driver_sql("SET LOCAL search_path TO docs_workspace, public")
        await writer.execute(TABLE.update().values(comment="MiB"))
        with pytest.raises(DBAPIError) as conflict:
            await anyio.to_thread.run_sync(alembic.upgrade, alembic_config, "0022")
        assert conflict.value.orig.sqlstate == "55P03"
        await writer.execute(TABLE.update().values(comment="Bytes"))
    assert await documentation_revision(database) == ("0021", False)
    await database.close()
    await anyio.to_thread.run_sync(alembic.upgrade, alembic_config, "0022")
    async with database.engine.begin() as connection:
        assert await documentation_guard_is_active(
            connection, "docs_workspace.tariff_field_documentation"
        )
        assert (
            await connection.scalar(
                text("SELECT comment FROM docs_workspace.tariff_field_documentation")
            )
            == "Bytes"
        )


async def test_catalogue_metadata_drop_retries_if_a_documentation_write_is_open(
    documentation_workspace,
):
    """Keep the active catalogue guards installed while an existing document writer holds them."""
    database = documentation_workspace
    tables = [PaymentAccountTariffPlan.__table__]
    async with database.engine.begin() as writer:
        assert await documentation_guard_is_active(
            writer, "docs_workspace.tariff_field_documentation"
        )
        with pytest.raises(DBAPIError) as conflict:
            async with database.engine.begin() as dropping:
                await dropping.exec_driver_sql("SET LOCAL statement_timeout = '2s'")
                await dropping.run_sync(lambda sync: Base.metadata.drop_all(sync, tables=tables))
        assert conflict.value.orig.sqlstate == "55P03"
    async with database.engine.begin() as connection:
        await connection.run_sync(lambda sync: Base.metadata.drop_all(sync, tables=tables))
        assert not await documentation_guard_is_active(
            connection, "docs_workspace.tariff_field_documentation"
        )


@pytest.mark.parametrize("retry", [False, True])
async def test_catalogue_metadata_autocommit_can_recover_a_busy_installation(
    documentation_workspace, retry
):
    """Retry or remove a partially created catalogue after the transition rejects an older writer."""
    database = documentation_workspace
    assignments = PaymentAccountTariffPlan.__table__
    tables = [assignments]
    async with database.engine.begin() as connection:
        await connection.run_sync(lambda sync: Base.metadata.drop_all(sync, tables=tables))
    async with database.engine.connect() as creating:
        await creating.execution_options(isolation_level="AUTOCOMMIT")
        async with database.engine.begin() as writer:
            assert not await documentation_guard_is_active(
                writer, "docs_workspace.tariff_field_documentation"
            )
            with pytest.raises(DBAPIError) as conflict:
                await creating.run_sync(lambda sync: Base.metadata.create_all(sync, tables=tables))
            assert conflict.value.orig.sqlstate == "55P03"
        if retry:
            await creating.run_sync(lambda sync: Base.metadata.create_all(sync, tables=tables))
            async with database.engine.begin() as connection:
                assert await documentation_guard_is_active(
                    connection, "docs_workspace.tariff_field_documentation"
                )
        await creating.run_sync(lambda sync: Base.metadata.drop_all(sync, tables=tables))
        assert not await creating.run_sync(lambda sync: inspect(sync).has_table(assignments.name))


async def test_catalogue_metadata_autocommit_locks_during_activation(
    documentation_workspace,
):
    """Refuse activation when an older writer arrives after assignment table creation."""
    database = documentation_workspace
    tables = [PaymentAccountTariffPlan.__table__]
    async with database.engine.begin() as connection:
        await connection.run_sync(lambda sync: Base.metadata.drop_all(sync, tables=tables))
    async with database.engine.begin() as writer, database.engine.connect() as creating:
        await creating.execution_options(isolation_level="AUTOCOMMIT")

        async def start_legacy_write(_driver):
            """Keep an independent transaction open until after the guard installation attempts to run."""
            assert not await documentation_guard_is_active(
                writer, "docs_workspace.tariff_field_documentation"
            )

        def before_install(connection, _cursor, statement, *_):
            """Start the write immediately before the DDL that installs the catalogue function."""
            definition = "CREATE OR REPLACE FUNCTION %1$I.serialize_billing_writes()"
            if statement.startswith("DO $billing_ddl$") and definition in statement:
                connection.connection.run_async(start_legacy_write)

        event.listen(creating.sync_connection, "before_cursor_execute", before_install)
        try:
            with pytest.raises(DBAPIError) as conflict:
                await creating.run_sync(lambda sync: Base.metadata.create_all(sync, tables=tables))
        finally:
            event.remove(creating.sync_connection, "before_cursor_execute", before_install)
        assert conflict.value.orig.sqlstate == "55P03"
    async with database.engine.begin() as connection:
        await connection.run_sync(lambda sync: Base.metadata.create_all(sync, tables=tables))
        assert await documentation_guard_is_active(
            connection, "docs_workspace.tariff_field_documentation"
        )


async def test_direct_documentation_table_create_installs_its_guard(
    migration_database, alembic_config
):
    """Install the same PostgreSQL guards when the caller creates only the Table object."""
    if migration_database.engine.dialect.name != "postgresql":
        pytest.skip("PostgreSQL uses catalogue write guards")
    await migration_database.close()
    await anyio.to_thread.run_sync(alembic.downgrade, alembic_config, "0022")
    async with migration_database.engine.begin() as connection:
        await connection.run_sync(TABLE.create)
        assert await documentation_guard_is_active(connection)


async def test_migrated_documentation_follows_a_renamed_billing_guard(
    migration_database, alembic_config
):
    """Use the active function bound to the parent trigger after a routine rename."""
    database = migration_database
    if database.engine.dialect.name != "postgresql":
        pytest.skip("PostgreSQL allows a trigger function to change its name")
    await database.close()
    await anyio.to_thread.run_sync(alembic.downgrade, alembic_config, "0022")
    async with database.engine.begin() as connection:
        await connection.exec_driver_sql(
            'ALTER FUNCTION serialize_billing_writes() RENAME TO "renamed coordination guard"'
        )
    await database.close()
    await anyio.to_thread.run_sync(alembic.upgrade, alembic_config, "0023")
    async with database.engine.begin() as connection:
        assert await documentation_guard_is_active(connection)


async def test_documentation_uses_the_coordination_path_of_a_relocated_guard(
    migration_database, plan_type
):
    """Find the actual catalogue after PostgreSQL moves its function to a different schema."""
    database = migration_database
    if database.engine.dialect.name != "postgresql":
        pytest.skip("PostgreSQL can move functions between schemas")
    async with database.engine.begin() as connection:
        await connection.execute(CreateSchema("guard_routines"))
        await connection.exec_driver_sql("CREATE TABLE guard_routines.coordination (id integer)")
        await connection.exec_driver_sql(
            "ALTER FUNCTION public.serialize_billing_writes() SET SCHEMA guard_routines"
        )
        version = text("SELECT ctid::text FROM public.coordination WHERE id = 1")
        before = await connection.scalar(version)
        await connection.execute(
            TariffPlanType.__table__.update()
            .where(TariffPlanType.id == plan_type.id)
            .values(name="Changed")
        )
        assert await connection.scalar(version) != before
        assert await documentation_guard_is_active(connection, coordination="public.coordination")


@pytest.mark.parametrize("relocate", [False, True])
async def test_documentation_guard_follows_a_renamed_foreign_key_table(
    migration_database, plan_type, relocate
):
    """Keep the existing PostgreSQL FK and coordination usable after a physical table rename."""
    database = migration_database
    if database.engine.dialect.name != "postgresql":
        pytest.skip("PostgreSQL resolves each guard's catalogue from its foreign key")
    async with database.engine.begin() as connection:
        await connection.execute(
            insert(TABLE).values(
                tariff_plan_type_id=plan_type.id,
                field_name="limit",
                language_code="en",
                comment="GiB",
            )
        )
        if relocate:
            await connection.execute(CreateSchema("relocated_types"))
            await connection.exec_driver_sql(
                "ALTER TABLE tariff_plan_types SET SCHEMA relocated_types"
            )
        else:
            await connection.exec_driver_sql(
                "ALTER TABLE tariff_plan_types RENAME TO archived_tariff_types"
            )
        assert await documentation_guard_is_active(connection)
        await connection.execute(TABLE.update().values(comment="Bytes"))
        assert await connection.scalar(select(TABLE.c.comment)) == "Bytes"


async def test_nested_documentation_delete_still_acquires_coordination(
    migration_database, plan_type
):
    """Coordinate a service trigger's delete even if the transaction already holds a table lock."""
    database = migration_database
    if database.engine.dialect.name != "postgresql":
        pytest.skip("PostgreSQL uses coordination during nested triggers")
    async with database.engine.begin() as setup:
        identifier = await setup.scalar(
            insert(TABLE)
            .values(
                tariff_plan_type_id=plan_type.id,
                field_name="limit",
                language_code="en",
                comment="Remove",
            )
            .returning(TABLE.c.id)
        )
        await setup.exec_driver_sql("CREATE TABLE doc_removals (id uuid)")
        await setup.exec_driver_sql("""
            CREATE FUNCTION remove_requested_documentation() RETURNS trigger LANGUAGE plpgsql AS $body$
            BEGIN
                DELETE FROM tariff_field_documentation WHERE id = NEW.id;
                RETURN NULL;
            END;
            $body$
        """)
        await setup.exec_driver_sql("""
            CREATE TRIGGER remove_requested_documentation AFTER INSERT ON doc_removals
            FOR EACH ROW EXECUTE FUNCTION remove_requested_documentation()
        """)
    async with database.engine.begin() as connection:
        await connection.exec_driver_sql("LOCK TABLE coordination IN ROW EXCLUSIVE MODE")
        version = text("SELECT ctid::text FROM coordination WHERE id = 1")
        before = await connection.scalar(version)
        await connection.execute(
            text("INSERT INTO doc_removals VALUES (CAST(:id AS uuid))"),
            {"id": identifier},
        )
        assert await connection.scalar(version) != before
        assert await connection.scalar(select(TABLE.c.id)) is None


async def test_secondary_foreign_key_does_not_disable_documentation_coordination(
    migration_database, plan_type
):
    """Use the cascading tariff type reference when another registry checks the same column."""
    database = migration_database
    if database.engine.dialect.name != "postgresql":
        pytest.skip("PostgreSQL can add a second foreign key after table creation")
    async with database.engine.begin() as connection:
        await connection.execute(CreateSchema("reference_registry"))
        await connection.exec_driver_sql(
            "CREATE TABLE reference_registry.eligible_plan_types (id uuid PRIMARY KEY)"
        )
        await connection.execute(
            text("INSERT INTO reference_registry.eligible_plan_types VALUES (CAST(:id AS uuid))"),
            {"id": plan_type.id},
        )
        await connection.exec_driver_sql("""
            ALTER TABLE tariff_field_documentation ADD CONSTRAINT eligible_plan_type
            FOREIGN KEY (tariff_plan_type_id) REFERENCES reference_registry.eligible_plan_types (id)
        """)
        assert await documentation_guard_is_active(connection)


async def test_documentation_guard_rejects_a_snapshot_older_than_its_foreign_key(
    migration_database, alembic_config, plan_type
):
    """Prevent a repeatable-read transaction from writing through a foreign key it cannot see."""
    database = migration_database
    if database.engine.dialect.name != "postgresql":
        pytest.skip("PostgreSQL exposes new DDL to an older repeatable-read transaction")
    await database.close()
    await anyio.to_thread.run_sync(alembic.downgrade, alembic_config, "0022")
    values = {
        "tariff_plan_type_id": plan_type.id,
        "field_name": "limit",
        "language_code": "en",
        "comment": "GiB",
    }
    with pytest.raises(DBAPIError) as stale:
        async with database.engine.begin() as writer:
            await writer.exec_driver_sql("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ")
            assert (
                await writer.scalar(text("SELECT revision FROM coordination WHERE id = 1"))
                is not None
            )
            await anyio.to_thread.run_sync(alembic.upgrade, alembic_config, "0023")
            await writer.execute(insert(TABLE).values(**values))
    assert stale.value.orig.sqlstate == "40001"
    async with database.engine.begin() as connection:
        await connection.execute(insert(TABLE).values(**values))
        assert await connection.scalar(select(TABLE.c.comment)) == "GiB"


@pytest.mark.parametrize(
    ("isolation", "keep_key_name"),
    [("REPEATABLE READ", False), ("SERIALIZABLE", True)],
)
async def test_documentation_retries_when_an_existing_foreign_key_changes_under_an_old_snapshot(
    migration_database, isolation, keep_key_name
):
    """Reject an old writer when the database now enforces a different catalogue reference."""
    database = migration_database
    if database.engine.dialect.name != "postgresql":
        pytest.skip("PostgreSQL has independent catalogue and transaction snapshots")
    alternate_type = str(uuid4())
    alternate_schema = "replacement_catalogue"
    async with database.engine.begin() as connection:
        await connection.execute(CreateSchema(alternate_schema))
        await connection.execution_options(schema_translate_map={None: alternate_schema})
        await connection.run_sync(Base.metadata.create_all)
        await connection.execute(
            insert(TariffPlanType).values(id=alternate_type, name="Replacement", field_types={})
        )

    values = {
        "tariff_plan_type_id": alternate_type,
        "field_name": "limit",
        "language_code": "en",
        "comment": "GiB",
    }
    with pytest.raises(DBAPIError) as old_snapshot:
        async with database.engine.connect() as stale:
            await stale.execution_options(isolation_level=isolation)
            assert (
                await stale.scalar(text("SELECT revision FROM public.coordination WHERE id = 1"))
                is not None
            )
            async with database.engine.begin() as ddl:
                key = await ddl.scalar(
                    text("""
                        SELECT conname FROM pg_catalog.pg_constraint
                        WHERE conrelid = 'public.tariff_field_documentation'::regclass
                            AND contype = 'f'
                    """)
                )
                quote = ddl.dialect.identifier_preparer.quote_identifier
                replacement = key if keep_key_name else "catalogue_replacement"
                await ddl.exec_driver_sql(f"""
                    ALTER TABLE public.tariff_field_documentation
                    DROP CONSTRAINT {quote(key)},
                    ADD CONSTRAINT {quote(replacement)} FOREIGN KEY (tariff_plan_type_id)
                        REFERENCES {alternate_schema}.tariff_plan_types (id) ON DELETE CASCADE
                """)
            await stale.execute(insert(TABLE).values(**values))
    assert old_snapshot.value.orig.sqlstate == "40001"

    async with database.engine.begin() as fresh:
        public_version = text("SELECT ctid::text FROM public.coordination WHERE id = 1")
        alternate_version = text(
            f"SELECT ctid::text FROM {alternate_schema}.coordination WHERE id = 1"
        )
        public_before = await fresh.scalar(public_version)
        alternate_before = await fresh.scalar(alternate_version)
        await fresh.execute(insert(TABLE).values(**values))
        assert await fresh.scalar(public_version) == public_before
        assert await fresh.scalar(alternate_version) != alternate_before
        assert await fresh.scalar(select(TABLE.c.comment)) == "GiB"


async def test_create_if_not_exists_preserves_documentation_guards_and_readers(
    migration_database,
):
    """Leave an existing PostgreSQL table's triggers in place on a native CREATE IF NOT EXISTS."""
    database = migration_database
    if database.engine.dialect.name != "postgresql":
        pytest.skip("PostgreSQL locks the table while replacing triggers")
    trigger_oids = text("""
        SELECT oid FROM pg_catalog.pg_trigger
        WHERE tgrelid = 'public.tariff_field_documentation'::regclass AND NOT tgisinternal
        ORDER BY oid
    """)
    async with database.engine.begin() as reader:
        await reader.execute(select(TABLE.c.id))
        before = (await reader.scalars(trigger_oids)).all()
        async with database.engine.begin() as creating:
            await creating.exec_driver_sql("SET LOCAL lock_timeout = '100ms'")
            await creating.execute(CreateTable(TABLE, if_not_exists=True))
            assert (await creating.scalars(trigger_oids)).all() == before


async def test_create_if_not_exists_leaves_a_concurrently_created_table_alone(
    migration_database,
    alembic_config,
):
    """Leave the winning transaction's table untouched when CREATE IF NOT EXISTS skips it."""
    database = migration_database
    if database.engine.dialect.name != "postgresql":
        pytest.skip("PostgreSQL supports a deterministic DDL start gate")
    await database.close()
    await anyio.to_thread.run_sync(alembic.downgrade, alembic_config, "0022")
    async with database.engine.begin() as setup:
        if not await setup.scalar(
            text("SELECT rolsuper FROM pg_roles WHERE rolname = current_user")
        ):
            pytest.skip("Creating the test-only DDL event trigger requires a PostgreSQL superuser")
        await setup.exec_driver_sql("""
            CREATE FUNCTION pause_documentation_create() RETURNS event_trigger
            LANGUAGE plpgsql AS $gate$
            BEGIN
                IF pg_catalog.current_setting('throng_test.pause_documentation_create', true) = 'on' THEN
                    PERFORM pg_catalog.pg_advisory_xact_lock(52427, 1);
                END IF;
            END;
            $gate$
        """)
        await setup.exec_driver_sql("""
            CREATE EVENT TRIGGER pause_documentation_create ON ddl_command_start
            WHEN TAG IN ('CREATE TABLE') EXECUTE FUNCTION pause_documentation_create()
        """)

    starting = asyncio.get_running_loop().create_future()

    async def create_if_missing():
        """Start CREATE while another transaction still holds the DDL gate."""
        async with database.engine.begin() as creating:
            await creating.exec_driver_sql(
                "SET LOCAL throng_test.pause_documentation_create = 'on'"
            )
            await creating.exec_driver_sql("SET LOCAL statement_timeout = '10s'")
            starting.set_result(await creating.scalar(text("SELECT pg_backend_pid()")))
            await creating.execute(CreateTable(TABLE, if_not_exists=True))

    creating = None
    try:
        async with database.engine.begin() as winner:
            holder = await winner.scalar(text("SELECT pg_backend_pid()"))
            await winner.exec_driver_sql("SELECT pg_catalog.pg_advisory_xact_lock(52427, 1)")
            creating = asyncio.create_task(create_if_missing())
            waiting = await asyncio.wait_for(starting, 10)
            await wait_for_postgres_blocker(database, holder, waiting)
            await winner.exec_driver_sql(f"CREATE TABLE {TABLE.name} (id uuid)")
    finally:
        if creating is not None:
            await asyncio.wait_for(creating, 10)
    async with database.engine.begin() as connection:
        columns = await connection.run_sync(lambda sync: inspect(sync).get_columns(TABLE.name))
        assert [column["name"] for column in columns] == ["id"]


@pytest.mark.parametrize("if_not_exists", [False, True])
async def test_create_documentation_uses_creation_schema_when_a_temporary_table_shadows_it(
    migration_database, alembic_config, if_not_exists
):
    """Create the real table and its guards in the target schema even if a temp table is visible."""
    database = migration_database
    if database.engine.dialect.name != "postgresql":
        pytest.skip("PostgreSQL searches temporary tables first")
    await database.close()
    await anyio.to_thread.run_sync(alembic.downgrade, alembic_config, "0022")
    async with database.engine.begin() as connection:
        await connection.exec_driver_sql(f"CREATE TEMP TABLE {TABLE.name} (id uuid)")
        await connection.execute(CreateTable(TABLE, if_not_exists=if_not_exists))
        assert await documentation_guard_is_active(connection, f"public.{TABLE.name}")
        assert await connection.scalar(text(f"SELECT count(*) FROM pg_temp.{TABLE.name}")) == 0


@pytest.mark.parametrize("table", [TABLE, PaymentAccountTariffPlan.__table__])
@pytest.mark.parametrize("uppercase_shadow", [False, True])
@pytest.mark.parametrize("no_parameters", [False, True])
async def test_billing_metadata_creates_the_index_on_the_permanent_table(
    migration_database, table, uppercase_shadow, no_parameters
):
    """Do not put a permanent table's index on a same-named temporary relation."""
    async with migration_database.engine.begin() as connection:
        schema = "public" if connection.dialect.name == "postgresql" else "main"
        temporary = table.name.upper() if uppercase_shadow else table.name
        await connection.run_sync(lambda sync: Base.metadata.drop_all(sync, tables=[table]))
        await connection.exec_driver_sql(f'CREATE TEMPORARY TABLE "{temporary}" (id integer)')
        await connection.execution_options(no_parameters=no_parameters)
        await connection.run_sync(
            lambda sync: Base.metadata.create_all(sync, tables=[table], checkfirst=False)
        )
        indexes = await connection.run_sync(
            lambda sync: inspect(sync).get_indexes(table.name, schema=schema)
        )
        assert {index["name"] for index in indexes} == {index.name for index in table.indexes}
        await connection.exec_driver_sql(
            f'DROP TABLE {"pg_temp" if schema == "public" else "temp"}."{temporary}"'
        )


@pytest.mark.parametrize("enabled", [False, True])
async def test_documentation_table_create_respects_index_conditions(
    migration_database, monkeypatch, enabled
):
    """Respect an explicit Index.ddl_if and send an allowed index to the permanent table."""
    index = next(iter(TABLE.indexes))
    monkeypatch.setattr(index, "_ddl_if", index._ddl_if)
    async with migration_database.engine.begin() as connection:
        dialect = connection.dialect.name
        schema = "public" if dialect == "postgresql" else "main"
        excluded = "sqlite" if dialect == "postgresql" else "postgresql"
        index.ddl_if(dialect=dialect if enabled else excluded)
        await connection.run_sync(TABLE.drop)
        await connection.exec_driver_sql(f"CREATE TEMPORARY TABLE {TABLE.name} (id integer)")
        await connection.run_sync(TABLE.create)
        indexes = await connection.run_sync(
            lambda sync: inspect(sync).get_indexes(TABLE.name, schema=schema)
        )
        assert [row["name"] for row in indexes] == ([index.name] if enabled else [])


async def test_documentation_index_declared_after_model_setup_stays_with_its_table(
    migration_database,
):
    """Route both built-in and additional indexes to the table being created."""
    index = Index("ix_doc_locale_test", TABLE.c.language_code)
    try:
        async with migration_database.engine.begin() as connection:
            schema = "public" if connection.dialect.name == "postgresql" else "main"
            await connection.run_sync(TABLE.drop)
            await connection.exec_driver_sql(f"CREATE TEMPORARY TABLE {TABLE.name} (id integer)")
            await connection.run_sync(TABLE.create)
            indexes = await connection.run_sync(
                lambda sync: inspect(sync).get_indexes(TABLE.name, schema=schema)
            )
            assert {row["name"] for row in indexes} == {index.name for index in TABLE.indexes}
    finally:
        TABLE.indexes.remove(index)


async def test_automatic_concurrent_index_enforces_uniqueness_on_the_postgres_table(
    migration_database,
    plan_type,
):
    """Keep a concurrent index on the permanent relation during autocommit table creation."""
    database = migration_database
    if database.engine.dialect.name != "postgresql":
        pytest.skip("PostgreSQL supports CREATE INDEX CONCURRENTLY")
    async with database.engine.begin() as connection:
        await connection.run_sync(TABLE.drop)
    index = Index(
        "ix_docs_concurrent_locale",
        TABLE.c.language_code,
        unique=True,
        postgresql_concurrently=True,
    )
    try:
        async with database.engine.connect() as connection:
            await connection.execution_options(isolation_level="AUTOCOMMIT")
            await connection.exec_driver_sql(f"CREATE TEMP TABLE {TABLE.name} (language_code text)")
            await connection.run_sync(TABLE.create)
            await connection.execution_options(schema_translate_map={None: "public"})
            await connection.execute(
                insert(TABLE).values(
                    tariff_plan_type_id=plan_type.id,
                    field_name="limit",
                    language_code="en",
                    comment="First",
                )
            )
            with pytest.raises(IntegrityError, match=index.name):
                await connection.execute(
                    insert(TABLE).values(
                        tariff_plan_type_id=plan_type.id,
                        field_name="other",
                        language_code="en",
                        comment="Second",
                    )
                )
            await connection.exec_driver_sql(f"DROP TABLE pg_temp.{TABLE.name}")
    finally:
        TABLE.indexes.remove(index)


@pytest.mark.parametrize("phase", ["before_cursor", "after_table", "before_index"])
async def test_automatic_index_keeps_the_creation_schema_when_a_ddl_hook_changes_search_path(
    migration_database,
    phase,
):
    """Use the table's creation namespace even after a user DDL hook changes name lookup."""
    database = migration_database
    if database.engine.dialect.name != "postgresql":
        pytest.skip("PostgreSQL DDL hooks can change the current schema")
    index = next(iter(TABLE.indexes))
    path = "SET LOCAL search_path TO extension_holder, docs_created, public"

    def shift_on_index(_index, connection, **_):
        """Change the ambient lookup before a native index is emitted."""
        connection.exec_driver_sql(path)

    def shift_after_table(connection, statement, *_):
        """Change the ambient lookup after the table DDL completes."""
        if isinstance(statement, CreateTable) and statement.element is TABLE:
            connection.exec_driver_sql(path)

    def shift_before_table(connection, _cursor, _sql, _params, context, _many):
        """Change the creation schema immediately before the driver receives the table DDL."""
        statement = getattr(context.compiled, "statement", None)
        if isinstance(statement, CreateTable) and statement.element is TABLE:
            connection.exec_driver_sql("SET LOCAL search_path TO docs_created, public")

    async with database.engine.begin() as connection:
        await connection.execute(CreateSchema("docs_created"))
        await connection.execute(CreateSchema("extension_holder"))
        if phase == "before_cursor":
            await connection.exec_driver_sql(path)
            target, name, listener = (
                connection.sync_connection,
                "before_cursor_execute",
                shift_before_table,
            )
        else:
            await connection.exec_driver_sql("SET LOCAL search_path TO docs_created, public")
        if phase == "after_table":
            target, name, listener = (
                connection.sync_connection,
                "after_execute",
                shift_after_table,
            )
        elif phase == "before_index":
            target, name, listener = index, "before_create", shift_on_index
        event.listen(target, name, listener)
        try:
            await connection.run_sync(TABLE.create)
        finally:
            event.remove(target, name, listener)
        indexes = await connection.run_sync(
            lambda sync: inspect(sync).get_indexes(TABLE.name, schema="docs_created")
        )
        assert [row["name"] for row in indexes] == [index.name]


@pytest.mark.parametrize("table", [TABLE, PaymentAccountTariffPlan.__table__])
async def test_direct_index_create_uses_the_native_namespace_lookup(migration_database, table):
    """Keep standalone Index.create and Index.drop independent of table creation's namespace."""
    database = migration_database
    index = next(iter(table.indexes))
    async with database.engine.begin() as connection:
        await connection.run_sync(index.drop)
        if connection.dialect.name == "postgresql":
            schema = "public"
            await connection.execute(CreateSchema("index_front"))
            await connection.exec_driver_sql("SET LOCAL search_path TO index_front, public")
        else:
            schema = "temp"
            await connection.run_sync(lambda sync: Base.metadata.drop_all(sync, tables=[table]))
            column = next(iter(index.columns)).name
            await connection.exec_driver_sql(f"CREATE TEMPORARY TABLE {table.name} ({column} text)")
        await connection.run_sync(index.create)
        indexes = await connection.run_sync(
            lambda sync: inspect(sync).get_indexes(table.name, schema=schema)
        )
        assert [row["name"] for row in indexes] == [index.name]


@pytest.mark.parametrize("explicit_statement", [False, True])
async def test_standalone_documentation_index_uses_a_visible_temporary_table(
    migration_database, explicit_statement
):
    """Allow standalone index creation to follow native lookup even when both tables exist."""
    index = next(iter(TABLE.indexes))
    async with migration_database.engine.begin() as connection:
        await connection.exec_driver_sql(
            f"CREATE TEMPORARY TABLE {TABLE.name} (tariff_plan_type_id text)"
        )
        if explicit_statement:
            await connection.execute(CreateIndex(index))
        else:
            await connection.run_sync(index.create)
        schema = (
            "temp"
            if connection.dialect.name == "sqlite"
            else await connection.scalar(
                text("""
            SELECT nspname FROM pg_catalog.pg_namespace WHERE oid = pg_catalog.pg_my_temp_schema()
        """)
            )
        )
        indexes = await connection.run_sync(
            lambda sync: inspect(sync).get_indexes(TABLE.name, schema=schema)
        )
        assert [row["name"] for row in indexes] == [index.name]


async def test_failed_index_condition_leaves_later_standalone_index_creation_unmodified(
    migration_database, monkeypatch
):
    """Release the automatic index's scope even when SQLAlchemy's native condition raises."""
    index = next(iter(TABLE.indexes))
    monkeypatch.setattr(index, "_ddl_if", index._ddl_if)

    def reject_create(_ddl, _index, connection, **_kwargs):
        """Interrupt a schema visitor before it sends the index statement."""
        assert connection.execute(text("SELECT 1")).scalar_one() == 1
        raise RuntimeError("index creation interrupted")

    async with migration_database.engine.begin() as connection:
        await connection.run_sync(TABLE.drop)
        await connection.exec_driver_sql(
            f"CREATE TEMPORARY TABLE {TABLE.name} (tariff_plan_type_id text)"
        )
        index.ddl_if(callable_=reject_create)
        with pytest.raises(RuntimeError, match="index creation interrupted"):
            await connection.run_sync(TABLE.create)
        await connection.execute(CreateIndex(index))
        namespace = "main" if connection.dialect.name == "sqlite" else "public"
        assert (
            await connection.run_sync(
                lambda sync: inspect(sync).get_indexes(TABLE.name, schema=namespace)
            )
            == []
        )


def test_billing_metadata_keeps_its_native_mock_index_ddl():
    """Let SQLAlchemy's schema compiler emit standalone index DDL without a live connection."""
    statements = []

    def collect(statement, *_args, **_kwargs):
        """Compile each DDL command in the mock engine's PostgreSQL dialect."""
        statements.append(str(statement.compile(dialect=engine.dialect)))

    engine = create_mock_engine("postgresql+asyncpg://", collect)
    Base.metadata.create_all(engine, tables=[TariffPlanType.__table__, TABLE], checkfirst=False)
    assert any(
        sql.startswith("CREATE INDEX ix_tariff_field_documentation_tariff_plan_type_id")
        for sql in statements
    )


async def test_metadata_checkfirst_keeps_the_legacy_catalogue_at_its_installed_revision(
    migration_database, alembic_config
):
    """Distinguish the working 0021 installation from a partially created catalogue."""
    database = migration_database
    if database.engine.dialect.name != "postgresql":
        pytest.skip("PostgreSQL restores interrupted catalogue installation")
    await database.close()
    await anyio.to_thread.run_sync(alembic.downgrade, alembic_config, "0021")
    async with database.engine.begin() as connection:
        await connection.run_sync(
            lambda sync: Base.metadata.create_all(sync, tables=[PaymentAccountTariffPlan.__table__])
        )
        assert await connection.scalar(text("SELECT version_num FROM alembic_version")) == "0021"
        assert (
            await connection.scalar(text("SELECT to_regprocedure('serialize_billing_writes()')"))
            is None
        )
        assert (
            await connection.scalar(
                text("""
            SELECT count(*) FROM pg_catalog.pg_trigger
            WHERE tgrelid = 'payment_accounts'::regclass AND tgname = :trigger
                AND tgfoid = pg_catalog.to_regprocedure(:routine)
        """),
                {"trigger": TRIGGER_NAME, "routine": f"{TRIGGER_NAME}()"},
            )
            == 1
        )


@pytest.mark.parametrize("use_default", [False, True])
async def test_metadata_checkfirst_resolves_the_requested_catalogue_schema(
    migration_database, use_default
):
    """Find an existing assignment table and catalogue outside the current PostgreSQL search path."""
    database = migration_database
    async with database.engine.begin() as connection:
        family = connection.dialect.name
        if family == "postgresql" and not use_default:
            await connection.exec_driver_sql("SET LOCAL search_path TO pg_catalog")
        schema = None if use_default else connection.dialect.default_schema_name
        await connection.execution_options(schema_translate_map={None: schema})
        assignments = PaymentAccountTariffPlan.__table__
        await connection.run_sync(lambda sync: Base.metadata.create_all(sync, tables=[assignments]))
        assert await connection.scalar(select(assignments.c.tariff_plan_id)) is None


async def test_translated_catalogue_metadata_keeps_its_own_guards(migration_database):
    """Install and remove only the catalogue selected by schema translation."""
    database = migration_database
    if database.engine.dialect.name != "postgresql":
        pytest.skip("PostgreSQL installs catalogue guards in the schema of the assignment table")
    public_guard = text("""
        SELECT oid FROM pg_catalog.pg_trigger
        WHERE tgrelid = 'public.tariff_plan_types'::regclass AND tgname = 'serialize_billing_writes'
    """)
    async with database.engine.begin() as connection:
        original = await connection.scalar(public_guard)
        await connection.execute(CreateSchema("another_catalogue"))
        await connection.execution_options(schema_translate_map={None: "another_catalogue"})
        await connection.run_sync(Base.metadata.create_all)
        table = "another_catalogue.tariff_field_documentation"
        coordination = "another_catalogue.coordination"
        assert await documentation_guard_is_active(connection, table, coordination)
        assert await connection.scalar(public_guard) == original

        selected = [PaymentAccountTariffPlan.__table__]
        await connection.run_sync(lambda sync: Base.metadata.drop_all(sync, tables=selected))
        assert not await documentation_guard_is_active(connection, table, coordination)
        assert await connection.scalar(public_guard) == original


async def test_catalogue_checkfirst_and_drop_ignore_a_temporary_assignment_table(
    migration_database,
):
    """Leave the real catalogue alone when SQLAlchemy selects a temporary namesake."""
    database = migration_database
    if database.engine.dialect.name != "postgresql":
        pytest.skip("PostgreSQL can shadow a catalogue table with a temporary relation")
    assignments = PaymentAccountTariffPlan.__table__
    async with database.engine.begin() as connection:
        await connection.exec_driver_sql(
            f"CREATE TEMP TABLE {assignments.name} (id uuid) ON COMMIT DROP"
        )
        await connection.run_sync(lambda sync: Base.metadata.create_all(sync, tables=[assignments]))
        assert await documentation_guard_is_active(connection)
        await connection.run_sync(lambda sync: Base.metadata.drop_all(sync, tables=[assignments]))
        assert (
            await connection.scalar(text(f"SELECT to_regclass('pg_temp.{assignments.name}')"))
            is None
        )
        assert (
            await connection.scalar(text(f"SELECT to_regclass('public.{assignments.name}')"))
            is not None
        )
        assert await documentation_guard_is_active(connection)


async def test_forced_catalogue_create_uses_the_creation_schema_with_a_temporary_shadow(
    migration_database,
):
    """Install the recreated catalogue in its permanent schema while a temp namesake remains."""
    database = migration_database
    if database.engine.dialect.name != "postgresql":
        pytest.skip("PostgreSQL resolves unqualified CREATE separately from lookup")
    assignments = PaymentAccountTariffPlan.__table__
    async with database.engine.begin() as connection:
        await connection.run_sync(lambda sync: Base.metadata.drop_all(sync, tables=[assignments]))
        await connection.exec_driver_sql(
            f"CREATE TEMP TABLE {assignments.name} (id uuid, tariff_plan_id uuid) ON COMMIT DROP"
        )
        await connection.run_sync(
            lambda sync: Base.metadata.create_all(sync, tables=[assignments], checkfirst=False)
        )
        assert await documentation_guard_is_active(connection)
        assert (
            await connection.scalar(text(f"SELECT to_regclass('pg_temp.{assignments.name}')"))
            is not None
        )
        assert (
            await connection.scalar(text(f"SELECT to_regclass('public.{assignments.name}')"))
            is not None
        )


@pytest.mark.parametrize("rename", [False, True])
async def test_documentation_guard_follows_a_restored_or_renamed_catalogue(
    migration_database, rename
):
    """Keep the transition fence after changing the catalogue's physical identity or schema name."""
    database = migration_database
    if database.engine.dialect.name != "postgresql":
        pytest.skip("PostgreSQL logical restoration can assign new schema OIDs")
    schema = "restored_tariffs"
    initial = "tariffs_before_rename" if rename else schema
    translated = {None: schema}
    assignments = [PaymentAccountTariffPlan.__table__]
    namespace = text("SELECT pg_catalog.to_regnamespace('restored_tariffs')::oid")
    routine = text("""
        SELECT pg_catalog.pg_get_functiondef('restored_tariffs.serialize_tariff_documentation_writes()'::regprocedure)
    """)
    async with database.engine.begin() as connection:
        await connection.execute(CreateSchema(initial))
        await connection.execution_options(schema_translate_map={None: initial})
        await connection.run_sync(Base.metadata.create_all)
        if rename:
            await connection.run_sync(lambda sync: Base.metadata.drop_all(sync, tables=assignments))
            await connection.exec_driver_sql(f"ALTER SCHEMA {initial} RENAME TO {schema}")
        else:
            saved_oid = await connection.scalar(namespace)
            saved_function = await connection.scalar(routine)
            await connection.execute(DropSchema(schema, cascade=True))
            await connection.execute(CreateSchema(schema))
            await connection.run_sync(Base.metadata.create_all)
            assert await connection.scalar(namespace) != saved_oid
            await connection.run_sync(lambda sync: Base.metadata.drop_all(sync, tables=assignments))
            await connection.exec_driver_sql(saved_function)

    table = "restored_tariffs.tariff_field_documentation"
    coordination = "restored_tariffs.coordination"
    async with database.engine.begin() as writer:
        assert not await documentation_guard_is_active(writer, table, coordination)
        with pytest.raises(DBAPIError) as conflict:
            async with database.engine.begin() as upgrading:
                await upgrading.execution_options(schema_translate_map=translated)
                await upgrading.run_sync(
                    lambda sync: Base.metadata.create_all(sync, tables=assignments)
                )
        assert conflict.value.orig.sqlstate == "55P03"
    async with database.engine.begin() as connection:
        await connection.execution_options(schema_translate_map=translated)
        await connection.run_sync(lambda sync: Base.metadata.create_all(sync, tables=assignments))
        assert await documentation_guard_is_active(connection, table, coordination)


async def test_autocommit_documentation_creation_installs_guards_before_exposing_the_table(
    migration_database, alembic_config, plan_type
):
    """Allow a writer after the documentation index is built without delaying guard installation."""
    database = migration_database
    if database.engine.dialect.name != "postgresql":
        pytest.skip("PostgreSQL has a separate catalogue coordination guard")
    await database.close()
    await anyio.to_thread.run_sync(alembic.downgrade, alembic_config, "0022")
    async with database.engine.begin() as writer, database.engine.connect() as creating:
        await creating.execution_options(isolation_level="AUTOCOMMIT")
        await creating.exec_driver_sql("SET lock_timeout = '100ms'")

        async def save_document(_driver):
            """Write to the already committed table as another client."""
            await writer.execute(
                insert(TABLE).values(
                    tariff_plan_type_id=plan_type.id,
                    field_name="limit",
                    language_code="en",
                    comment="GiB",
                )
            )

        def after_index(connection, _cursor, statement, *_):
            """Use the point at which another client can use the finished table and index."""
            if "CREATE INDEX ix_tariff_field_documentation_tariff_plan_type_id" in statement:
                connection.connection.run_async(save_document)

        event.listen(creating.sync_connection, "after_cursor_execute", after_index)
        try:
            await creating.run_sync(lambda sync: Base.metadata.create_all(sync, tables=[TABLE]))
        finally:
            event.remove(creating.sync_connection, "after_cursor_execute", after_index)
            await creating.exec_driver_sql("RESET lock_timeout")
    async with database.engine.begin() as connection:
        assert await connection.scalar(select(TABLE.c.comment)) == "GiB"
        assert await documentation_guard_is_active(connection)


@pytest.mark.parametrize("schema", ["docs_translated", "Docs'\\%$documentation_ddl$__[SCHEMA_x]"])
async def test_translated_documentation_ddl_targets_its_own_table(migration_database, schema):
    """Install and remove guards in the translated schema while retaining the original table's guard."""
    if migration_database.engine.dialect.name != "postgresql":
        pytest.skip("PostgreSQL supports documentation tables in multiple schemas")
    guard_count = text("""
        SELECT count(*) FROM pg_catalog.pg_trigger
        WHERE tgrelid = pg_catalog.to_regclass(:relation) AND NOT tgisinternal
    """)
    async with migration_database.engine.begin() as connection:
        await connection.execute(CreateSchema(schema))
        namespace = connection.dialect.identifier_preparer.quote_schema(schema)
        translated = {"relation": f"{namespace}.{TABLE.name}"}
        original = {"relation": f"public.{TABLE.name}"}
        await connection.execution_options(schema_translate_map={None: schema})
        await connection.run_sync(
            lambda sync: Base.metadata.create_all(sync, tables=[TariffPlanType.__table__, TABLE])
        )
        assert await connection.scalar(guard_count, translated) == 2
        assert await connection.scalar(guard_count, original) == 2
        await connection.run_sync(lambda sync: Base.metadata.drop_all(sync, tables=[TABLE]))
        assert await connection.scalar(guard_count, translated) == 0
        assert await connection.scalar(guard_count, original) == 2
        assert not await connection.scalar(
            text("SELECT pg_catalog.to_regprocedure(:routine) IS NOT NULL"),
            {"routine": f"{namespace}.serialize_tariff_documentation_writes()"},
        )
        assert await documentation_guard_is_active(connection)


@pytest.mark.parametrize("operation", [CreateTable, DropTable])
def test_atomic_postgres_ddl_is_limited_to_tariff_documentation(operation):
    """Preserve SQLAlchemy's standard DDL for other tables and render translated documentation DDL."""
    dialect = postgresql.dialect()
    ordinary = str(operation(TariffPlanType.__table__).compile(dialect=dialect))
    documentation = str(operation(TABLE).compile(dialect=dialect))
    translated = str(
        operation(TABLE).compile(dialect=dialect, schema_translate_map={None: "review_docs"})
    )
    command = "CREATE" if operation is CreateTable else "DROP"
    assert ordinary.lstrip().startswith(f"{command} TABLE tariff_plan_types")
    assert (
        documentation.startswith("DO ")
        and f"{command} TABLE " in documentation
        and "tariff_field_documentation" in documentation
    )
    assert (
        translated.startswith("DO ")
        and f"{command} TABLE " in translated
        and "review_docs.tariff_field_documentation" in translated
    )


def test_documentation_postgres_create_preserves_if_not_exists():
    """Keep the native operator and leave the existing table's guards intact."""
    statement = str(CreateTable(TABLE, if_not_exists=True).compile(dialect=postgresql.dialect()))
    assert "CREATE TABLE IF NOT EXISTS tariff_field_documentation" in statement
    assert "IF pg_catalog.cardinality(created_tables) = 0 THEN RETURN" in statement


def test_postgres_migration_guard_uses_a_qualified_relation_with_quoted_schema():
    """Quote an explicit target independently of the contents of its schema name."""
    relation = '"Billing\'s catalogue $definition$".tariff_field_documentation'
    sql = documentation_schema.create_documentation_guards(relation)
    assert sql.startswith("DO E'")
    assert "pg_catalog.to_regclass(E''\"Billing''''s catalogue $definition$\"" in sql


def test_postgres_automatic_documentation_index_scope_consumes_the_create_result():
    """Read the physical schema after CREATE without modifying the native DDL result."""
    dialect = postgresql.dialect()
    runner = SimpleNamespace(memo={documentation_schema.BILLING_INDEX_TABLE_CREATION: {}})
    connection = Mock(
        dialect=dialect, info={documentation_schema.BILLING_TABLE_CREATORS: {TABLE: runner}}
    )
    command = CreateTable(TABLE)
    marked, _, _ = documentation_schema.mark_documentation_creation(connection, command, [], {}, {})
    result = marked.compile(dialect=dialect)
    assert documentation_schema.CREATED_DOCUMENTATION_SCHEMA in str(result)
    assert documentation_schema.CREATED_DOCUMENTATION_SCHEMA not in str(
        command.compile(dialect=dialect)
    )
    probe = connection.connection.cursor.return_value
    probe.fetchone.return_value = ("table_namespace", "")
    documentation_schema.record_documentation_creation(
        connection, Mock(), str(result), (), SimpleNamespace(compiled=result), False
    )
    assert runner.memo[documentation_schema.BILLING_INDEX_TABLE_CREATION] == {
        TABLE: "table_namespace"
    }
    probe.close.assert_called_once()


@pytest.mark.parametrize(
    ("dialect", "namespace", "options", "expected"),
    [
        (postgresql.dialect(), "active_schema", {}, "active_schema"),
        (postgresql.dialect(), None, {"schema_translate_map": {}}, None),
        (sqlite.dialect(), None, {}, "main"),
    ],
)
def test_documentation_load_reuses_the_namespace_for_its_result_set(
    dialect, namespace, options, expected
):
    """Resolve the connection namespace once even when an ORM result loads multiple rows."""
    connection = Mock(dialect=dialect, info={})
    connection.execute.return_value.one_or_none.return_value = (
        (namespace, TABLE.name, 123) if namespace is not None else None
    )
    connection.get_execution_options.return_value = {}
    connection.exec_driver_sql.return_value.all.return_value = [(0, "main", "")]
    mapper = inspect(TariffFieldDocumentation)
    context = SimpleNamespace(
        user_passed_query=select(TariffFieldDocumentation),
        execution_options=options,
        session=Mock(connection=Mock(return_value=connection), info={}),
        bind_arguments={},
        attributes={},
        refresh_state=None,
        compile_state=SimpleNamespace(_entities=[SimpleNamespace(mapper=mapper, selectable=TABLE)]),
    )
    for _ in range(2):
        state = Mock(info={}, mapper=mapper)
        remember_documentation_schema(state, context)
        scope = state.info["billing_documentation_schema_scope"]
        assert scope.table_name == TABLE.name and scope.schema == expected
    if dialect.name == "postgresql":
        connection.execute.assert_called_once()
        assert connection.execute.call_args.args[1] == {"relation": TABLE.name}
    else:
        connection.execute.assert_not_called()
        assert connection.exec_driver_sql.call_count == 2
        connection.exec_driver_sql.assert_called_with("PRAGMA database_list")


@pytest.mark.parametrize(
    "case", ["other_connection", "dropped", "owned", "moved", "replaced", "denied", "disconnected"]
)
def test_temporary_documentation_reconciliation_uses_the_owning_postgres_connection(
    case,
):
    """Only probe a temporary relation while it exists on the connection that loaded it."""
    from throng_api.models import cascaded_field_documentation, documentation_connection_scope

    connection = Mock(dialect=postgresql.dialect(), info={})
    location = "pg_temp_42", TABLE.name, 123
    connection.execute.return_value.one_or_none.return_value = location
    scope = documentation_connection_scope(connection, TABLE.c.id, {None: "pg_temp"})
    assert scope.schema == "pg_temp_42" and scope.postgres_temp_connection is not None
    if case == "other_connection":
        connection.info.clear()
    connection.execute.reset_mock()
    connection.execute.return_value.one_or_none.return_value = (
        None if case == "dropped" else location
    )
    connection.begin_nested.return_value = nullcontext()
    connection.scalar.return_value = (
        None if case in ("moved", "replaced") else scope.postgres_relation_oid
    )
    connection.scalars.return_value = []
    if case in ("moved", "denied", "disconnected"):
        connection.scalars.side_effect = DBAPIError(
            None, None, RuntimeError(case), connection_invalidated=case == "disconnected"
        )
    state = Mock(
        mapper=inspect(TariffFieldDocumentation),
        identity=(str(uuid4()),),
        info={"billing_documentation_schema_scope": scope},
    )
    session = Mock(info={})
    session.connection.return_value = connection
    session.identity_map.all_states.return_value = [state]
    session.get_bind.return_value.engine = connection.engine
    if case in ("denied", "disconnected"):
        with pytest.raises(DBAPIError, match=case):
            cascaded_field_documentation(session)
    else:
        assert cascaded_field_documentation(session) == ([state] if case == "owned" else [])
    expected_reads = 2 if case in ("moved", "replaced") else int(case != "other_connection")
    assert connection.execute.call_count == expected_reads


@pytest.mark.parametrize("dialect", [sqlite.dialect(), default.DefaultDialect()])
def test_documentation_relation_without_a_discoverable_namespace_has_no_storage(dialect):
    """Preserve an unqualified source when the dialect cannot identify a visible relation."""
    from throng_api.models import documentation_connection_scope

    connection = Mock(dialect=dialect, info={})
    connection.exec_driver_sql.return_value.all.return_value = [(0, "main", ""), (1, "temp", "")]
    connection.scalar.return_value = None
    scope = documentation_connection_scope(connection, TABLE.c.id, {})
    assert scope.table_name == TABLE.name
    assert scope.schema is None and scope.sqlite_storage is None


def test_documentation_binding_recovers_a_live_pool_with_no_remaining_source_engine():
    """A different engine can still own the pool from an earlier load."""
    from throng_api.models import DOCUMENTATION_BIND_SCOPE, documentation_state_pool

    state, pool = Mock(), Mock()
    session = Mock(info={DOCUMENTATION_BIND_SCOPE: {state: (lambda: None, ref(pool))}})
    assert documentation_state_pool(session, state) is pool


@pytest.mark.parametrize("mixed_sources", [True, False])
def test_grouped_compound_select_considers_all_documentation_identifier_sources(mixed_sources):
    """A nested UNION can include sources absent from the innermost first branch."""
    from throng_api.models import documentation_statement_identifier

    metadata = MetaData()
    TariffPlanType.__table__.to_metadata(metadata, schema="archive")
    other = TABLE.to_metadata(metadata, schema="archive") if mixed_sources else TABLE
    statement = union_all(select(TABLE), union_all(select(TABLE), select(other)))
    assert documentation_statement_identifier(statement) is (None if mixed_sources else TABLE.c.id)


@pytest.mark.parametrize(
    ("matches", "expected"),
    [([True], "temp"), ([False, False, True], "attached, reports"), ([False] * 3, None)],
)
def test_sqlite_documentation_scope_follows_unqualified_database_order(matches, expected):
    """Search temp first and safely quote an attached SQLite database name."""
    from throng_api.models import sqlite_documentation_schema

    connection = Mock(dialect=sqlite.dialect())
    connection.exec_driver_sql.return_value.all.return_value = [
        (0, "main", ""),
        (1, "temp", ""),
        (2, "attached, reports", ""),
    ]
    connection.scalar.side_effect = matches
    assert sqlite_documentation_schema(connection, TABLE.name) == expected
    schemas = ["temp", "main", "attached, reports"][: len(matches)]
    for call, schema in zip(connection.scalar.call_args_list, schemas, strict=True):
        quoted = connection.dialect.identifier_preparer.quote_schema(schema)
        assert f"FROM {quoted}.sqlite_master" in str(call.args[0])
        assert call.args[1] == {"relation": TABLE.name}


def test_sqlite_documentation_storage_returns_none_for_an_unavailable_attachment():
    """A namespace that is no longer attached has no current storage identity."""
    connection = Mock()
    connection.exec_driver_sql.return_value.all.return_value = [(0, "main", "")]
    assert documentation_schema.sqlite_documentation_storage(connection, "gone") is None


def test_postgres_connect_does_not_record_sqlite_attachment_identity():
    """The SQLite attachment observer leaves PostgreSQL connection records alone."""
    record = SimpleNamespace(info={})
    documentation_schema.remember_sqlite_main_database(postgresql.dialect(), record, (), {})
    assert record.info == {}


def test_postgres_documentation_downgrade_compiles_physical_drop_checks():
    """Bind the drop validation to the captured PostgreSQL relation."""
    metadata = MetaData()
    TariffPlanType.__table__.to_metadata(metadata)
    documentation = TABLE.to_metadata(metadata)
    documentation.info[documentation_schema.DOCUMENTATION_DROP_OID] = 42
    statement = str(DropTable(documentation).compile(dialect=postgresql.dialect()))
    assert "selected_table IS DISTINCT FROM 42::oid" in statement
    assert "pg_class WHERE oid = selected_table" in statement


@pytest.mark.parametrize(
    ("statement", "parameters", "expected"),
    [
        (
            "/* test */ ATTACH DATABASE ? AS docs",
            ("file:reports%2F2026?cache=shared&mode=memory",),
            (b"reports/2026", b""),
        ),
        ("ATTACH 'file::memory:?cache=shared' AS docs", (), (b":memory:", b"")),
        (
            "ATTACH :uri AS docs",
            {"uri": "file:reports?cache=shared&mode=memory"},
            (b"reports", b""),
        ),
        (
            "ATTACH ?2 AS docs",
            (None, "file:reports?mode=memory&cache=shared"),
            (b"reports", b""),
        ),
        (
            "ATTACH $uri AS docs",
            ("file:reports?mode=memory&cache=shared",),
            (b"reports", b""),
        ),
        ("ATTACH 'file:private?mode=memory&cache=private' AS docs", (), None),
        ("ATTACH 'file:report.sqlite?cache=shared' AS docs", (), None),
        ("ATTACH DATABASE ? AS docs", (None,), None),
        ("ATTACH :uri AS docs", {"uri": "file:?mode=memory&cache=shared"}, None),
        ("ATTACH :uri AS docs", {"uri": "file:%00hidden?mode=memory&cache=shared"}, None),
        (
            "ATTACH :uri AS docs",
            {"uri": "file:reports%00suffix?mode=memory&cache=shared%00suffix"},
            (b"reports", b""),
        ),
        ("ATTACH 'file:reports%ff?mode=memory&cache=shared' AS docs", (), (b"reports\xff", b"")),
        ("ATTACH 'file:reports%fe?mode=memory&cache=shared' AS docs", (), (b"reports\xfe", b"")),
        ("ATTACH 'file:re\tports?mode=memory&cache=shared' AS docs", (), (b"re\tports", b"")),
        (
            "ATTACH 'file:reports?mode=memory&cache=shared&vfs=unix' AS docs",
            (),
            (b"reports", b"unix"),
        ),
        (
            "ATTACH 'file:reports?mode=memory&cache=shared&vfs=unix-dotfile' AS docs",
            (),
            (b"reports", b"unix-dotfile"),
        ),
        (
            "ATTACH 'file://localhost/reports?mode=memory&cache=shared' AS docs",
            (),
            (b"/reports", b""),
        ),
        ("ATTACH 'file://other/reports?mode=memory&cache=shared' AS docs", (), None),
        ("ATTACH 'file:' || 'report.sqlite' AS docs", (), None),
    ],
)
def test_sqlite_attachment_scope_uses_only_a_recognized_shared_memory_uri(
    statement, parameters, expected
):
    """Associate the same named memory database across connections when its URI identifies it."""
    if expected is not None and not expected[1]:
        expected = expected[0], documentation_schema.SQLITE_DEFAULT_VFS
    assert documentation_schema.sqlite_shared_documentation_key(statement, parameters) == expected


def test_postgres_billing_index_compiler_preserves_an_explicit_schema_and_concurrently():
    """Use the ordinary PostgreSQL compiler when the index target is explicit or concurrent."""
    dialect = postgresql.dialect()
    docs = TABLE.to_metadata(MetaData())
    index = next(iter(docs.indexes))
    automatic = CreateIndex(index).execution_options(billing_index_table_creation="public")
    managed = str(automatic.compile(dialect=dialect))
    standalone = str(CreateIndex(index).compile(dialect=dialect))
    translated = str(
        automatic.compile(
            dialect=dialect,
            schema_translate_map={None: "catalogue"},
            render_schema_translate=True,
        )
    )
    index.dialect_options["postgresql"]["concurrently"] = True
    concurrent = str(automatic.compile(dialect=dialect))
    assert managed.startswith("CREATE INDEX ") and "ON public.tariff_field_documentation" in managed
    assert standalone.startswith("CREATE INDEX ")
    assert "ON catalogue.tariff_field_documentation" in translated
    assert (
        concurrent.startswith("CREATE INDEX CONCURRENTLY ")
        and "ON public.tariff_field_documentation" in concurrent
    )


def test_sqlite_billing_index_compiler_qualifies_only_automatic_indexes():
    """Keep SQLite's standalone and explicitly translated index targets available."""
    index = next(iter(TABLE.indexes))
    dialect = sqlite.dialect()
    automatic = CreateIndex(index).execution_options(billing_index_table_creation="main")
    assert str(CreateIndex(index).compile(dialect=dialect)).startswith(f"CREATE INDEX {index.name}")
    assert str(automatic.compile(dialect=dialect)).startswith(f"CREATE INDEX main.{index.name}")
    translated = automatic.compile(
        dialect=dialect,
        schema_translate_map={None: "catalogue"},
        render_schema_translate=True,
    )
    assert str(translated).startswith(f"CREATE INDEX catalogue.{index.name}")


def test_catalogue_postgres_drop_keeps_guard_removal_and_table_drop_together():
    """Compile native PostgreSQL DROP with guard cleanup in the same statement."""
    table = PaymentAccountTariffPlan.__table__
    dialect = postgresql.dialect(paramstyle="named")
    statement = str(DropTable(table, if_exists=True).compile(dialect=dialect))
    assert statement.startswith("DO ")
    assert "DROP TABLE IF EXISTS " in statement and table.name in statement
    assert "DROP FUNCTION IF EXISTS %1$I.serialize_billing_writes()" in statement


async def test_postgres_catalogue_metadata_does_not_require_ownership_of_documentation(
    migration_database,
):
    """Retire and rebuild the shared guards while a different owner retains their documentation."""
    if migration_database.engine.dialect.name != "postgresql":
        pytest.skip("PostgreSQL supports separate schema and table owners")
    catalogue_role = "catalogue_" + uuid4().hex
    docs_role = "docs_" + uuid4().hex
    tables = [PaymentAccountTariffPlan.__table__]
    async with migration_database.engine.connect() as connection:
        transaction = await connection.begin()
        try:
            can_create_roles = await connection.scalar(
                text("""
                SELECT rolcreaterole OR rolsuper FROM pg_catalog.pg_roles WHERE rolname = current_user
            """)
            )
            if not can_create_roles:
                pytest.skip("The test database administrator cannot create roles")
            for schema, role in (
                ("role_catalogue", catalogue_role),
                ("role_docs", docs_role),
            ):
                await connection.exec_driver_sql(f'CREATE ROLE "{role}"')
                await connection.exec_driver_sql(f'CREATE SCHEMA {schema} AUTHORIZATION "{role}"')
            await connection.exec_driver_sql(f'SET LOCAL ROLE "{catalogue_role}"')
            await connection.exec_driver_sql("SET LOCAL search_path TO role_catalogue")
            await connection.run_sync(Base.metadata.create_all)
            await connection.exec_driver_sql(
                "INSERT INTO coordination (id, revision) VALUES (1, 0)"
            )
            plan_type_id = await connection.scalar(
                insert(TariffPlanType.__table__)
                .values(name="Storage", field_types={})
                .returning(TariffPlanType.id)
            )
            await connection.exec_driver_sql(
                f'GRANT USAGE ON SCHEMA role_catalogue TO "{docs_role}"'
            )
            await connection.exec_driver_sql(
                f'GRANT REFERENCES ON tariff_plan_types TO "{docs_role}"'
            )
            await connection.exec_driver_sql(
                f'GRANT SELECT, INSERT, UPDATE ON coordination TO "{docs_role}"'
            )

            await connection.exec_driver_sql("RESET ROLE")
            await connection.exec_driver_sql(f'SET LOCAL ROLE "{docs_role}"')
            await connection.exec_driver_sql("SET LOCAL search_path TO role_docs, role_catalogue")
            await connection.run_sync(
                lambda sync: Base.metadata.create_all(sync, tables=[TABLE], checkfirst=False)
            )
            await connection.execute(
                insert(TABLE).values(
                    tariff_plan_type_id=plan_type_id,
                    field_name="limit",
                    language_code="en",
                    comment="GiB",
                )
            )
            assert await documentation_guard_is_active(connection)

            await connection.exec_driver_sql("RESET ROLE")
            await connection.exec_driver_sql(f'SET LOCAL ROLE "{catalogue_role}"')
            await connection.exec_driver_sql("SET LOCAL search_path TO role_catalogue")
            await connection.run_sync(lambda sync: Base.metadata.drop_all(sync, tables=tables))
            await connection.run_sync(lambda sync: Base.metadata.create_all(sync, tables=tables))

            await connection.exec_driver_sql("RESET ROLE")
            await connection.exec_driver_sql(f'SET LOCAL ROLE "{docs_role}"')
            await connection.exec_driver_sql("SET LOCAL search_path TO role_docs, role_catalogue")
            assert await connection.scalar(select(TABLE.c.comment)) == "GiB"
            assert await documentation_guard_is_active(connection)
            await connection.run_sync(lambda sync: Base.metadata.drop_all(sync, tables=[TABLE]))
        finally:
            await transaction.rollback()


async def documentation_revision(database):
    """Read the physical table and revision marker from a separate connection."""
    async with database.engine.connect() as connection:
        exists = await connection.run_sync(lambda sync: inspect(sync).has_table(TABLE.name))
        return await connection.scalar(text("SELECT version_num FROM alembic_version")), exists


@pytest.mark.parametrize(
    ("lock_timeout", "version_schema", "already_locked"),
    [(None, None, False), ("1ms", "Version schema", False), (None, None, True)],
)
def test_postgres_billing_migration_scope_restores_the_connection_settings(
    lock_timeout, version_schema, already_locked
):
    """Use the physical version schema and restore the connection before Alembic updates it."""
    from throng_api.migrations.billing import BILLING_VERSION_RELATION, BILLING_VERSION_SCHEMA

    postgres = postgresql.dialect()
    schema = "Billing, primary"
    connection = Mock(dialect=postgres, info={})
    if already_locked:
        connection.info[BILLING_VERSION_SCHEMA] = schema, 42
    connection.execute.return_value.one_or_none.return_value = (schema, 42, 43, 43)
    context = SimpleNamespace(
        version_table="migration history", version_table_schema=version_schema
    )
    previous = {
        "SHOW search_path": '"$user", public',
        "SHOW row_security": "on",
        "SHOW lock_timeout": "20ms",
    }
    connection.exec_driver_sql.side_effect = lambda sql: Mock(
        scalar_one=Mock(return_value=previous.get(sql))
    )
    with billing_migration_schema(connection, context, lock_timeout=lock_timeout) as scope:
        assert scope == (schema, '"Billing, primary".')
        assert connection.info[BILLING_VERSION_RELATION] == (
            version_schema,
            "migration history",
            42,
        )
    version_table = '"migration history"'
    if version_schema:
        version_table = '"Version schema".' + version_table
    bindings = [] if already_locked else [{"relation": version_table}]
    bindings.append({"path": '"Billing, primary", pg_temp'})
    if lock_timeout:
        bindings.extend([{"timeout": "1ms"}, {"timeout": previous["SHOW lock_timeout"]}])
    bindings.extend([{"path": previous["SHOW search_path"]}, {"previous": "on"}])
    assert [call.args[1] for call in connection.execute.call_args_list] == bindings
    commands = [call.args[0] for call in connection.exec_driver_sql.call_args_list]
    assert "SET LOCAL row_security = off" in commands


def test_billing_revision_update_only_accepts_a_row_from_the_original_relation():
    """Add PostgreSQL's physical table identity to Alembic's version UPDATE."""
    from throng_api.migrations.billing import (
        BILLING_VERSION_RELATION,
        constrain_billing_version_update,
    )

    owned_version = Table("history", MetaData(), Column("version_num", Text()), schema="billing")
    other_version = owned_version.to_metadata(MetaData(), schema="elsewhere")
    connection = Mock(
        dialect=postgresql.dialect(), info={BILLING_VERSION_RELATION: ("billing", "history", 42)}
    )
    unrelated = update(other_version).values(version_num="0022")
    assert constrain_billing_version_update(connection, unrelated, (), {}, {})[0] is unrelated
    read = select(owned_version)
    assert constrain_billing_version_update(connection, read, (), {}, {})[0] is read
    marked, _, _ = constrain_billing_version_update(
        connection, update(owned_version).values(version_num="0022"), (), {}, {}
    )
    compiled = marked.compile(dialect=postgresql.dialect())
    assert "WHERE CAST(pg_catalog.to_regclass(" in str(compiled)
    assert set(compiled.params.values()) == {"0022", "billing.history", 42}
    assert BILLING_VERSION_RELATION not in connection.info
    assert constrain_billing_version_update(connection, unrelated, (), {}, {})[0] is unrelated


def test_version_read_locks_its_pg_schema_after_cursor_routing():
    """Use the actual version table and reuse its locked namespace for later statements."""
    from throng_api.migrations.billing import (
        BILLING_VERSION_SCHEMA,
        BILLING_VERSION_TABLE,
        lock_schema_for_version_read,
    )

    version = Table("history", MetaData(), Column("version_num", Text()))
    shadow = version.to_metadata(MetaData(), schema="unrelated")
    connection = Mock(info={BILLING_VERSION_TABLE: (None, "history")})
    execution = SimpleNamespace(compiled=None, root_connection=connection)
    cursor = Mock()
    cursor.fetchone.return_value = ("routed", 42, 43, 43)
    lock_schema_for_version_read(cursor, "", (), execution)
    for statement in (update(version), select(literal(1)), select(version.alias()), select(shadow)):
        execution.compiled = statement.compile(dialect=postgresql.dialect())
        lock_schema_for_version_read(cursor, "", (), execution)
    cursor.execute.assert_not_called()
    execution.compiled = select(version.c.version_num).compile(dialect=postgresql.dialect())
    lock_schema_for_version_read(cursor, "", (), execution)
    lock_schema_for_version_read(cursor, "", (), execution)
    cursor.execute.assert_called_once()
    assert connection.info[BILLING_VERSION_SCHEMA] == ("routed", 42)
    assert "pg_catalog.to_regclass(E'history')" in cursor.execute.call_args.args[0]


@pytest.mark.parametrize("location", [None, ("routed", 42, 43, 44)])
def test_migration_rejects_a_missing_or_moved_version_schema(location):
    """Stop the migration if the schema lock cannot establish the version table's identity."""
    from throng_api.migrations.billing import checked_version_schema

    with pytest.raises(CommandError, match="Billing schema changed"):
        checked_version_schema(location)


@pytest.fixture
async def postgres_migration_role(migration_database, alembic_config):
    """Run real PostgreSQL migrations through a temporary restricted role."""
    database = migration_database
    if database.engine.dialect.name != "postgresql":
        pytest.skip("PostgreSQL supports separate relation owners and row policies")
    owner = "migration_docs_" + uuid4().hex
    async with database.engine.begin() as connection:
        if not await connection.scalar(
            text("SELECT rolsuper FROM pg_roles WHERE rolname = current_user")
        ):
            pytest.skip("This fixture changes roles on a separate migration connection")
        await connection.exec_driver_sql(f'CREATE ROLE "{owner}"')
        await connection.exec_driver_sql(f'GRANT USAGE, CREATE ON SCHEMA public TO "{owner}"')
        await connection.exec_driver_sql(f'GRANT SELECT, UPDATE ON alembic_version TO "{owner}"')
        await connection.exec_driver_sql(f'GRANT REFERENCES ON tariff_plan_types TO "{owner}"')

    def assume_owner(connection, _cursor, statement, *_):
        """Select the role before Alembic reads the installed revision."""
        if (
            connection.engine.url.database == database.engine.url.database
            and statement.lstrip().startswith("SELECT alembic_version.version_num")
            and not connection.info.get("test_documentation_owner_active")
        ):
            connection.exec_driver_sql(f'SET LOCAL ROLE "{owner}"')
            connection.info["test_documentation_owner_active"] = True

    async def migrate(operation, revision):
        """Use the normal Alembic entry point in the restricted role."""
        event.listen(Engine, "before_cursor_execute", assume_owner)
        try:
            await anyio.to_thread.run_sync(operation, alembic_config, revision)
        finally:
            event.remove(Engine, "before_cursor_execute", assume_owner)

    try:
        yield owner, migrate
    finally:
        async with database.engine.begin() as connection:
            await connection.exec_driver_sql(f'DROP OWNED BY "{owner}" CASCADE')
            await connection.exec_driver_sql(f'DROP ROLE "{owner}"')


@pytest.mark.parametrize("force_rls", [False, True])
async def test_documentation_migration_respects_its_owner_and_cannot_drop_hidden_rows(
    migration_database,
    alembic_config,
    plan_type,
    postgres_migration_role,
    force_rls,
):
    """Let the documentation owner migrate and preserve rows hidden by a policy."""
    database = migration_database
    owner, migrate = postgres_migration_role
    await database.close()
    await anyio.to_thread.run_sync(alembic.downgrade, alembic_config, "0022")
    if force_rls:
        async with database.engine.begin() as connection:
            await connection.exec_driver_sql(f'GRANT TRUNCATE ON tariff_plan_types TO "{owner}"')
    await migrate(alembic.upgrade, "0023")
    async with database.engine.begin() as connection:
        assert (
            await connection.scalar(
                text("""
            SELECT pg_catalog.pg_get_userbyid(relowner) FROM pg_catalog.pg_class
            WHERE oid = 'tariff_field_documentation'::regclass
        """)
            )
            == owner
        )
        await connection.execute(
            insert(TABLE).values(
                tariff_plan_type_id=plan_type.id,
                field_name="limit",
                language_code="en",
                comment="Kept",
            )
        )
        if force_rls:
            await connection.exec_driver_sql(f"ALTER TABLE {TABLE.name} ENABLE ROW LEVEL SECURITY")
            await connection.exec_driver_sql(f"ALTER TABLE {TABLE.name} FORCE ROW LEVEL SECURITY")
    if not force_rls:
        async with database.engine.begin() as connection:
            await connection.execute(
                delete(TariffPlanType.__table__).where(TariffPlanType.id == plan_type.id)
            )
            assert await connection.scalar(select(TABLE.c.id)) is None
    if force_rls:
        with pytest.raises(DBAPIError, match="affected by row-level security"):
            await migrate(alembic.downgrade, "0022")
        assert await documentation_revision(database) == ("0023", True)
        async with database.engine.begin() as connection:
            assert await connection.scalar(select(TABLE.c.comment)) == "Kept"
            await connection.exec_driver_sql(f"ALTER TABLE {TABLE.name} DISABLE ROW LEVEL SECURITY")
            await connection.execute(delete(TABLE))
    await migrate(alembic.downgrade, "0022")
    assert await documentation_revision(database) == ("0022", False)


@pytest.mark.parametrize("move_guard", [False, True])
async def test_documentation_migration_survives_a_change_of_table_owner(
    migration_database,
    alembic_config,
    postgres_migration_role,
    move_guard,
):
    """Allow the new owner to replace its table while retaining a separately owned routine."""
    database = migration_database
    await database.close()
    await anyio.to_thread.run_sync(alembic.downgrade, alembic_config, "0023")
    owner, migrate = postgres_migration_role
    routine = text(
        "SELECT tgfoid FROM pg_trigger WHERE tgrelid = 'tariff_field_documentation'::regclass AND tgname = 'serialize_billing_writes'"
    )
    async with database.engine.begin() as connection:
        old_routine = await connection.scalar(routine)
        if move_guard:
            await connection.execute(CreateSchema("private_tariff_routines"))
            await connection.exec_driver_sql(
                f"ALTER FUNCTION public.{documentation_schema.DOCUMENTATION_GUARD}()"
                " SET SCHEMA private_tariff_routines"
            )
        await connection.exec_driver_sql(f'ALTER TABLE {TABLE.name} OWNER TO "{owner}"')
    await migrate(alembic.downgrade, "0022")
    assert await documentation_revision(database) == ("0022", False)
    async with database.engine.begin() as connection:
        assert (
            await connection.scalar(
                text("SELECT oid FROM pg_catalog.pg_proc WHERE oid = :oid"), {"oid": old_routine}
            )
            == old_routine
        )
    await migrate(alembic.upgrade, "0023")
    async with database.engine.begin() as connection:
        assert await connection.scalar(routine) != old_routine
        assert await documentation_guard_is_active(connection)
    await migrate(alembic.downgrade, "0022")


@pytest.mark.parametrize("permission", ["schema_owner", "version_column"])
async def test_documentation_downgrade_preserves_existing_migration_role_permissions(
    postgres_migration_role, migration_database, alembic_config, permission
):
    """Downgrade with the ownership or column grants already sufficient for Alembic."""
    database = migration_database
    await database.close()
    await anyio.to_thread.run_sync(alembic.downgrade, alembic_config, "0023")
    owner, migrate = postgres_migration_role
    async with database.engine.begin() as connection:
        original_owner = await connection.scalar(
            text("SELECT pg_get_userbyid(nspowner) FROM pg_namespace WHERE nspname = 'public'")
        )
        if permission == "schema_owner":
            await connection.exec_driver_sql(f'ALTER SCHEMA public OWNER TO "{owner}"')
            await connection.exec_driver_sql(f'GRANT SELECT, TRUNCATE ON {TABLE.name} TO "{owner}"')
        else:
            await connection.exec_driver_sql(f'ALTER TABLE {TABLE.name} OWNER TO "{owner}"')
            await connection.exec_driver_sql(f'REVOKE ALL ON alembic_version FROM "{owner}"')
            await connection.exec_driver_sql(
                f'GRANT SELECT (version_num), UPDATE (version_num) ON alembic_version TO "{owner}"'
            )
    try:
        await migrate(alembic.downgrade, "0022")
        assert await documentation_revision(database) == ("0022", False)
    finally:
        if permission == "schema_owner":
            async with database.engine.begin() as connection:
                qualified = connection.dialect.identifier_preparer.quote_identifier(original_owner)
                await connection.exec_driver_sql(f"ALTER SCHEMA public OWNER TO {qualified}")


async def test_billing_downgrade_does_not_require_ownership_of_existing_fk_parents(
    migration_database,
    alembic_config,
    postgres_migration_role,
):
    """Roll back a catalogue created by a role that only references the platform owner tables."""
    database = migration_database
    owner, migrate = postgres_migration_role
    await database.close()
    await anyio.to_thread.run_sync(alembic.downgrade, alembic_config, "0019")
    async with database.engine.begin() as connection:
        await connection.exec_driver_sql(f'GRANT REFERENCES ON users, companies TO "{owner}"')
    await migrate(alembic.upgrade, "0020")
    async with database.engine.begin() as reader:
        await reader.exec_driver_sql("SELECT id FROM public.users")
        with pytest.raises(DBAPIError) as conflict:
            await migrate(alembic.downgrade, "0019")
        assert conflict.value.orig.sqlstate == "55P03"
        assert await reader.scalar(text("SELECT version_num FROM alembic_version")) == "0020"
    await migrate(alembic.downgrade, "0019")
    async with database.engine.begin() as connection:
        assert await connection.scalar(text("SELECT version_num FROM alembic_version")) == "0019"
        assert await connection.scalar(text("SELECT to_regclass('payment_accounts')")) is None
        assert await connection.scalar(text("SELECT to_regclass('users')")) is not None


@pytest.mark.parametrize("starting_revision", ["0020", "0021"])
async def test_early_billing_migrations_refuse_hidden_rows(
    migration_database,
    alembic_config,
    postgres_migration_role,
    starting_revision,
):
    """Reject destructive downgrade or incomplete backfill when row policies hide billing data."""
    database = migration_database
    owner, migrate = postgres_migration_role
    await database.close()
    await anyio.to_thread.run_sync(alembic.downgrade, alembic_config, starting_revision)
    tables = [
        "alembic_version",
        "coordination",
        "users",
        "companies",
        "tariff_plan_types",
        "tariff_plans",
        "user_tariff_plans",
        "payment_accounts",
        "payment_ledger_entries",
    ]
    if starting_revision == "0021":
        tables.append("payment_account_tariff_plans")
    affected = "tariff_plan_types" if starting_revision == "0020" else "payment_accounts"
    async with database.engine.begin() as connection:
        await connection.execute(
            insert(TariffPlanType.__table__).values(name="Preserved", field_types={})
        )
        for table in tables:
            await connection.exec_driver_sql(f'ALTER TABLE {table} OWNER TO "{owner}"')
        await connection.exec_driver_sql(f"ALTER TABLE {affected} ENABLE ROW LEVEL SECURITY")
        await connection.exec_driver_sql(f"ALTER TABLE {affected} FORCE ROW LEVEL SECURITY")
    try:
        with pytest.raises(DBAPIError, match="affected by row-level security"):
            if starting_revision == "0020":
                await migrate(alembic.downgrade, "0019")
            else:
                await migrate(alembic.upgrade, "0023")
        async with database.engine.begin() as connection:
            assert (
                await connection.scalar(text("SELECT version_num FROM alembic_version"))
                == starting_revision
            )
            assert await connection.scalar(select(TariffPlanType.name)) == "Preserved"
    finally:
        async with database.engine.begin() as connection:
            for table in tables:
                await connection.exec_driver_sql(
                    f"ALTER TABLE IF EXISTS {table} OWNER TO CURRENT_USER"
                )
            await connection.exec_driver_sql(
                f"ALTER TABLE IF EXISTS {affected} DISABLE ROW LEVEL SECURITY"
            )


@pytest.mark.parametrize("alembic_drop", [False, True])
async def test_dropping_new_documentation_keeps_the_archived_tables_guard(
    migration_database,
    alembic_config,
    plan_type,
    alembic_drop,
):
    """Keep the archived table's trigger function when its replacement is removed."""
    database = migration_database
    if database.engine.dialect.name != "postgresql":
        pytest.skip("PostgreSQL documentation uses a schema-local trigger function")
    archive = "archived_tariff_documentation"
    routine = text("""
        SELECT tgfoid FROM pg_catalog.pg_trigger
        WHERE tgrelid = pg_catalog.to_regclass(:table) AND tgname = 'serialize_billing_writes'
    """)
    async with database.engine.begin() as connection:
        saved = await connection.scalar(routine, {"table": TABLE.name})
        await connection.execute(
            insert(TABLE).values(
                tariff_plan_type_id=plan_type.id,
                field_name="limit",
                language_code="en",
                comment="Archived",
            )
        )
        await connection.exec_driver_sql(f"ALTER TABLE {TABLE.name} RENAME TO {archive}")
        await connection.exec_driver_sql(
            f"ALTER INDEX {TABLE.primary_key.name} RENAME TO archived_docs_key"
        )
        index = next(iter(TABLE.indexes))
        await connection.exec_driver_sql(f"ALTER INDEX {index.name} RENAME TO archived_docs_index")
        await connection.run_sync(TABLE.create)
        replacement = await connection.scalar(routine, {"table": TABLE.name})
        assert replacement != saved
        if not alembic_drop:
            await connection.run_sync(TABLE.drop)
    if alembic_drop:
        await database.close()
        await anyio.to_thread.run_sync(alembic.downgrade, alembic_config, "0022")
    async with database.engine.begin() as connection:
        assert await connection.scalar(text(f"SELECT to_regclass('{TABLE.name}')")) is None
        assert await connection.scalar(text(f"SELECT comment FROM {archive}")) == "Archived"
        assert await connection.scalar(routine, {"table": archive}) == saved
        assert (
            await connection.scalar(
                text("SELECT oid FROM pg_catalog.pg_proc WHERE oid = :oid"),
                {"oid": replacement},
            )
            is None
        )
        assert await documentation_guard_is_active(connection, archive)


async def test_dropping_relocated_documentation_removes_its_unused_guard(
    migration_database,
):
    """Follow the trigger's actual routine across schema changes and clean it up after DROP."""
    database = migration_database
    if database.engine.dialect.name != "postgresql":
        pytest.skip("PostgreSQL trigger routines can stay in another schema")
    async with database.engine.begin() as connection:
        oid = await connection.scalar(
            text("""
            SELECT tgfoid FROM pg_catalog.pg_trigger
            WHERE tgrelid = 'public.tariff_field_documentation'::regclass
                AND tgname = 'serialize_billing_writes'
        """)
        )
        await connection.execute(CreateSchema("relocated_docs"))
        await connection.exec_driver_sql(f"ALTER TABLE {TABLE.name} SET SCHEMA relocated_docs")
        assert await documentation_guard_is_active(connection, f"relocated_docs.{TABLE.name}")
        await connection.execution_options(schema_translate_map={None: "relocated_docs"})
        await connection.run_sync(TABLE.drop)
        assert (
            await connection.scalar(
                text("SELECT oid FROM pg_catalog.pg_proc WHERE oid = :oid"),
                {"oid": oid},
            )
            is None
        )


async def test_documentation_downgrade_preserves_a_concurrently_replaced_guard(
    migration_database, alembic_config
):
    """Protect a different routine that acquires the old trigger function's name."""
    database = migration_database
    if database.engine.dialect.name != "postgresql":
        pytest.skip("PostgreSQL routines can be replaced independently of their old table")
    guard = documentation_schema.DOCUMENTATION_GUARD
    routine = text("SELECT pg_catalog.to_regprocedure(:name)::oid")
    async with database.engine.begin() as setup:
        if not await setup.scalar(
            text("SELECT rolsuper FROM pg_roles WHERE rolname = current_user")
        ):
            pytest.skip("Creating the test-only DDL event trigger requires a PostgreSQL superuser")
        original = await setup.scalar(routine, {"name": f"public.{guard}()"})
        assert original is not None
        await setup.exec_driver_sql("""
            CREATE FUNCTION pause_documentation_drop() RETURNS event_trigger
            LANGUAGE plpgsql AS $gate$
            DECLARE old_timeout text := pg_catalog.current_setting('lock_timeout');
            BEGIN
                PERFORM pg_catalog.set_config('lock_timeout', '0', true);
                PERFORM pg_catalog.pg_advisory_xact_lock(52427, 2);
                PERFORM pg_catalog.set_config('lock_timeout', old_timeout, true);
            END;
            $gate$
        """)
        await setup.exec_driver_sql("""
            CREATE EVENT TRIGGER pause_documentation_drop ON ddl_command_end
            WHEN TAG IN ('DROP TABLE') EXECUTE FUNCTION pause_documentation_drop()
        """)
    downgrading = None
    try:
        async with database.engine.begin() as writer:
            holder = await writer.scalar(text("SELECT pg_catalog.pg_backend_pid()"))
            await writer.exec_driver_sql("SELECT pg_catalog.pg_advisory_xact_lock(52427, 2)")
            downgrading = asyncio.create_task(
                anyio.to_thread.run_sync(alembic.downgrade, alembic_config, "0022")
            )
            await wait_for_postgres_blocker(database, holder)
            await writer.exec_driver_sql(
                f"ALTER FUNCTION public.{guard}() RENAME TO older_documentation_guard"
            )
            await writer.exec_driver_sql(
                f"CREATE FUNCTION public.{guard}() RETURNS integer LANGUAGE sql AS 'SELECT 42'"
            )
            replacement = await writer.scalar(routine, {"name": f"public.{guard}()"})
    finally:
        if downgrading is not None:
            await asyncio.wait_for(downgrading, 10)
    assert await documentation_revision(database) == ("0022", False)
    async with database.engine.begin() as connection:
        assert (
            await connection.scalar(routine, {"name": "public.older_documentation_guard()"})
            == original
        )
        assert await connection.scalar(routine, {"name": f"public.{guard}()"}) == replacement
        assert await connection.scalar(text(f"SELECT public.{guard}()")) == 42


async def create_staged_billing_catalogue(database, alembic_config, revision):
    """Install an independent, migrated catalogue in the same PostgreSQL test database."""
    async with database.engine.begin() as connection:
        preparer = connection.dialect.identifier_preparer
        database_name = preparer.quote_identifier(database.engine.url.database)
        await connection.execute(CreateSchema("staged_docs"))
        await connection.exec_driver_sql(
            f"ALTER DATABASE {database_name} SET search_path TO staged_docs"
        )
    try:
        await anyio.to_thread.run_sync(alembic.upgrade, alembic_config, revision)
    finally:
        async with database.engine.begin() as connection:
            await connection.exec_driver_sql(f"ALTER DATABASE {database_name} RESET search_path")


@pytest.mark.parametrize(
    ("revision", "previous", "boundary"),
    [
        ("0020", "0019", "type_check"),
        ("0021", "0020", "tariff_check"),
        ("0023", "0022", "version_read"),
        ("0023", "0022", "before_documentation_lock"),
        ("0023", "0022", "documentation_check"),
        ("0023", "0022", "documentation_drop"),
    ],
)
async def test_billing_downgrade_holds_the_physical_schema_through_the_version_update(
    migration_database, alembic_config, revision, previous, boundary
):
    """Pin each revision to the catalogue whose version Alembic actually read."""
    database = migration_database
    if database.engine.dialect.name != "postgresql":
        pytest.skip("PostgreSQL supports transactional locks on schema names")
    await anyio.to_thread.run_sync(alembic.downgrade, alembic_config, revision)
    await create_staged_billing_catalogue(database, alembic_config, revision)
    populated_original = boundary == "before_documentation_lock"
    options = {} if populated_original else {"schema_translate_map": {None: "staged_docs"}}
    kind = str(uuid4())
    async with database.engine.begin() as connection:
        if revision in ("0020", "0021"):
            name = connection.dialect.identifier_preparer.quote_identifier(
                database.engine.url.database
            )
            await connection.exec_driver_sql("DROP TABLE staged_docs.alembic_version")
            await connection.exec_driver_sql(
                f"ALTER DATABASE {name} SET search_path TO public, staged_docs"
            )
        await connection.execute(
            insert(TariffPlanType).values(id=kind, name="Protected type", field_types={}),
            execution_options=options,
        )
        if revision == "0021":
            await connection.execute(
                insert(TariffPlan).values(
                    tariff_plan_type_id=kind,
                    name="Protected publication",
                    field_values={},
                    created_at=datetime(2026, 1, 1, tzinfo=UTC),
                    published_at=datetime(2026, 2, 1, tzinfo=UTC),
                ),
                execution_options=options,
            )
        if revision == "0023":
            await connection.execute(
                insert(TABLE).values(
                    tariff_plan_type_id=kind,
                    field_name="limit",
                    language_code="en",
                    comment="Protected documentation",
                ),
                execution_options=options,
            )

    probes = []

    async def attempt_swap():
        """Try to change the catalogue name in a separate PostgreSQL transaction."""
        try:
            async with database.engine.begin() as connection:
                await connection.exec_driver_sql("SET LOCAL lock_timeout = '200ms'")
                await connection.exec_driver_sql("ALTER SCHEMA public RENAME TO previous_public")
                await connection.exec_driver_sql("ALTER SCHEMA staged_docs RENAME TO public")
        except DBAPIError as error:
            probes.append(error.orig.sqlstate)
        else:
            probes.append("renamed")

    def at_boundary(connection, _cursor, statement, *_):
        """Run the competing rename at a boundary where Alembic uses the selected schema."""
        if connection.engine.url.database != database.engine.url.database or probes:
            return
        sql = statement.lstrip()
        matched = {
            "type_check": sql.startswith("SELECT 1") and "FROM public.tariff_plan_types" in sql,
            "tariff_check": sql.startswith("SELECT min(public.tariff_plans.published_at)"),
            "version_read": sql.startswith("SELECT alembic_version.version_num"),
            "before_documentation_lock": sql.startswith(f"LOCK TABLE public.{TABLE.name}"),
            "documentation_check": sql.startswith("SELECT 1")
            and f"FROM public.{TABLE.name}" in sql,
            "documentation_drop": sql.startswith("DO ") and "DROP TABLE" in sql,
        }
        if matched[boundary]:
            anyio.from_thread.run(attempt_swap)

    hook = "before_cursor_execute" if populated_original else "after_cursor_execute"
    event.listen(Engine, hook, at_boundary)
    try:
        if populated_original:
            with pytest.raises(
                CommandError, match="Cannot downgrade while tariff field documentation exists"
            ):
                await anyio.to_thread.run_sync(alembic.downgrade, alembic_config, previous)
        else:
            await anyio.to_thread.run_sync(alembic.downgrade, alembic_config, previous)
    finally:
        event.remove(Engine, hook, at_boundary)

    assert probes == ["55P03"]
    async with database.engine.begin() as connection:
        installed = await connection.scalar(text("SELECT version_num FROM public.alembic_version"))
        assert installed == (revision if populated_original else previous)
        assert (
            await connection.scalar(select(TariffPlanType.name), execution_options=options)
            == "Protected type"
        )
        if revision == "0021":
            assert await connection.scalar(
                select(TariffPlan.published_at), execution_options=options
            ) == (datetime(2026, 2, 1, tzinfo=UTC))
        if revision == "0023":
            assert (
                await connection.scalar(select(TABLE.c.comment), execution_options=options)
                == "Protected documentation"
            )
        await connection.exec_driver_sql("SET LOCAL lock_timeout = '1s'")
        await connection.exec_driver_sql("ALTER SCHEMA public RENAME TO finished_catalogue")
        await connection.exec_driver_sql("ALTER SCHEMA finished_catalogue RENAME TO public")


async def test_documentation_upgrade_and_downgrade_preserve_catalogue(
    migration_database, alembic_config, plan_type
):
    """Keep the existing catalogue across upgrade and reject removing a populated table."""
    plan = TariffPlan(name="Personal storage", plan_type=plan_type, field_values={"limit": 20})
    async with migration_database.write() as session:
        session.add(plan)
    await migration_database.close()
    await anyio.to_thread.run_sync(alembic.downgrade, alembic_config, "0022")
    assert await documentation_revision(migration_database) == ("0022", False)
    async with migration_database.write() as session:
        unused = TariffPlanType(name="Unused", field_types={})
        session.add(unused)
        await session.flush()
        await session.delete(unused)
        await session.flush()
    await migration_database.close()
    await anyio.to_thread.run_sync(alembic.upgrade, alembic_config, "head")
    await anyio.to_thread.run_sync(alembic.check, alembic_config)
    await anyio.to_thread.run_sync(alembic.downgrade, alembic_config, "0023")
    assert await documentation_revision(migration_database) == ("0023", True)

    doc = TariffFieldDocumentation(
        tariff_plan_type_id=plan_type.id,
        field_name="limit",
        language_code="en",
        comment="GiB",
    )
    async with migration_database.write() as session:
        session.add(doc)
    await migration_database.close()
    with pytest.raises(CommandError, match="tariff field documentation exists"):
        await anyio.to_thread.run_sync(alembic.downgrade, alembic_config, "0022")
    assert await documentation_revision(migration_database) == ("0023", True)
    async with migration_database.write() as session:
        assert (await session.get(TariffPlan, plan.id)).field_values == {"limit": 20}
        saved = await session.get(TariffFieldDocumentation, doc.id)
        assert saved.comment == "GiB"
        await session.delete(saved)
    await migration_database.close()
    await anyio.to_thread.run_sync(alembic.downgrade, alembic_config, "0022")
    assert await documentation_revision(migration_database) == ("0022", False)


@pytest.mark.parametrize("stage", ["table", "index", "version"])
async def test_interrupted_documentation_upgrade_can_retry(
    migration_database, alembic_config, plan_type, stage
):
    """Roll back the new table, index and Alembic version in the same transaction."""
    await migration_database.close()
    await anyio.to_thread.run_sync(alembic.downgrade, alembic_config, "0022")

    def interrupt(_connection, _cursor, statement, _parameters, context, _many):
        """Fail after a real DDL statement or revision update has completed."""
        ddl = getattr(context.compiled, "statement", None)
        interrupted = {
            "table": isinstance(ddl, CreateTable) and ddl.element.name == TABLE.name,
            "index": isinstance(ddl, CreateIndex) and ddl.element.table.name == TABLE.name,
            "version": statement.lstrip().startswith("UPDATE alembic_version"),
        }
        if interrupted[stage]:
            raise RuntimeError("interrupted documentation upgrade")

    event.listen(Engine, "after_cursor_execute", interrupt)
    try:
        with pytest.raises(RuntimeError, match="interrupted documentation upgrade"):
            await anyio.to_thread.run_sync(alembic.upgrade, alembic_config, "0023")
    finally:
        event.remove(Engine, "after_cursor_execute", interrupt)
    assert await documentation_revision(migration_database) == ("0022", False)
    await migration_database.close()
    await anyio.to_thread.run_sync(alembic.upgrade, alembic_config, "0023")
    assert await documentation_revision(migration_database) == ("0023", True)
    async with migration_database.read() as session:
        assert (await session.get(TariffPlanType, plan_type.id)).field_types == {"limit": "int"}


async def test_downgrade_locks_documentation_before_checking_that_it_is_empty(
    migration_database, alembic_config, plan_type
):
    """Prevent a concurrent insert between the emptiness check and dropping the table."""
    blocked = []
    async with migration_database.engine.connect() as writer:
        if writer.dialect.name == "sqlite":
            await writer.exec_driver_sql("PRAGMA busy_timeout = 50")
        else:
            await writer.exec_driver_sql("SET lock_timeout = '50ms'")
        await writer.commit()

        async def insert_during_downgrade():
            """Try to commit documentation through an independent write transaction."""
            try:
                async with writer.begin():
                    await writer.execute(
                        insert(TABLE).values(
                            tariff_plan_type_id=plan_type.id,
                            field_name="limit",
                            language_code="en",
                            comment="GiB",
                        )
                    )
            except DBAPIError as error:
                blocked.append(str(error.orig))

        def before_drop(_connection, _cursor, statement, *_):
            """Attempt the insert after Alembic has observed the empty table."""
            if "DROP TABLE" in statement and TABLE.name in statement:
                anyio.from_thread.run(insert_during_downgrade)

        event.listen(Engine, "before_cursor_execute", before_drop)
        try:
            await anyio.to_thread.run_sync(alembic.downgrade, alembic_config, "0022")
        finally:
            event.remove(Engine, "before_cursor_execute", before_drop)
    assert len(blocked) == 1 and any(
        message in blocked[0] for message in ("database is locked", "lock timeout")
    )
    assert await documentation_revision(migration_database) == ("0022", False)


async def test_postgres_documentation_migrates_beside_the_tariff_catalogue(
    migration_database, alembic_config, plan_type
):
    """Follow the installed revision even when another schema shadows the table names."""
    if migration_database.engine.dialect.name != "postgresql":
        pytest.skip("PostgreSQL can resolve tables from several schemas")
    await migration_database.close()
    await anyio.to_thread.run_sync(alembic.downgrade, alembic_config, "0022")
    async with migration_database.engine.begin() as connection:
        await connection.exec_driver_sql('CREATE SCHEMA "Shadow catalogue"')
        for table in ("tariff_plan_types", TABLE.name):
            await connection.exec_driver_sql(
                f'CREATE TABLE "Shadow catalogue".{table} (id uuid PRIMARY KEY)'
            )

    def use_shadow_first(connection, _cursor, statement, *_):
        """Make unqualified names prefer the other schema for both migration directions."""
        if statement.startswith("SELECT alembic_version.version_num"):
            connection.exec_driver_sql('SET LOCAL search_path TO "Shadow catalogue", public')

    event.listen(Engine, "before_cursor_execute", use_shadow_first)
    try:
        await anyio.to_thread.run_sync(alembic.upgrade, alembic_config, "0023")
        async with migration_database.engine.begin() as connection:
            assert await connection.scalar(
                text("SELECT to_regclass('public.tariff_field_documentation')")
            )
            assert (
                await connection.scalar(
                    text("SELECT to_regclass('pg_catalog.tariff_field_documentation')")
                )
                is None
            )
            row = await connection.execute(
                insert(TABLE)
                .values(
                    tariff_plan_type_id=plan_type.id,
                    field_name="limit",
                    language_code="en",
                    comment="GiB",
                )
                .returning(TABLE.c.id)
            )
            await connection.execute(delete(TABLE).where(TABLE.c.id == row.scalar_one()))
        await migration_database.close()
        await anyio.to_thread.run_sync(alembic.downgrade, alembic_config, "0022")
    finally:
        event.remove(Engine, "before_cursor_execute", use_shadow_first)
    async with migration_database.engine.connect() as connection:
        assert (
            await connection.scalar(text("SELECT to_regclass('public.tariff_field_documentation')"))
            is None
        )
        assert await connection.scalar(
            text("SELECT to_regclass('\"Shadow catalogue\".tariff_field_documentation')")
        )


async def test_postgres_documentation_upgrade_supports_latin1():
    """Install docs in an existing non-UTF-8 database and still reject a blank locale."""
    admin_url = os.environ.get("THRONG_TEST_POSTGRES_ADMIN_URL")
    if not admin_url:
        pytest.skip("Requires a disposable PostgreSQL server")
    name = "throng_test_" + uuid4().hex
    url = make_url(admin_url).set(drivername="postgresql+asyncpg", database=name)
    settings = Settings(database_url=url.render_as_string(hide_password=False), _env_file=None)
    config = migration_config(settings)
    admin = await asyncpg.connect(admin_url)
    database = Database(settings.database_url)
    try:
        await admin.execute(
            f"""CREATE DATABASE "{name}" TEMPLATE template0 ENCODING 'LATIN1' LOCALE 'C'"""
        )
        await anyio.to_thread.run_sync(alembic.upgrade, config, "0022")
        await anyio.to_thread.run_sync(alembic.upgrade, config, "0023")

        kind = TariffPlanType(name="Stockage", field_types={"Capacité": "int"})
        document = TariffFieldDocumentation(
            plan_type=kind,
            field_name="Capacité",
            language_code="fr-CA",
            comment="Limite en unités.",
        )
        async with database.write() as session:
            session.add(document)
        async with database.read() as session:
            saved = await session.get(TariffFieldDocumentation, document.id)
            assert saved.field_name == "Capacité"
            assert saved.language_code == "fr-CA"
            assert saved.comment == "Limite en unités."
        with pytest.raises(IntegrityError, match="language_code_nonblank"):
            async with database.engine.begin() as connection:
                await connection.execute(
                    insert(TABLE).values(
                        tariff_plan_type_id=kind.id,
                        field_name="Capacité",
                        language_code="\u00a0",
                        comment="",
                    )
                )
    finally:
        await database.close()
        await admin.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')
        await admin.close()


async def test_postgres_documentation_upgrade_in_a_schema_containing_a_dollar_delimiter(
    migration_database, alembic_config
):
    """Install and use the tariff guard when the catalogue's schema contains a PL/pgSQL delimiter."""
    database = migration_database
    if database.engine.dialect.name != "postgresql":
        pytest.skip("PostgreSQL supports schema-local tariff catalogues")
    async with database.engine.begin() as connection:
        await connection.execute(CreateSchema("catalogue$body$"))
        name = connection.dialect.identifier_preparer.quote_identifier(database.engine.url.database)
        await connection.exec_driver_sql(
            f'ALTER DATABASE {name} SET search_path TO "catalogue$body$"'
        )
    await database.close()
    await anyio.to_thread.run_sync(alembic.upgrade, alembic_config, "0022")
    assert await documentation_revision(database) == ("0022", False)
    await database.close()
    await anyio.to_thread.run_sync(alembic.upgrade, alembic_config, "0023")
    assert await documentation_revision(database) == ("0023", True)
    async with database.engine.begin() as connection:
        assert await documentation_guard_is_active(connection)


@pytest.mark.parametrize("starting_revision", ["0019", "0020"])
async def test_all_billing_revisions_follow_the_revision_schema_with_another_schema_first(
    migration_database, alembic_config, starting_revision
):
    """Keep billing tables and backfill scratch data in their intended PostgreSQL namespaces."""
    database = migration_database
    if database.engine.dialect.name != "postgresql":
        pytest.skip("PostgreSQL resolves unqualified migration objects through search_path")
    await database.close()
    await anyio.to_thread.run_sync(alembic.downgrade, alembic_config, starting_revision)
    async with database.engine.begin() as connection:
        await connection.execute(CreateSchema("before_billing"))
        await connection.exec_driver_sql(
            "CREATE TABLE public._account_tariff_cutoffs (marker integer)"
        )
        await connection.exec_driver_sql("INSERT INTO public._account_tariff_cutoffs VALUES (42)")
        name = connection.dialect.identifier_preparer.quote_identifier(database.engine.url.database)
        await connection.exec_driver_sql(
            f"ALTER DATABASE {name} SET search_path TO before_billing, public"
        )
    await database.close()
    await anyio.to_thread.run_sync(alembic.upgrade, alembic_config, "0023")
    async with database.engine.begin() as connection:
        assert (
            await connection.scalar(text("SELECT version_num FROM public.alembic_version"))
            == "0023"
        )
        assert await documentation_guard_is_active(connection)
        assert (
            await connection.scalar(text("SELECT marker FROM public._account_tariff_cutoffs")) == 42
        )
        for relation in (
            TariffPlanType.__tablename__,
            PaymentAccountTariffPlan.__tablename__,
            TABLE.name,
        ):
            assert (
                await connection.scalar(
                    text("SELECT pg_catalog.to_regclass(:relation)"),
                    {"relation": f"before_billing.{relation}"},
                )
                is None
            )
    await database.close()
    await anyio.to_thread.run_sync(alembic.downgrade, alembic_config, starting_revision)
    async with database.engine.begin() as connection:
        assert (
            await connection.scalar(text("SELECT version_num FROM public.alembic_version"))
            == starting_revision
        )
        assert (
            await connection.scalar(text("SELECT marker FROM public._account_tariff_cutoffs")) == 42
        )


@pytest.mark.parametrize(
    ("start", "revision"),
    [
        ("0019", "0020"),
        ("0020", "0019"),
        ("0020", "0021"),
        ("0021", "0020"),
        ("0022", "0021"),
        ("0019", "0023"),
    ],
)
async def test_billing_ddl_stays_with_the_revision_schema_after_a_server_hook(
    migration_database, alembic_config, start, revision
):
    """Keep billing DDL and backfill in the installed revision schema after a path change."""
    database = migration_database
    if database.engine.dialect.name != "postgresql":
        pytest.skip("PostgreSQL supports server DDL event triggers")
    await database.close()
    await anyio.to_thread.run_sync(alembic.downgrade, alembic_config, start)
    async with database.engine.begin() as connection:
        if not await connection.scalar(
            text("SELECT rolsuper FROM pg_roles WHERE rolname = current_user")
        ):
            pytest.skip("PostgreSQL can install event triggers only as a superuser")
        await connection.execute(CreateSchema("billing_shadow"))
        await connection.exec_driver_sql(
            "CREATE TABLE billing_shadow.payment_accounts (marker integer)"
        )
        await connection.exec_driver_sql("INSERT INTO billing_shadow.payment_accounts VALUES (42)")
        await connection.exec_driver_sql("""
            CREATE TABLE billing_shadow.tariff_plans (marker integer, published_at timestamptz)
        """)
        await connection.exec_driver_sql("""
            INSERT INTO billing_shadow.tariff_plans VALUES (42, '2026-01-01 00:00:00+00')
        """)
        await connection.exec_driver_sql(f"""
            CREATE FUNCTION billing_shadow.{TRIGGER_NAME}() RETURNS trigger LANGUAGE plpgsql
            AS $body$ BEGIN RETURN NEW; END; $body$
        """)
        await connection.exec_driver_sql("""
            CREATE FUNCTION public.shift_billing_path() RETURNS event_trigger LANGUAGE plpgsql
            AS $body$
            BEGIN
                PERFORM pg_catalog.set_config('search_path', 'billing_shadow, public', true);
            END;
            $body$
        """)
        await connection.exec_driver_sql("""
            CREATE EVENT TRIGGER shift_billing_path ON ddl_command_end
            WHEN TAG IN ('CREATE TABLE', 'DROP TABLE', 'ALTER TABLE', 'DROP FUNCTION')
            EXECUTE FUNCTION public.shift_billing_path()
        """)
    await database.close()
    command = alembic.upgrade if start < revision else alembic.downgrade
    await anyio.to_thread.run_sync(command, alembic_config, revision)
    async with database.engine.begin() as connection:
        assert (
            await connection.scalar(text("SELECT version_num FROM public.alembic_version"))
            == revision
        )
        assert (
            await connection.scalar(text("SELECT marker FROM billing_shadow.payment_accounts"))
            == 42
        )
        for relation in (
            "tariff_plan_types",
            "tariff_plans",
            "user_tariff_plans",
            "payment_accounts",
            "payment_ledger_entries",
        ):
            table = await connection.scalar(
                text("SELECT to_regclass(:relation)"), {"relation": f"public.{relation}"}
            )
            assert (table is not None) is (revision >= "0020")
        assert await connection.scalar(text("SELECT marker FROM billing_shadow.tariff_plans")) == 42
        assert await connection.scalar(
            text("SELECT published_at IS NOT NULL FROM billing_shadow.tariff_plans")
        )
        public_timestamp = await connection.scalar(
            text("""
                SELECT 1 FROM pg_catalog.pg_attribute
                WHERE attrelid = pg_catalog.to_regclass('public.tariff_plans')
                    AND attname = 'published_at' AND NOT attisdropped
            """)
        )
        assert bool(public_timestamp) is (revision >= "0021")
        for namespace, expected in (("public", revision >= "0021"), ("billing_shadow", False)):
            installed = await connection.scalar(
                text("""
                    SELECT 1 FROM pg_catalog.pg_trigger
                    WHERE tgrelid = pg_catalog.to_regclass(:table) AND tgname = :trigger
                """),
                {"table": f"{namespace}.payment_accounts", "trigger": TRIGGER_NAME},
            )
            assert bool(installed) is expected


def test_postgres_billing_migration_ddl_embeds_the_selected_schema():
    """Use a resolved schema name even when it contains SQL dollar delimiters and quotes."""
    schema = "Billing's $definition$ schema"
    statement = postgres_catalog_ddl("SELECT 1 FROM %1$I.tariff_plans", schema=schema)
    assert f"pg_catalog.decode('{schema.encode().hex()}', 'hex')" in statement
    assert schema not in statement
    assert "SELECT 1 FROM %1$I.tariff_plans" in statement


@pytest.mark.parametrize("namesakes", [False, True])
async def test_billing_upgrade_keeps_references_to_legacy_owner_schemas(
    migration_database, alembic_config, namesakes
):
    """Upgrade and roll back the catalogue and organization fields across owner schemas."""
    database = migration_database
    if database.engine.dialect.name != "postgresql":
        pytest.skip("PostgreSQL allows references across schemas")
    await database.close()
    await anyio.to_thread.run_sync(alembic.downgrade, alembic_config, "0019")
    owner_id = str(uuid4())
    async with database.engine.begin() as connection:
        await connection.execute(
            text(
                "INSERT INTO users (id, created_at, email, password_hash, first_name, last_name) "
                "VALUES (:id, CURRENT_TIMESTAMP, 'legacy-owner@example.test', 'unused', 'Legacy', 'Owner')"
            ),
            {"id": owner_id},
        )
        await connection.execute(CreateSchema("legacy_identity"))
        await connection.execute(CreateSchema("Legacy, companies"))
        await connection.exec_driver_sql("ALTER TABLE public.users SET SCHEMA legacy_identity")
        await connection.exec_driver_sql(
            'ALTER TABLE public.companies SET SCHEMA "Legacy, companies"'
        )
        if namesakes:
            await connection.exec_driver_sql("CREATE TABLE public.users (id uuid PRIMARY KEY)")
            await connection.exec_driver_sql("CREATE TABLE public.companies (id uuid PRIMARY KEY)")
        path = 'legacy_identity, "Legacy, companies"'
        path = f"{path}, public" if namesakes else f"public, {path}"
        name = connection.dialect.identifier_preparer.quote_identifier(database.engine.url.database)
        await connection.exec_driver_sql(f"ALTER DATABASE {name} SET search_path TO {path}")
    await database.close()
    await anyio.to_thread.run_sync(alembic.upgrade, alembic_config, "head")
    async with database.engine.begin() as connection:
        assert await documentation_guard_is_active(connection)
        for table, expected in (
            ("user_tariff_plans", {"users": "legacy_identity"}),
            ("payment_accounts", {"users": "legacy_identity", "companies": "Legacy, companies"}),
            ("computers", {"users": "legacy_identity"}),
            ("agent_credentials", {"users": "legacy_identity"}),
        ):
            keys = await connection.run_sync(
                lambda sync, table=table: inspect(sync).get_foreign_keys(
                    table, schema="public", postgresql_ignore_search_path=True
                )
            )
            matched = [key for key in keys if key["referred_table"] in expected]
            assert {key["referred_table"] for key in matched} == expected.keys()
            assert all(key["referred_schema"] == expected[key["referred_table"]] for key in matched)
            if table == "computers":
                columns = {column for key in matched for column in key["constrained_columns"]}
                assert {"connected_user_id", "legacy_transfer_user_id"} <= columns
            if table == "agent_credentials":
                assert any(
                    key["constrained_columns"] == ["pending_connected_user_id"] for key in matched
                )
        assert (
            await connection.scalar(text("SELECT name_search FROM legacy_identity.users"))
            == "legacy owner"
        )
        columns = await connection.run_sync(
            lambda sync: inspect(sync).get_columns("companies", schema="Legacy, companies")
        )
        assert "avatar_version" in {column["name"] for column in columns}
        if namesakes:
            for table in ("users", "companies"):
                shadow = await connection.run_sync(
                    lambda sync, table=table: inspect(sync).get_columns(table, schema="public")
                )
                assert {column["name"] for column in shadow} == {"id"}
        async with AsyncSession(bind=connection) as session:
            assert await session.get(User, owner_id) is not None
            account = PaymentAccount(user_id=owner_id)
            session.add(account)
            await session.flush()
            assert await session.get(PaymentAccount, account.id) is account
            await session.delete(account)
            await session.flush()
    await database.close()
    await anyio.to_thread.run_sync(alembic.downgrade, alembic_config, "0019")
    async with database.engine.begin() as connection:
        assert (
            await connection.scalar(text("SELECT version_num FROM public.alembic_version"))
            == "0019"
        )
        assert await connection.scalar(text("SELECT to_regclass('legacy_identity.users')"))
        assert await connection.scalar(
            text("""SELECT to_regclass('"Legacy, companies".companies')""")
        )


@pytest.mark.parametrize("via_migration", [False, True])
async def test_documentation_creation_stays_in_its_schema_when_a_server_ddl_hook_changes_the_path(
    migration_database, alembic_config, via_migration
):
    """Keep the index and guard on the new table when a PostgreSQL DDL event changes lookup."""
    database = migration_database
    if database.engine.dialect.name != "postgresql":
        pytest.skip("PostgreSQL supports server DDL event triggers")
    await database.close()
    await anyio.to_thread.run_sync(alembic.downgrade, alembic_config, "0022")
    async with database.engine.begin() as connection:
        if not await connection.scalar(
            text("SELECT rolsuper FROM pg_roles WHERE rolname = current_user")
        ):
            pytest.skip("PostgreSQL can install event triggers only as a superuser")
        await connection.execute(CreateSchema("docs_shadow"))
        await connection.exec_driver_sql("""
            CREATE TABLE docs_shadow.tariff_field_documentation (
                id uuid PRIMARY KEY,
                tariff_plan_type_id uuid REFERENCES public.tariff_plan_types(id) ON DELETE CASCADE
            )
        """)
        await connection.exec_driver_sql("""
            CREATE FUNCTION public.shift_documentation_path() RETURNS event_trigger
            LANGUAGE plpgsql AS $body$
            BEGIN
                IF EXISTS (
                    SELECT 1 FROM pg_catalog.pg_event_trigger_ddl_commands()
                    WHERE object_type = 'table'
                        AND object_identity = 'public.tariff_field_documentation'
                ) THEN
                    PERFORM pg_catalog.set_config('search_path', 'docs_shadow, public', true);
                END IF;
            END;
            $body$
        """)
        await connection.exec_driver_sql("""
            CREATE EVENT TRIGGER shift_documentation_path ON ddl_command_end
            WHEN TAG IN ('CREATE TABLE') EXECUTE FUNCTION public.shift_documentation_path()
        """)
    if via_migration:
        await database.close()
        await anyio.to_thread.run_sync(alembic.upgrade, alembic_config, "0023")
        assert await documentation_revision(database) == ("0023", True)
    else:
        async with database.engine.begin() as connection:
            await connection.run_sync(TABLE.create)
    async with database.engine.begin() as connection:
        assert await documentation_guard_is_active(connection, table=f"public.{TABLE.name}")
        for schema, expected in (
            ("public", [f"ix_{TABLE.name}_tariff_plan_type_id"]),
            ("docs_shadow", []),
        ):
            indexes = await connection.run_sync(
                lambda sync, schema=schema: inspect(sync).get_indexes(TABLE.name, schema=schema)
            )
            assert [index["name"] for index in indexes] == expected
        assert not await connection.scalar(
            text("""
                SELECT 1 FROM pg_catalog.pg_trigger
                WHERE tgrelid = 'docs_shadow.tariff_field_documentation'::regclass AND NOT tgisinternal
            """)
        )


@pytest.mark.parametrize("automatic_indexes", [False, True])
async def test_documentation_uses_the_created_relation_when_server_hooks_change_both_paths(
    migration_database, alembic_config, automatic_indexes
):
    """Identify the new relation even if the path before and after CREATE points elsewhere."""
    database = migration_database
    if database.engine.dialect.name != "postgresql":
        pytest.skip("PostgreSQL supports server DDL event triggers")
    await database.close()
    await anyio.to_thread.run_sync(alembic.downgrade, alembic_config, "0022")
    async with database.engine.begin() as connection:
        if not await connection.scalar(
            text("SELECT rolsuper FROM pg_roles WHERE rolname = current_user")
        ):
            pytest.skip("PostgreSQL can install event triggers only as a superuser")
        await connection.execute(CreateSchema("created_docs"))
        await connection.exec_driver_sql("""
            CREATE TABLE public.tariff_field_documentation (
                id uuid PRIMARY KEY,
                tariff_plan_type_id uuid REFERENCES public.tariff_plan_types(id) ON DELETE CASCADE
            )
        """)
        for phase, path in (("start", "created_docs, public"), ("end", "public")):
            await connection.exec_driver_sql(f"""
                CREATE FUNCTION public.shift_path_{phase}() RETURNS event_trigger
                LANGUAGE plpgsql AS $body$
                BEGIN
                    PERFORM pg_catalog.set_config('search_path', '{path}', true);
                END;
                $body$
            """)
            await connection.exec_driver_sql(f"""
                CREATE EVENT TRIGGER shift_path_{phase} ON ddl_command_{phase}
                WHEN TAG IN ('CREATE TABLE') EXECUTE FUNCTION public.shift_path_{phase}()
            """)
        if automatic_indexes:
            await connection.run_sync(TABLE.create)
        else:
            await connection.execute(CreateTable(TABLE))
        assert await connection.scalar(text("SHOW search_path")) == "public"
        assert await documentation_guard_is_active(connection, table=f"created_docs.{TABLE.name}")
        for schema, guards, indexes in (
            ("public", 0, 0),
            ("created_docs", 2, int(automatic_indexes)),
        ):
            assert (
                await connection.scalar(
                    text("""
                    SELECT count(*) FROM pg_catalog.pg_trigger
                    WHERE tgrelid = pg_catalog.to_regclass(:table) AND NOT tgisinternal
                """),
                    {"table": f"{schema}.{TABLE.name}"},
                )
                == guards
            )
            names = await connection.run_sync(
                lambda sync, schema=schema: inspect(sync).get_indexes(TABLE.name, schema=schema)
            )
            assert len(names) == indexes


@pytest.mark.parametrize("occupied", [False, True])
async def test_documentation_downgrade_checks_the_locked_schema_after_a_path_change(
    migration_database, plan_type, alembic_config, migration_head, occupied
):
    """Use the locked relation for both the emptiness check and the actual drop."""
    database = migration_database
    if database.engine.dialect.name != "postgresql":
        pytest.skip("PostgreSQL can move name lookup while a migration has locked its table")
    async with database.engine.begin() as connection:
        await connection.execute(CreateSchema("docs_shadow"))
        await connection.exec_driver_sql(
            f"CREATE TABLE docs_shadow.{TABLE.name} (id integer PRIMARY KEY)"
        )
        if occupied:
            await connection.execute(
                insert(TABLE).values(
                    tariff_plan_type_id=plan_type.id,
                    field_name="limit",
                    language_code="en",
                    comment="Keep",
                )
            )
        else:
            await connection.exec_driver_sql(f"INSERT INTO docs_shadow.{TABLE.name} VALUES (1)")
    await database.close()

    def shift_after_lock(connection, _cursor, statement, *_):
        """Move unqualified lookup to a namesake after the migration locks its own table."""
        if connection.engine.url.database == database.engine.url.database and statement.startswith(
            f"LOCK TABLE public.{TABLE.name} IN ACCESS EXCLUSIVE MODE"
        ):
            connection.exec_driver_sql("SET LOCAL search_path TO docs_shadow, public")

    event.listen(Engine, "after_cursor_execute", shift_after_lock)
    try:
        if occupied:
            with pytest.raises(CommandError, match="tariff field documentation exists"):
                await anyio.to_thread.run_sync(alembic.downgrade, alembic_config, "0022")
        else:
            await anyio.to_thread.run_sync(alembic.downgrade, alembic_config, "0022")
    finally:
        event.remove(Engine, "after_cursor_execute", shift_after_lock)

    assert await documentation_revision(database) == (
        migration_head if occupied else "0022",
        occupied,
    )
    async with database.engine.begin() as connection:
        assert await connection.scalar(
            text(f"SELECT count(*) FROM docs_shadow.{TABLE.name}")
        ) == int(not occupied)
        if occupied:
            assert await connection.scalar(select(TABLE.c.comment)) == "Keep"
    if not occupied:
        await database.close()
        await anyio.to_thread.run_sync(alembic.upgrade, alembic_config, "0023")
        assert await documentation_revision(database) == ("0023", True)


def test_renamed_documentation_table_uses_standard_postgres_ddl():
    """Leave an explicitly renamed metadata copy independent of the mapped documentation guards."""
    metadata = MetaData()
    TariffPlanType.__table__.to_metadata(metadata)
    renamed = TABLE.to_metadata(metadata, name="another_tariff_documentation")
    dialect = postgresql.dialect()
    assert (
        str(CreateTable(renamed).compile(dialect=dialect))
        .lstrip()
        .startswith("CREATE TABLE another_tariff_documentation")
    )
    assert (
        str(DropTable(renamed).compile(dialect=dialect))
        .lstrip()
        .startswith("DROP TABLE another_tariff_documentation")
    )


async def test_documentation_metadata_copies_preserve_only_the_target_dialect_checks(
    migration_database, plan_type
):
    """Create each metadata copy and enforce portable field validation on both backends."""
    copied = TABLE
    async with migration_database.engine.begin() as connection:
        # SQLAlchemy copies index names unchanged, so create each version in turn.
        await connection.run_sync(TABLE.drop)
        for name in ("copied_documentation", "copied_again_documentation"):
            metadata = MetaData()
            TariffPlanType.__table__.to_metadata(metadata)
            copied = copied.to_metadata(metadata, name=name)
            await connection.run_sync(copied.create)
            values = {
                "tariff_plan_type_id": plan_type.id,
                "field_name": "limit",
                "comment": "The inherited check should accept this comment.",
            }
            await connection.execute(insert(copied).values(**values, language_code="en"))
            with pytest.raises(IntegrityError, match="language_code_nonblank"):
                async with connection.begin_nested():
                    await connection.execute(
                        insert(copied).values(**values, language_code="\u00a0")
                    )
            assert await connection.scalar(select(copied.c.language_code)) == "en"
            await connection.run_sync(copied.drop)


@pytest.mark.parametrize("use_metadata", [False, True])
async def test_same_name_documentation_metadata_copy_creates_its_index_in_the_permanent_schema(
    migration_database, use_metadata
):
    """Keep inherited schema routing on a metadata copy beside a temporary namesake."""
    metadata = MetaData()
    TariffPlanType.__table__.to_metadata(metadata)
    copied = TABLE.to_metadata(metadata)
    async with migration_database.engine.begin() as connection:
        postgres = connection.dialect.name == "postgresql"
        schema = "public" if postgres else "main"
        await connection.run_sync(TABLE.drop)
        await connection.exec_driver_sql(f"CREATE TEMPORARY TABLE {TABLE.name} (id integer)")
        if use_metadata:
            await connection.run_sync(
                lambda sync: metadata.create_all(sync, tables=[copied], checkfirst=False)
            )
        else:
            await connection.run_sync(copied.create)
        indexes = await connection.run_sync(
            lambda sync: inspect(sync).get_indexes(TABLE.name, schema=schema)
        )
        assert [index["name"] for index in indexes] == [f"ix_{TABLE.name}_tariff_plan_type_id"]
        if postgres:
            assert await documentation_guard_is_active(connection, table=f"public.{TABLE.name}")
