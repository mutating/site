"""Schema hooks for tariff field documentation and its billing catalogue."""

import os
import re
from collections.abc import Mapping
from contextlib import closing
from threading import Lock
from urllib.parse import unquote_to_bytes
from uuid import uuid4
from weakref import WeakValueDictionary

from sqlalchemy import Index, event, text
from sqlalchemy.engine import Connection, Engine
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.pool import Pool
from sqlalchemy.schema import CreateIndex, CreateTable, DropTable

from throng_api.account_tariff_schema import TRIGGER_NAME
from throng_api.billing_schema import FIELD_DOCUMENTATION_TABLE
from throng_api.first_tariff_schema import (
    CATALOGUE_TABLE,
    LOCK_BILLING_WRITES,
    LOCK_CATALOGUE_GUARD_TRANSITION,
    MANAGED_CATALOGUE,
    POSTGRES_BILLING_WRITE_BODY,
    POSTGRES_COORDINATION_LOCK_SETTING,
    POSTGRES_DROP_CATALOGUE_GUARDS,
    postgres_catalog_function,
    postgres_catalogue_guard_lock_key,
    postgres_catalogue_schema,
    postgres_truncate_marker,
    postgres_write_guard,
)

DOCUMENTATION_GUARD = "serialize_tariff_documentation_writes"
DOCUMENTATION_GUARD_PREFIX = "serialize_tariff_doc_"
MANAGED_DOCUMENTATION = "tariff_documentation_guarded_drop"
DOCUMENTATION_DROP_OID = "tariff_documentation_drop_oid"
BILLING_INDEX_TABLE_CREATION = "billing_index_table_creation"
BILLING_TABLE_CREATORS = "billing_table_creators"
DOCUMENTATION_TABLE_CREATOR = "billing_documentation_table_creator"
CREATED_DOCUMENTATION_SCHEMA = "throng.billing_created_documentation_schema"
SQLITE_DOCUMENTATION_ATTACHMENTS = "billing_documentation_sqlite_attachments"
SQLITE_DOCUMENTATION_VFS = "billing_documentation_sqlite_vfs"
SQLITE_DEFAULT_VFS = b"win32" if os.name == "nt" else b"unix"
SQLITE_IDENTIFIER_LOWERCASE = str.maketrans(
    "ABCDEFGHIJKLMNOPQRSTUVWXYZ", "abcdefghijklmnopqrstuvwxyz"
)
SQLITE_SHARED_DOCUMENTATION_DATABASES = WeakValueDictionary()
SQLITE_SHARED_DOCUMENTATION_LOCK = Lock()
SQLITE_SQL_GAP = r"(?:\s|--[^\n]*(?:\n|$)|/\*[\s\S]*?\*/)*"
SQLITE_ATTACHMENT = re.compile(rf"{SQLITE_SQL_GAP}(?:ATTACH|DETACH)\b", re.IGNORECASE)
SQLITE_ATTACHMENT_SOURCE = re.compile(
    rf"""{SQLITE_SQL_GAP}ATTACH\b{SQLITE_SQL_GAP}(?:DATABASE\b{SQLITE_SQL_GAP})?
        (?P<filename>\?\d*|[:@$]\w+|'(?:''|[^'])*'|"(?:""|[^"])*")
        {SQLITE_SQL_GAP}AS\b""",
    re.IGNORECASE | re.VERBOSE,
)
RETRY_DOCUMENTATION_SNAPSHOT = """RAISE EXCEPTION
    'Retry the tariff documentation write with a current catalogue snapshot'
    USING ERRCODE = '40001';"""


