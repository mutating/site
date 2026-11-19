"""Seed missing account assignments when a tariff type receives its first plan."""

from functools import partial

from throng_api.account_tariff_schema import (
    CREATE_ACCOUNT_TARIFF_TRIGGER,
    DROP_ACCOUNT_TARIFF_TRIGGER,
    METADATA_CATALOGUE_RELATION,
    TRIGGER_NAME,
    postgres_account_tariff_body,
    register_tariff_triggers,
)

ASSIGN_FIRST_TARIFF = "assign_first_account_tariff"
SEED_FIRST_TARIFF = "seed_first_tariff_of_type"
SEED_NEW_ACCOUNT = "seed_tariffs_for_new_account"
LOCK_BILLING_WRITES = "serialize_billing_writes"
LOCK_CATALOGUE_CHANGES = "serialize_tariff_catalogue_changes"
MARK_BILLING_TRUNCATE = "mark_billing_truncate"
BILLING_TRUNCATED = "throng.billing_tables_truncated"
POSTGRES_COORDINATION_LOCK_SETTING = (
    "'throng.billing_coordination_' || pg_catalog.to_regclass('coordination')::oid::text"
)
CATALOGUE_GUARD_LOCK_NAMESPACE = 0x5448524E
CATALOGUE_TABLE = "payment_account_tariff_plans"
MANAGED_CATALOGUE = "tariff_catalogue_guarded_drop"
STATEMENT_GUARD_TABLES = (
    "payment_accounts",
    CATALOGUE_TABLE,
    "payment_ledger_entries",
    "tariff_plan_types",
    "user_tariff_plans",
)
BILLING_WRITE_TABLES = (*STATEMENT_GUARD_TABLES, "tariff_plans")


def postgres_relation_schema(table_oid):
    return f"""
        SELECT n.nspname
        FROM pg_catalog.pg_class AS c JOIN pg_catalog.pg_namespace AS n ON n.oid = c.relnamespace
        WHERE c.oid = {table_oid}
    """


def postgres_catalogue_schema(table_oid):
    # A same-named relation is only part of the catalogue when its account FK points into it.
    return f"""{postgres_relation_schema(table_oid)}
        AND c.relpersistence <> 't' AND EXISTS (
            SELECT 1 FROM pg_catalog.pg_constraint AS fk
            JOIN pg_catalog.pg_class AS account ON account.oid = fk.confrelid
            WHERE fk.contype = 'f' AND fk.conrelid = c.oid
                AND account.relname = 'payment_accounts' AND account.relnamespace = c.relnamespace
        )
    """


def postgres_catalogue_guard_lock_key(namespace):
    return f"{CATALOGUE_GUARD_LOCK_NAMESPACE}, ({namespace})::integer"


def postgres_catalogue_lock_statements(schema):
    tables = ", ".join(schema + table for table in BILLING_WRITE_TABLES)
    return (
        f"LOCK TABLE {schema}coordination IN EXCLUSIVE MODE NOWAIT",
        f"LOCK TABLE {tables} IN ACCESS EXCLUSIVE MODE NOWAIT",
    )


LOCK_CATALOGUE_GUARD_TRANSITION = f"""
    -- Keep documents on one locking order throughout a transaction.
    IF NOT pg_catalog.pg_try_advisory_xact_lock({
    postgres_catalogue_guard_lock_key(
        "(SELECT oid FROM pg_catalog.pg_namespace WHERE nspname = billing_schema)"
    )
}) THEN
        RAISE EXCEPTION 'Tariff documentation writes are in progress' USING ERRCODE = '55P03';
    END IF;
"""


def version_coordination(schema=""):
    return f"""
        INSERT INTO {schema}coordination (id, revision) VALUES (1, 0)
        ON CONFLICT (id) DO UPDATE SET revision = coordination.revision
    """