def postgres_documentation_parent(documentation_oid):
    return f"""
        SELECT fk.oid AS foreign_key_oid, fk.conname AS foreign_key_name,
            guard.oid AS guard_oid, guard.tgfoid AS function_oid,
            guard_function.proname AS function_name,
            (SELECT option_value FROM pg_catalog.pg_options_to_table(guard_function.proconfig)
                WHERE option_name = 'search_path') AS guard_path,
            COALESCE(guard_function.pronamespace, plan_type.relnamespace) AS schema_oid
        FROM pg_catalog.pg_constraint AS fk
        JOIN pg_catalog.pg_attribute AS field ON field.attrelid = fk.conrelid
        JOIN pg_catalog.pg_class AS plan_type ON plan_type.oid = fk.confrelid
        LEFT JOIN pg_catalog.pg_trigger AS guard ON guard.tgrelid = plan_type.oid
            AND guard.tgname = '{LOCK_BILLING_WRITES}'
        LEFT JOIN pg_catalog.pg_proc AS guard_function ON guard_function.oid = guard.tgfoid
        WHERE fk.contype = 'f' AND fk.confdeltype = 'c' AND fk.conkey = ARRAY[field.attnum]
            AND fk.conrelid = {documentation_oid}
            AND field.attname = 'tariff_plan_type_id'
        ORDER BY (guard.oid IS NOT NULL) DESC, (plan_type.relname = 'tariff_plan_types') DESC, fk.oid
        LIMIT 1
    """


POSTGRES_DOCUMENTATION_GUARDS = ";\n".join(
    (
        postgres_catalog_function(
            "%2$I()",
            "trigger",
            # The catalogue can be migrated by a different owner.
            f"""DECLARE
                catalogue record;
                live_foreign_key oid;
                coordination_schema oid;
                current_path text;
            BEGIN
                SELECT parent.* INTO catalogue FROM ({postgres_documentation_parent("TG_RELID")}) AS parent;
                IF NOT FOUND THEN
                    {RETRY_DOCUMENTATION_SNAPSHOT}
                END IF;
                -- Native object lookup uses the current catalogue even under an old data snapshot.
                IF pg_catalog.current_setting('transaction_isolation') IN ('repeatable read', 'serializable') THEN
                    BEGIN
                        SELECT address.objid INTO live_foreign_key
                        FROM pg_catalog.pg_get_object_address(
                            'table constraint',
                            ARRAY[TG_TABLE_SCHEMA::text, TG_TABLE_NAME::text, catalogue.foreign_key_name::text],
                            ARRAY[]::text[]
                        ) AS address;
                    EXCEPTION WHEN undefined_object THEN
                        NULL;
                    END;
                    IF live_foreign_key IS DISTINCT FROM catalogue.foreign_key_oid THEN
                        {RETRY_DOCUMENTATION_SNAPSHOT}
                    END IF;
                END IF;
                current_path := 'pg_catalog, ' || catalogue.schema_oid::pg_catalog.regnamespace::text || ', pg_temp';
                PERFORM pg_catalog.set_config(
                    'search_path', COALESCE(catalogue.guard_path, current_path), true
                );
                SELECT relnamespace INTO coordination_schema
                FROM pg_catalog.pg_class
                WHERE oid = pg_catalog.to_regclass('coordination') AND relpersistence <> 't';
                IF NOT FOUND THEN
                    -- A schema rename can leave the function's configured path with its old name.
                    PERFORM pg_catalog.set_config('search_path', current_path, true);
                END IF;
                IF NOT pg_catalog.pg_try_advisory_xact_lock_shared(
                    {postgres_catalogue_guard_lock_key("COALESCE(coordination_schema, catalogue.schema_oid)")}
                ) THEN
                    RAISE EXCEPTION 'Tariff write guards are changing' USING ERRCODE = '55P03';
                END IF;
                IF catalogue.guard_oid IS NULL THEN
                    IF pg_catalog.to_regprocedure('{LOCK_BILLING_WRITES}()') IS NOT NULL THEN
                        {RETRY_DOCUMENTATION_SNAPSHOT}
                    END IF;
                    RETURN NULL;
                END IF;
                IF pg_catalog.to_regprocedure(
                    catalogue.schema_oid::pg_catalog.regnamespace::text || '.'
                    || pg_catalog.quote_ident(catalogue.function_name) || '()'
                ) IS DISTINCT FROM catalogue.function_oid THEN
                    {RETRY_DOCUMENTATION_SNAPSHOT}
                END IF;
                -- FK cascades run as the child owner after the parent has versioned coordination.
                IF TG_OP = 'DELETE' AND pg_catalog.pg_trigger_depth() > 1
                    AND pg_catalog.current_setting({POSTGRES_COORDINATION_LOCK_SETTING}, true) = 'on'
                    AND EXISTS (
                        SELECT 1 FROM pg_catalog.pg_locks AS held
                        WHERE held.pid = pg_catalog.pg_backend_pid() AND held.granted
                            AND held.relation = pg_catalog.to_regclass('coordination')
                            AND held.mode = 'RowExclusiveLock' AND held.locktype = 'relation'
                    ) THEN
                    RETURN NULL;
                END IF;
                {POSTGRES_BILLING_WRITE_BODY}
            END;""",
            search_schema=None,
            replace=False,
        ),
        postgres_write_guard("%3$I", routine="%2$I"),
        postgres_truncate_marker("%3$I", routine="%2$I"),
    )
)


def postgres_documentation_guards(
    relation,
    ddl,
    *,
    declarations="",
    before="",
    require_catalogue_guard,
    track_creation=False,
):
    require_guard = "TRUE" if require_catalogue_guard else "FALSE"
    receipt = (
        f"PERFORM pg_catalog.set_config('{CREATED_DOCUMENTATION_SCHEMA}', installation.table_schema, false);"
        if track_creation
        else ""
    )
    return f"""
    DECLARE installation record;
        routine_name name := '{DOCUMENTATION_GUARD}';
        {declarations}
    BEGIN
        {before}
        SELECT doc_schema.nspname AS table_schema, doc.relname AS table_name, parent.guard_oid
        INTO installation
        FROM pg_catalog.pg_class AS doc
        JOIN pg_catalog.pg_namespace AS doc_schema ON doc_schema.oid = doc.relnamespace
        JOIN LATERAL ({postgres_documentation_parent("doc.oid")}) AS parent ON true
        WHERE doc.oid = {relation};
        IF NOT FOUND OR ({require_guard} AND installation.guard_oid IS NULL) THEN
            RAISE EXCEPTION 'Tariff field documentation has no compatible tariff catalogue';
        END IF;
        IF pg_catalog.to_regprocedure(
            pg_catalog.quote_ident(installation.table_schema) || '.' || routine_name || '()'
        ) IS NOT NULL THEN
            routine_name := '{DOCUMENTATION_GUARD_PREFIX}'
                || pg_catalog.replace(pg_catalog.gen_random_uuid()::text, '-', '');
        END IF;
        EXECUTE pg_catalog.format($definition$
            {ddl}
        $definition$, installation.table_schema, routine_name, installation.table_name);
        {receipt}
    END;"""


def is_managed_documentation(table):
    return table.name == FIELD_DOCUMENTATION_TABLE and table.info.get(MANAGED_DOCUMENTATION)


def is_managed_catalogue(table):
    return table.name == CATALOGUE_TABLE and table.info.get(MANAGED_CATALOGUE)


def postgres_string(value):
    escaped = value.replace("\\", "\\\\").replace("'", "''")
    # A resolved name may itself contain a schema-translation placeholder.
    return "E'" + escaped.replace("__[SCHEMA_", r"__\x5bSCHEMA_") + "'"


def create_documentation_guards(qualified_table):
    body = postgres_documentation_guards(
        f"pg_catalog.to_regclass({postgres_string(qualified_table)})",
        POSTGRES_DOCUMENTATION_GUARDS,
        require_catalogue_guard=True,
    )
    return "DO " + postgres_string(body)


def postgres_ddl_literal(compiler, statement):
    if compiler.schema_translate_map:
        # Resolve these before quoting the native DDL inside a PostgreSQL string literal.
        statement = compiler.preparer._render_schema_translates(
            statement, dict(compiler.schema_translate_map)
        )
    return postgres_string(statement)