def insert_missing_tariffs(plans, *, schema="", lock_accounts=False):
    lock = "FOR KEY SHARE OF account" if lock_accounts else ""
    return f"""
        INSERT INTO {schema}payment_account_tariff_plans (payment_account_id, tariff_plan_type_id, tariff_plan_id)
        SELECT account.id, plan.tariff_plan_type_id, plan.id
        FROM {schema}payment_accounts AS account CROSS JOIN ({plans}) AS plan
        WHERE NOT EXISTS (
            SELECT 1 FROM {schema}payment_account_tariff_plans AS assigned
            WHERE assigned.payment_account_id = account.id
                AND assigned.tariff_plan_type_id = plan.tariff_plan_type_id
        )
        {lock}
        ON CONFLICT (payment_account_id, tariff_plan_type_id) DO NOTHING
    """


def backfill_first_tariffs(schema):
    return insert_missing_tariffs(
        f"""
        SELECT plan.tariff_plan_type_id, plan.id
        FROM {schema}tariff_plan_types AS plan_type
        JOIN {schema}tariff_plans AS plan ON plan.id = (
            SELECT candidate.id FROM {schema}tariff_plans AS candidate
            WHERE candidate.tariff_plan_type_id = plan_type.id
            ORDER BY candidate.published_at
            LIMIT 1
        )
        """,
        schema=schema,
    )


INSERTED_TARIFF = "SELECT NEW.tariff_plan_type_id AS tariff_plan_type_id, NEW.id AS id"


def postgres_catalog_commands(*statements):
    ddl = ";\n".join(statements)
    return f"""EXECUTE pg_catalog.format($definition$
        {ddl}
    $definition$, billing_schema);"""


def postgres_catalog_ddl(
    *statements, before="", relation="'tariff_plans'", existing_catalogue=False, schema=None
):
    # Use the schema containing the catalogue even if search_path begins with another schema.
    if schema is None:
        resolve_schema = (
            postgres_catalogue_schema if existing_catalogue else postgres_relation_schema
        )
        schema_query = resolve_schema(f"pg_catalog.to_regclass({relation})")
    else:
        # Keep identifiers, including dollar delimiters, out of the DO block syntax.
        schema_query = (
            f"pg_catalog.convert_from(pg_catalog.decode('{schema.encode().hex()}', 'hex'), 'UTF8')"
        )
    return f"""DO $billing_ddl$
    DECLARE
        billing_schema name := ({schema_query});
    BEGIN
        {before}
        {postgres_catalog_commands(*statements)}
    END;
    $billing_ddl$"""


def postgres_catalog_function(signature, returns, body, *, search_schema="%1$I", replace=True):
    path = f"pg_catalog, {search_schema}, pg_temp" if search_schema else "pg_catalog, pg_temp"
    verb = "CREATE OR REPLACE" if replace else "CREATE"
    return f"""{verb} FUNCTION %1$I.{signature} RETURNS {returns}
        LANGUAGE plpgsql SET search_path TO {path} AS $body$
        {body}
        $body$"""


def postgres_write_guard(table, *, routine=LOCK_BILLING_WRITES):
    return f"""CREATE TRIGGER {LOCK_BILLING_WRITES} BEFORE INSERT OR UPDATE OR DELETE
        ON %1$I.{table} FOR EACH STATEMENT
        EXECUTE FUNCTION %1$I.{routine}()"""


def postgres_truncate_marker(table, *, routine=LOCK_BILLING_WRITES):
    return f"""CREATE TRIGGER {MARK_BILLING_TRUNCATE} AFTER TRUNCATE
        ON %1$I.{table} FOR EACH STATEMENT
        EXECUTE FUNCTION %1$I.{routine}()"""


def postgres_drop_table_guards(table):
    return f"""DROP TRIGGER IF EXISTS {LOCK_BILLING_WRITES} ON %1$I.{table};
        DROP TRIGGER IF EXISTS {MARK_BILLING_TRUNCATE} ON %1$I.{table}"""


POSTGRES_ACCOUNT_DEFAULT_TRIGGER = f"""CREATE TRIGGER {TRIGGER_NAME} AFTER INSERT
    ON %1$I.payment_accounts FOR EACH ROW EXECUTE FUNCTION %1$I.{TRIGGER_NAME}()"""


POSTGRES_FIRST_TARIFFS = insert_missing_tariffs(
    "SELECT plan_type_id AS tariff_plan_type_id, plan_id AS id", lock_accounts=True
)