@compiles(CreateTable, "postgresql")
def compile_documentation_create(element, compiler, **kw):
    statement = compiler.visit_create_table(element, **kw)
    if not is_managed_documentation(element.element):
        return statement
    matching_tables = (
        "SELECT c.oid FROM pg_catalog.pg_class AS c WHERE c.relkind IN ('r', 'p') "
        f"AND c.relname = {postgres_string(element.element.name)}"
    )
    declarations = f"""previous_tables oid[] := ARRAY({matching_tables});
        created_tables oid[];"""
    if_skipped = (
        "IF pg_catalog.cardinality(created_tables) = 0 THEN RETURN; END IF;"
        if element.if_not_exists
        else ""
    )
    # Native CREATE keeps an exclusive lock on each new relation in this transaction.
    before = f"""EXECUTE {postgres_ddl_literal(compiler, statement)};
        created_tables := ARRAY({matching_tables} AND NOT c.oid = ANY(previous_tables)
            AND EXISTS (
                SELECT 1 FROM pg_catalog.pg_locks AS held
                WHERE held.pid = pg_catalog.pg_backend_pid() AND held.granted
                    AND held.mode = 'AccessExclusiveLock' AND held.locktype = 'relation'
                    AND held.relation = c.oid
            )
        );
        {if_skipped}
        IF pg_catalog.cardinality(created_tables) <> 1 THEN
            RAISE EXCEPTION 'Cannot identify the newly created tariff documentation table';
        END IF;"""
    ddl = compiler.sql_compiler.post_process_text(POSTGRES_DOCUMENTATION_GUARDS)
    body = postgres_documentation_guards(
        "created_tables[1]",
        ddl,
        declarations=declarations,
        before=before,
        require_catalogue_guard=False,
        track_creation=DOCUMENTATION_TABLE_CREATOR in element.get_execution_options(),
    )
    return "DO " + postgres_string(body)


def billing_index_creation_schema(element, compiler):
    if compiler.preparer.schema_for_object(element.element.table):
        return None
    return element.get_execution_options().get(BILLING_INDEX_TABLE_CREATION)


@compiles(CreateIndex, "postgresql")
def compile_postgres_billing_index(element, compiler, **kw):
    statement = compiler.visit_create_index(element, **kw)
    index = element.element
    schema = billing_index_creation_schema(element, compiler)
    if not schema:
        return statement
    name = compiler._prepared_index_name(index, include_schema=False)
    table = compiler.preparer.format_table(index.table)
    prefix, suffix = statement.split(f"{name} ON {table} ", 1)
    return f"{prefix}{name} ON {compiler.preparer.quote_schema(schema)}.{table} {suffix}"


@compiles(CreateIndex, "sqlite")
def compile_sqlite_billing_index(element, compiler, **kw):
    statement = compiler.visit_create_index(element, **kw)
    index = element.element
    schema = billing_index_creation_schema(element, compiler)
    if not schema:
        return statement
    name = compiler._prepared_index_name(index, include_schema=False)
    return statement.replace(
        f"{name} ON ", f"{compiler.preparer.quote_schema(schema)}.{name} ON ", 1
    )


def postgres_guarded_drop(element, compiler, *, declarations="", before="", after=""):
    relation = postgres_ddl_literal(compiler, compiler.preparer.format_table(element.element))
    command = postgres_string("DROP TABLE IF EXISTS " if element.if_exists else "DROP TABLE ")
    absent = (
        "RETURN;"
        if element.if_exists
        else f"RAISE undefined_table USING MESSAGE = 'Table ' || {relation} || ' does not exist';"
    )
    # Use the same physical target for cleaning up its guards and dropping its table.
    body = f"""DECLARE
        selected_table regclass := pg_catalog.to_regclass({relation});
        table_schema name;
        table_name name;
        qualified_table text;
        drop_sql text := {command};
        {declarations}
    BEGIN
        SELECT n.nspname, c.relname INTO table_schema, table_name
        FROM pg_catalog.pg_class AS c JOIN pg_catalog.pg_namespace AS n ON n.oid = c.relnamespace
        WHERE c.oid = selected_table;
        IF NOT FOUND THEN {absent} END IF;
        qualified_table := pg_catalog.quote_ident(table_schema) || '.' || pg_catalog.quote_ident(table_name);
        {before}
        EXECUTE drop_sql || qualified_table;
        {after}
    END;"""
    return "DO " + postgres_string(body)


def catalogue_guards_exist(schema):
    return f"""(pg_catalog.to_regprocedure(
        pg_catalog.quote_ident({schema}) || '.{LOCK_BILLING_WRITES}()'
    ) IS NOT NULL OR pg_catalog.to_regprocedure(
        pg_catalog.quote_ident({schema}) || '.{TRIGGER_NAME}()'
    ) IS NOT NULL)"""


def compile_catalogue_drop(element, compiler):
    schema = postgres_catalogue_schema("selected_table")
    remove_guards = compiler.sql_compiler.post_process_text(POSTGRES_DROP_CATALOGUE_GUARDS)
    guard_exists = catalogue_guards_exist("table_schema")
    absent = "RETURN;" if element.if_exists else "RAISE;"
    changed = (
        "RAISE EXCEPTION 'Tariff assignment table changed during DROP' USING ERRCODE = '55P03';"
    )
    return postgres_guarded_drop(
        element,
        compiler,
        declarations="billing_schema name;",
        before=f"""BEGIN
            EXECUTE 'LOCK TABLE ' || qualified_table || ' IN ACCESS EXCLUSIVE MODE'
                || CASE WHEN {guard_exists} THEN ' NOWAIT' ELSE '' END;
        EXCEPTION WHEN insufficient_privilege THEN
            -- A schema owner may DROP an unguarded table without having explicit LOCK privileges.
            IF {guard_exists} THEN RAISE; END IF;
            EXECUTE drop_sql || qualified_table;
            IF {guard_exists} THEN {changed} END IF;
            RETURN;
        WHEN undefined_table THEN
            {absent}
        END;
        IF pg_catalog.to_regclass(qualified_table) IS DISTINCT FROM selected_table THEN
            {changed}
        END IF;
        billing_schema := ({schema});
        IF {catalogue_guards_exist("billing_schema")} THEN
            {LOCK_CATALOGUE_GUARD_TRANSITION}
            {remove_guards}
        END IF;""",
    )


@compiles(DropTable, "postgresql")
def compile_guarded_billing_drop(element, compiler, **kw):
    table = element.element
    if is_managed_catalogue(table):
        return compile_catalogue_drop(element, compiler)
    if not is_managed_documentation(table):
        return compiler.visit_drop_table(element, **kw)
    expected_oid = table.info.get(DOCUMENTATION_DROP_OID)
    verify_target = verify_drop = ""
    if expected_oid is not None:
        changed = """RAISE EXCEPTION 'Tariff documentation table changed during downgrade'
            USING ERRCODE = '40001';"""
        verify_target = f"""IF selected_table IS DISTINCT FROM {int(expected_oid)}::oid THEN
            {changed}
        END IF;"""
        verify_drop = f"""IF EXISTS (SELECT 1 FROM pg_catalog.pg_class WHERE oid = selected_table) THEN
            {changed}
        END IF;"""
    # The FK parent can require a lock. Keep the timeout and the cleanup in one statement.
    return postgres_guarded_drop(
        element,
        compiler,
        declarations="""previous_lock_timeout text := pg_catalog.current_setting('lock_timeout');
            guard_function text;
            guard_function_oid oid;
            guard_schema name;
            retired_name name;
            retired_function text;""",
        before=f"""{verify_target}
        PERFORM pg_catalog.set_config('lock_timeout', '1ms', true);
        SELECT pg_catalog.quote_ident(n.nspname) || '.' || pg_catalog.quote_ident(p.proname) || '()',
            p.oid, n.nspname
        INTO guard_function, guard_function_oid, guard_schema
        FROM pg_catalog.pg_trigger AS t
        JOIN pg_catalog.pg_proc AS p ON p.oid = t.tgfoid
        JOIN pg_catalog.pg_namespace AS n ON n.oid = p.pronamespace
        WHERE t.tgrelid = selected_table AND t.tgname = '{LOCK_BILLING_WRITES}' AND NOT t.tgisinternal
            AND p.pronargs = 0
            AND (p.proname = '{DOCUMENTATION_GUARD}'
                OR pg_catalog.left(p.proname::text, {len(DOCUMENTATION_GUARD_PREFIX)}) = '{DOCUMENTATION_GUARD_PREFIX}');""",
        after=f"""{verify_drop}
        BEGIN
            IF pg_catalog.to_regprocedure(guard_function) = guard_function_oid THEN
                -- Take the routine's DDL lock and roll back the rename if its identity changed.
                retired_name := 'throng_retired_tariff_doc_' || guard_function_oid::text;
                EXECUTE 'ALTER FUNCTION ' || guard_function || ' RENAME TO '
                    || pg_catalog.quote_ident(retired_name);
                retired_function := pg_catalog.quote_ident(guard_schema) || '.'
                    || pg_catalog.quote_ident(retired_name) || '()';
                IF pg_catalog.to_regprocedure(retired_function) IS DISTINCT FROM guard_function_oid THEN
                    RAISE undefined_function;
                END IF;
                EXECUTE 'DROP FUNCTION ' || retired_function;
            END IF;
        EXCEPTION WHEN undefined_function OR duplicate_function
            OR dependent_objects_still_exist OR insufficient_privilege THEN
            NULL;
        END;
        PERFORM pg_catalog.set_config('lock_timeout', previous_lock_timeout, true);""",
    )