POSTGRES_BILLING_WRITE_BODY = f"""
IF TG_OP = 'TRUNCATE' THEN
    PERFORM pg_catalog.set_config('{BILLING_TRUNCATED}', 'on', true);
    RETURN NULL;
END IF;
-- A user FK cascade already holds its owner row.
IF TG_TABLE_NAME = 'user_tariff_plans' AND TG_OP = 'DELETE'
    AND pg_catalog.pg_trigger_depth() > 1 THEN
    RETURN NULL;
END IF;
-- TRUNCATE takes exclusive table locks before any trigger runs.
-- Take the upsert's locks without waiting when the order is already reversed.
IF pg_catalog.current_setting('{BILLING_TRUNCATED}', true) = 'on' THEN
    LOCK TABLE coordination IN ROW EXCLUSIVE MODE NOWAIT;
    PERFORM id FROM coordination WHERE id = 1 FOR NO KEY UPDATE NOWAIT;
    IF NOT FOUND THEN
        -- A missing sentinel can be the target of a concurrent insert.
        LOCK TABLE coordination IN EXCLUSIVE MODE NOWAIT;
    END IF;
END IF;
{version_coordination()};
PERFORM pg_catalog.set_config({POSTGRES_COORDINATION_LOCK_SETTING}, 'on', true);
RETURN NEW;
"""


POSTGRES_CREATE_FIRST_TARIFF_OBJECTS = (
    # Version the shared row so stale repeatable-read writers retry as well.
    postgres_catalog_function(
        f"{LOCK_BILLING_WRITES}()",
        "trigger",
        f"BEGIN\n{POSTGRES_BILLING_WRITE_BODY}\nEND;",
    ),
    *(
        f"{postgres_drop_table_guards(table)};\n{postgres_truncate_marker(table)}"
        for table in BILLING_WRITE_TABLES
    ),
    # Billing writers acquire coordination before taking row or foreign-key locks.
    *(postgres_write_guard(table) for table in STATEMENT_GUARD_TABLES),
    # The row trigger makes PostgreSQL process COPY one tariff at a time.
    f"""CREATE TRIGGER {LOCK_BILLING_WRITES} BEFORE INSERT ON %1$I.tariff_plans
            FOR EACH ROW EXECUTE FUNCTION %1$I.{LOCK_BILLING_WRITES}()""",
    f"DROP TRIGGER IF EXISTS {LOCK_CATALOGUE_CHANGES} ON %1$I.tariff_plans",
    f"""CREATE TRIGGER {LOCK_CATALOGUE_CHANGES} BEFORE UPDATE OR DELETE
            ON %1$I.tariff_plans FOR EACH STATEMENT
            EXECUTE FUNCTION %1$I.{LOCK_BILLING_WRITES}()""",
    postgres_catalog_function(
        f"{SEED_FIRST_TARIFF}(plan_type_id uuid, plan_id uuid)",
        "boolean",
        f"""BEGIN
                    IF NOT EXISTS (
                        SELECT 1 FROM tariff_plans
                        WHERE tariff_plan_type_id = plan_type_id AND id <> plan_id
                    ) THEN
                        {POSTGRES_FIRST_TARIFFS};
                    END IF;
                    RETURN FALSE;
                END;""",
    ),
    f"DROP TRIGGER IF EXISTS {ASSIGN_FIRST_TARIFF} ON %1$I.tariff_plans",
    # Evaluate the fan-out at the inserted row, before any later CTE insert.
    # The function returns false because there is no work to queue at statement end.
    f"""CREATE TRIGGER {ASSIGN_FIRST_TARIFF} AFTER INSERT ON %1$I.tariff_plans FOR EACH ROW
            WHEN (%1$I.{SEED_FIRST_TARIFF}(NEW.tariff_plan_type_id, NEW.id))
            EXECUTE FUNCTION %1$I.{LOCK_BILLING_WRITES}()""",
    postgres_catalog_function(
        f"{SEED_NEW_ACCOUNT}(new_account_id uuid, account_created_at timestamptz)",
        "boolean",
        postgres_account_tariff_body(
            account_id="new_account_id", created_at="account_created_at", result="FALSE"
        ),
    ),
    f"DROP TRIGGER IF EXISTS {TRIGGER_NAME} ON %1$I.payment_accounts",
    # Account initialization uses the same visibility boundary as the first-plan seed.
    f"""CREATE TRIGGER {TRIGGER_NAME} AFTER INSERT ON %1$I.payment_accounts FOR EACH ROW
            WHEN (%1$I.{SEED_NEW_ACCOUNT}(NEW.id, NEW.created_at))
            EXECUTE FUNCTION %1$I.{LOCK_BILLING_WRITES}()""",
)