def register_field_documentation_triggers(table):
    table.info[MANAGED_DOCUMENTATION] = True


def sqlite_databases(connection):
    return {
        name: filename
        for _, name, filename in connection.exec_driver_sql("PRAGMA database_list").all()
    }


def sqlite_documentation_has_relation(connection, schema, relation):
    preparer = connection.dialect.identifier_preparer
    catalogue = f"{preparer.quote_schema(schema)}.sqlite_master"
    return bool(
        connection.scalar(
            text(
                f"SELECT 1 FROM {catalogue} WHERE name = :relation COLLATE NOCASE "
                "AND type IN ('table', 'view')"
            ),
            {"relation": relation},
        )
    )


def sqlite_documentation_schema(connection, relation):
    databases = sqlite_databases(connection)
    if len(databases) == 1:
        return next(iter(databases))
    # SQLite searches temp, then main, then attached databases in attachment order.
    for name in sorted(databases, key=lambda name: name != "temp"):
        if sqlite_documentation_has_relation(connection, name, relation):
            return name
    return None


def sqlite_documentation_storage(connection, schema):
    schema = schema.translate(SQLITE_IDENTIFIER_LOWERCASE)
    for name, filename in sqlite_databases(connection).items():
        if name.translate(SQLITE_IDENTIFIER_LOWERCASE) == schema:
            if filename:
                return "file", filename
            attachments = connection.info.setdefault(SQLITE_DOCUMENTATION_ATTACHMENTS, {})
            if name not in attachments:
                attachments[name] = SQLiteDocumentationStorage("connection")
            return attachments[name].identity
    return None


def sqlite_documentation_lookup_schema(connection, storage, relation):
    attachments = connection.info.get(SQLITE_DOCUMENTATION_ATTACHMENTS, {})
    for name, filename in sqlite_databases(connection).items():
        origin = (
            ("file", filename) if filename else getattr(attachments.get(name), "identity", None)
        )
        if origin == storage and sqlite_documentation_has_relation(connection, name, relation):
            return name
    return None


class SQLiteDocumentationStorage:
    """Keep a memory database identity alive while its connections hold it."""

    def __init__(self, kind):
        self.identity = kind, str(uuid4())


def sqlite_uri_part(value):
    return unquote_to_bytes(value).partition(b"\0")[0]


def sqlite_documentation_uri(filename):
    if not isinstance(filename, str) or not filename.startswith("file:"):
        return b"", {}
    path, _, query = filename[5:].partition("#")[0].partition("?")
    if path.startswith("//"):
        authority, slash, remainder = path[2:].partition("/")
        if authority not in ("", "localhost"):
            return b"", {}
        path = slash + remainder
    # SQLite compares decoded filename bytes and stops a URI part at its first NUL.
    name = sqlite_uri_part(path)
    options = {
        sqlite_uri_part(key): sqlite_uri_part(value)
        for key, _, value in (part.partition("=") for part in query.split("&"))
    }
    return name, options


def sqlite_memory_documentation_key(name, options, inherited_vfs):
    if (
        name
        and options.get(b"cache") == b"shared"
        and (options.get(b"mode") == b"memory" or name == b":memory:")
    ):
        return name, options.get(b"vfs", inherited_vfs) or SQLITE_DEFAULT_VFS
    return None


def sqlite_shared_documentation_key(statement, parameters, inherited_vfs=SQLITE_DEFAULT_VFS):
    match = SQLITE_ATTACHMENT_SOURCE.match(statement)
    if match is None:
        return None
    filename = match["filename"]
    if filename[0] in {"'", '"'}:
        quote = filename[0]
        filename = filename[1:-1].replace(quote * 2, quote)
    elif isinstance(parameters, Mapping):
        filename = parameters.get(filename[1:])
    else:
        position = int(filename[1:]) if filename.startswith("?") and filename[1:] else 1
        filename = parameters[position - 1]
    name, options = sqlite_documentation_uri(filename)
    return sqlite_memory_documentation_key(name, options, inherited_vfs)


def retain_sqlite_documentation_storage(key):
    if key is None:
        return SQLiteDocumentationStorage("connection")
    with SQLITE_SHARED_DOCUMENTATION_LOCK:
        storage = SQLITE_SHARED_DOCUMENTATION_DATABASES.get(key)
        if storage is None:
            storage = SQLiteDocumentationStorage("shared_memory")
            SQLITE_SHARED_DOCUMENTATION_DATABASES[key] = storage
        return storage


@event.listens_for(Engine, "do_connect")
def remember_sqlite_main_database(dialect, record, args, parameters):
    if dialect.name != "sqlite":
        return
    filename = args[0] if parameters.get("uri") else None
    name, options = sqlite_documentation_uri(filename)
    vfs = options.get(b"vfs") or SQLITE_DEFAULT_VFS
    record.info[SQLITE_DOCUMENTATION_VFS] = vfs
    key = sqlite_memory_documentation_key(name, options, vfs)
    if key is not None:
        record.info.setdefault(SQLITE_DOCUMENTATION_ATTACHMENTS, {})["main"] = (
            retain_sqlite_documentation_storage(key)
        )


@event.listens_for(Engine, "before_cursor_execute")
def remember_sqlite_attachment_names(connection, _cursor, statement, _parameters, context, _many):
    if connection.dialect.name == "sqlite" and SQLITE_ATTACHMENT.match(statement):
        context.sqlite_documentation_attachments = sqlite_databases(connection)


@event.listens_for(Engine, "after_cursor_execute")
def refresh_sqlite_attachment_names(connection, _cursor, statement, parameters, context, _many):
    previous = getattr(context, "sqlite_documentation_attachments", None)
    if previous is None:
        return
    current = sqlite_databases(connection)
    attachments = connection.info.setdefault(SQLITE_DOCUMENTATION_ATTACHMENTS, {})
    for detached in previous.keys() - current.keys():
        attachments.pop(detached, None)
    for attached in current.keys() - previous.keys():
        if not current[attached]:
            inherited_vfs = connection.info.get(SQLITE_DOCUMENTATION_VFS, SQLITE_DEFAULT_VFS)
            key = sqlite_shared_documentation_key(statement, parameters, inherited_vfs)
            attachments[attached] = retain_sqlite_documentation_storage(key)


@event.listens_for(Pool, "close")
def forget_sqlite_documentation_attachments(_connection, record):
    record.info.pop(SQLITE_DOCUMENTATION_ATTACHMENTS, None)