CREATE_FIRST_TARIFF_TRIGGERS = {
    "sqlite": (
        f"""CREATE TRIGGER IF NOT EXISTS {ASSIGN_FIRST_TARIFF}
        AFTER INSERT ON tariff_plans FOR EACH ROW
        WHEN NOT EXISTS (
            SELECT 1 FROM tariff_plans
            WHERE tariff_plan_type_id = NEW.tariff_plan_type_id AND id <> NEW.id
        )
        BEGIN
            {insert_missing_tariffs(INSERTED_TARIFF)};
        END""",
    ),
}

POSTGRES_DROP_FIRST_TARIFF_OBJECTS = (
    f"DROP TRIGGER IF EXISTS {ASSIGN_FIRST_TARIFF} ON %1$I.tariff_plans",
    f"DROP TRIGGER IF EXISTS {LOCK_CATALOGUE_CHANGES} ON %1$I.tariff_plans",
    f"DROP TRIGGER IF EXISTS {TRIGGER_NAME} ON %1$I.payment_accounts",
    *(postgres_drop_table_guards(table) for table in BILLING_WRITE_TABLES),
    f"DROP FUNCTION IF EXISTS %1$I.{SEED_FIRST_TARIFF}(uuid, uuid)",
    f"DROP FUNCTION IF EXISTS %1$I.{SEED_NEW_ACCOUNT}(uuid, timestamptz)",
    f"DROP FUNCTION IF EXISTS %1$I.{LOCK_BILLING_WRITES}()",
)

POSTGRES_DROP_CATALOGUE_GUARDS = postgres_catalog_commands(
    *postgres_catalogue_lock_statements("%1$I."),
    *POSTGRES_DROP_FIRST_TARIFF_OBJECTS,
    f"DROP FUNCTION IF EXISTS %1$I.{TRIGGER_NAME}()",
)

DROP_FIRST_TARIFF_TRIGGERS = {
    "sqlite": (f"DROP TRIGGER {ASSIGN_FIRST_TARIFF}",),
}


def register_first_tariff_triggers(table):
    table.info[MANAGED_CATALOGUE] = True
    postgres_metadata_ddl = partial(postgres_catalog_ddl, relation=METADATA_CATALOGUE_RELATION)
    postgres_objects = (
        postgres_catalog_function(f"{TRIGGER_NAME}()", "trigger", postgres_account_tariff_body()),
        *POSTGRES_CREATE_FIRST_TARIFF_OBJECTS,
    )
    create = {
        "sqlite": CREATE_ACCOUNT_TARIFF_TRIGGER["sqlite"] + CREATE_FIRST_TARIFF_TRIGGERS["sqlite"],
        "postgresql": (
            postgres_metadata_ddl(*postgres_objects, before=LOCK_CATALOGUE_GUARD_TRANSITION),
        ),
    }
    drop = {"sqlite": DROP_ACCOUNT_TARIFF_TRIGGER["sqlite"] + DROP_FIRST_TARIFF_TRIGGERS["sqlite"]}
    retry = {
        "postgresql": (
            postgres_metadata_ddl(
                *postgres_objects,
                existing_catalogue=True,
                before=f"""IF billing_schema IS NULL OR pg_catalog.to_regprocedure(
                    pg_catalog.quote_ident(billing_schema) || '.{LOCK_BILLING_WRITES}()'
                ) IS NOT NULL OR EXISTS (
                    SELECT 1 FROM pg_catalog.pg_trigger
                    WHERE tgrelid = pg_catalog.to_regclass(pg_catalog.format('%I.payment_accounts', billing_schema))
                        AND tgname = '{TRIGGER_NAME}'
                ) THEN RETURN; END IF;
                {LOCK_CATALOGUE_GUARD_TRANSITION}""",
            ),
        )
    }
    register_tariff_triggers(table, create, drop, retry=retry)