INDEX_SCHEMA_QUERY = {"postgresql": "SELECT pg_catalog.current_schema()", "sqlite": "SELECT 'main'"}
TAKE_DOCUMENTATION_CREATION_SCHEMA = f"""
    WITH created AS MATERIALIZED (
        SELECT pg_catalog.current_setting('{CREATED_DOCUMENTATION_SCHEMA}', true) AS schema_name
    )
    SELECT schema_name, pg_catalog.set_config(
        '{CREATED_DOCUMENTATION_SCHEMA}', '', false
    )
    FROM created
"""


@event.listens_for(Engine, "before_execute", retval=True)
def mark_documentation_creation(connection, statement, multiparams, params, _options):
    if (
        connection.dialect.name == "postgresql"
        and isinstance(statement, CreateTable)
        and is_managed_documentation(statement.element)
        and (creators := connection.info.get(BILLING_TABLE_CREATORS)) is not None
        and (runner := creators.pop(statement.element, None)) is not None
    ):
        statement = statement.execution_options(**{DOCUMENTATION_TABLE_CREATOR: runner})
    return statement, multiparams, params


@event.listens_for(Engine, "after_cursor_execute")
def record_documentation_creation(connection, _cursor, _sql, _parameters, context, _many):
    compiled = context.compiled
    if compiled is None or not isinstance(compiled.statement, CreateTable):
        return
    statement = compiled.statement
    runner = statement.get_execution_options().get(DOCUMENTATION_TABLE_CREATOR)
    if runner is not None:
        with closing(connection.connection.cursor()) as probe:
            probe.execute(TAKE_DOCUMENTATION_CREATION_SCHEMA)
            schema = probe.fetchone()[0]
        runner.memo[BILLING_INDEX_TABLE_CREATION][statement.element] = schema


@event.listens_for(Engine, "do_execute")
def record_billing_table_schema(cursor, _sql, _parameters, context):
    compiled = context.compiled
    if compiled is None or not isinstance(compiled.statement, CreateTable):
        return
    table = compiled.statement.element
    creators = context.root_connection.info.get(BILLING_TABLE_CREATORS)
    if creators is None:
        return
    runner = creators.pop(table, None)
    if runner is not None:
        # Read the namespace after SQLAlchemy statement hooks, immediately before CREATE.
        cursor.execute(INDEX_SCHEMA_QUERY.get(context.dialect.name, "SELECT NULL"))
        runner.memo[BILLING_INDEX_TABLE_CREATION][table] = cursor.fetchone()[0]


@event.listens_for(Engine, "do_execute_no_params")
def record_unparameterized_billing_table_schema(cursor, sql, context):
    record_billing_table_schema(cursor, sql, (), context)


def route_indexes_during_table_creation(table, connection, *, _ddl_runner, **_):
    if not isinstance(connection, Connection):
        return
    connection.info.setdefault(BILLING_TABLE_CREATORS, WeakValueDictionary())[table] = _ddl_runner
    tables = _ddl_runner.memo.setdefault(BILLING_INDEX_TABLE_CREATION, {})
    if tables:
        tables[table] = None
        return
    tables[table] = None
    traverse = _ddl_runner.traverse_single

    def visit(item, **options):
        # SchemaGenerator passes create_ok to indexes visited from Table.create or create_all.
        if not (isinstance(item, Index) and item.table in tables and options.get("create_ok")):
            return traverse(item, **options)

        def qualify(_connection, statement, multiparams, params, _options):
            if isinstance(statement, CreateIndex) and statement.element is item:
                statement = statement.execution_options(
                    **{BILLING_INDEX_TABLE_CREATION: tables[item.table]}
                )
            return statement, multiparams, params

        event.listen(connection, "before_execute", qualify, retval=True)
        try:
            return traverse(item, **options)
        finally:
            event.remove(connection, "before_execute", qualify)

    _ddl_runner.traverse_single = visit


def register_billing_index_creation(table):
    event.listen(table, "before_create", route_indexes_during_table_creation, propagate=True)
