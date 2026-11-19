"""Database definitions shared by revision 0021 and the ORM metadata."""

from datetime import UTC, date, datetime, time, timedelta
from functools import partial

from asyncpg import Connection as AsyncpgConnection
from sqlalchemy import DDL, DateTime, event
from sqlalchemy.dialects import postgresql
from sqlalchemy.engine import Engine
from sqlalchemy.types import TypeDecorator

PUBLICATION_UNIQUE = "uq_tariff_plans_type_published_at"
METADATA_CATALOGUE_RELATION = "__metadata_tariff_relation__"
POSTGRES_IDENTIFIERS = postgresql.dialect(paramstyle="named").identifier_preparer
UTC_TIMESTAMP_PATTERN = (
    "[0-9]" * 4
    + "-"
    + "-".join(["[0-9]" * 2] * 2)
    + " "
    + ":".join(["[0-9]" * 2] * 3)
    + "."
    + "[0-9]" * 6
)
POSTGRES_MIN_TIMESTAMP = "TIMESTAMPTZ '0001-01-01 00:00:00+00'"
POSTGRES_MAX_TIMESTAMP = "TIMESTAMPTZ '9999-12-31 23:59:59.999999+00'"
PUBLICATION_STORAGE_CHECKS = {
    "sqlite": (
        "published_at_utc",
        "typeof(published_at) = 'text' AND length(published_at) = 26 "
        "AND instr(published_at, char(0)) = 0 "
        f"AND published_at GLOB '{UTC_TIMESTAMP_PATTERN}' "
        "AND substr(published_at, 1, 4) <> '0000' "
        "AND datetime(substr(published_at, 1, 19), '+0 seconds') IS substr(published_at, 1, 19)",
    ),
    "postgresql": (
        "published_at_range",
        f"published_at BETWEEN {POSTGRES_MIN_TIMESTAMP} AND {POSTGRES_MAX_TIMESTAMP}",
    ),
}
TRIGGER_NAME = "assign_default_account_tariffs"
TIME_DIGITS = "[0-9][0-9]"
SQLITE_CLOCK_PATTERNS = (
    TIME_DIGITS,
    TIME_DIGITS * 2,
    TIME_DIGITS * 3,
    ":".join([TIME_DIGITS] * 2),
    ":".join([TIME_DIGITS] * 3),
)


def normalize_utc(value):
    if value is None:
        return None
    if value.utcoffset() is None:
        raise ValueError("Timezone-aware datetime required")
    return value.astimezone(UTC)


def stored_utc(value):
    if value is None:
        return None
    return value.replace(tzinfo=UTC) if value.utcoffset() is None else value.astimezone(UTC)


class _FiniteTimestamp(datetime):
    """A mapped timestamp whose endpoints are real instants, including in tuple binds."""


POSTGRES_EPOCH = datetime(2000, 1, 1, tzinfo=UTC)
MICROSECOND = timedelta(microseconds=1)
INFINITE_TIMESTAMPS = {datetime.min: -(1 << 63), datetime.max: (1 << 63) - 1}
INFINITE_BY_MICROSECOND = {
    (bound.replace(tzinfo=UTC) - POSTGRES_EPOCH) // MICROSECOND: infinity
    for bound, infinity in INFINITE_TIMESTAMPS.items()
}
INFINITE_BY_VALUE = {infinity: bound for bound, infinity in INFINITE_TIMESTAMPS.items()}


def encode_postgres_timestamp(value):
    if not isinstance(value, datetime):
        if not isinstance(value, date):
            raise TypeError("PostgreSQL timestamps require a datetime or date")
        value = datetime.combine(value, time.min, tzinfo=datetime.now(UTC).astimezone().tzinfo)
    sentinel = INFINITE_TIMESTAMPS.get(value)
    if sentinel is not None:
        return (sentinel,)
    microseconds = (value.astimezone(UTC) - POSTGRES_EPOCH) // MICROSECOND
    if isinstance(value, _FiniteTimestamp):
        return (microseconds,)
    # Match asyncpg's existing behavior for all other timestamp types in the API.
    return (INFINITE_BY_MICROSECOND.get(microseconds, microseconds),)


def decode_postgres_timestamp(value):
    (microseconds,) = value
    if microseconds in INFINITE_BY_VALUE:
        return INFINITE_BY_VALUE[microseconds]
    return POSTGRES_EPOCH + timedelta(microseconds=microseconds)


@event.listens_for(Engine, "connect")
def configure_postgres_timestamp_codec(dbapi_connection, _):
    if isinstance(getattr(dbapi_connection, "driver_connection", None), AsyncpgConnection):
        dbapi_connection.run_async(
            lambda driver: driver.set_type_codec(
                "timestamptz",
                schema="pg_catalog",
                format="tuple",
                encoder=encode_postgres_timestamp,
                decoder=decode_postgres_timestamp,
            )
        )


class PostgreSQLExactTimestamp(TypeDecorator):
    """Keep finite endpoints without special SQL expressions in filters."""

    impl = DateTime(timezone=True)
    cache_ok = True

    def process_bind_param(self, value, _dialect):
        normalized = normalize_utc(value)
        return (
            None
            if normalized is None
            else _FiniteTimestamp.combine(normalized.date(), normalized.timetz())
        )

    def process_result_value(self, value, _dialect):
        return stored_utc(value)


def sqlite_clock_matches(column):
    return "(" + " OR ".join(f"{column} GLOB '{shape}'" for shape in SQLITE_CLOCK_PATTERNS) + ")"


def sqlite_publication_cutoff(column):
    # Split the local clock and its UTC offset before converting either to integers.
    # SQLite's date functions round fractional seconds, so only pass whole seconds.
    # Python's ISO parser interprets offsets below one second as UTC.
    return f"""
        CASE WHEN typeof({column}) <> 'text' OR instr({column}, char(0)) <> 0 THEN NULL
        WHEN {column} GLOB '{UTC_TIMESTAMP_PATTERN}'
            AND datetime(substr({column}, 1, 19), '+0 seconds') IS substr({column}, 1, 19)
            AND substr({column}, 1, 4) <> '0000' THEN {column}
        ELSE (
            WITH input(value, date_size) AS (
                SELECT {column}, CASE WHEN substr({column}, 5, 1) = '-' THEN 10 ELSE 8 END
            ), parts(day, tail) AS (
                SELECT CASE WHEN date_size = 10 THEN substr(value, 1, 10)
                    ELSE substr(value, 1, 4) || '-' || substr(value, 5, 2) || '-' || substr(value, 7, 2) END,
                    substr(value, date_size + 2)
                FROM input
                WHERE length(value) = date_size
                    OR (length(value) > date_size + 1 AND substr(value, date_size + 1, 1) IN (' ', 'T'))
            ), offsets AS (
                SELECT *, max(instr(tail, '+'), instr(tail, '-')) AS sign_at FROM parts
            ), tokens AS (
                SELECT day, tail = '' AS date_only,
                    replace(CASE WHEN sign_at > 0 THEN substr(tail, 1, sign_at - 1)
                        WHEN substr(tail, -1) = 'Z' THEN substr(tail, 1, length(tail) - 1) ELSE tail END, ',', '.') AS clock,
                    replace(CASE WHEN sign_at > 0 THEN substr(tail, sign_at) ELSE '+00' END, ',', '.') AS zone
                FROM offsets
            ), digits AS (
                SELECT *, substr(zone, 1, 1) AS sign,
                    substr(clock, 1, instr(clock || '.', '.') - 1) AS local_time,
                    substr(zone, 2, instr(zone || '.', '.') - 2) AS offset_time,
                    substr(clock, instr(clock || '.', '.') + 1) AS local_fraction,
                    substr(zone, instr(zone || '.', '.') + 1) AS offset_fraction
                FROM tokens
            ), padded AS (
                SELECT day, sign, replace(local_time, ':', '') || '000000' AS local,
                    replace(offset_time, ':', '') || '000000' AS offset,
                    substr(local_fraction || '000000', 1, 6) AS micros,
                    substr(offset_fraction || '000000', 1, 6) AS offset_micros
                FROM digits
                WHERE date(day, '+0 days') IS day AND substr(day, 1, 4) <> '0000'
                    AND (date_only OR {sqlite_clock_matches("local_time")}) AND {sqlite_clock_matches("offset_time")}
                    AND local_fraction NOT GLOB '*[^0-9]*' AND offset_fraction NOT GLOB '*[^0-9]*'
                    AND (instr(clock, '.') = 0 OR local_fraction <> '')
                    AND (instr(zone, '.') = 0 OR offset_fraction <> '')
            ), utc AS (
                SELECT 1000000 * CAST(strftime('%s', day || ' ' || substr(local, 1, 2) || ':' || substr(local, 3, 2) || ':' || substr(local, 5, 2)) AS INTEGER)
                    + CAST(micros AS INTEGER) + (CASE sign WHEN '-' THEN 1 ELSE -1 END) * (
                        1000000 * (substr(offset, 1, 2) * 3600 + substr(offset, 3, 2) * 60 + substr(offset, 5, 2))
                        + CASE WHEN substr(offset, 1, 6) = '000000' THEN 0 ELSE CAST(offset_micros AS INTEGER) END
                    ) AS instant
                FROM padded
                WHERE substr(local, 1, 2) < '24' AND substr(local, 3, 2) < '60' AND substr(local, 5, 2) < '60'
                    AND substr(offset, 1, 2) < '24'
                    AND substr(offset, 3, 2) < '60' AND substr(offset, 5, 2) < '60'
            ), fractions AS (
                SELECT instant, (instant % 1000000 + 1000000) % 1000000 AS fraction FROM utc
            )
            SELECT datetime((instant - fraction) / 1000000, 'unixepoch') || '.' || printf('%06d', fraction)
            FROM fractions
            WHERE instant BETWEEN -62135596800000000 AND 253402300799999999
        ) END
    """


def insert_initial_tariffs(cutoff, *, accounts=None, account_id="NEW.id", schema=""):
    source = f"{accounts} AS account CROSS JOIN " if accounts else ""
    if accounts:
        account_id = "account.id"
    return f"""
        INSERT INTO {schema}payment_account_tariff_plans (payment_account_id, tariff_plan_type_id, tariff_plan_id)
        SELECT {account_id}, plan_type.id, plan.id
        FROM {source}{schema}tariff_plan_types AS plan_type
        JOIN {schema}tariff_plans AS plan ON plan.id = (
            SELECT candidate.id FROM {schema}tariff_plans AS candidate
            WHERE candidate.tariff_plan_type_id = plan_type.id AND candidate.published_at <= ({cutoff})
            ORDER BY candidate.published_at DESC
            LIMIT 1
        )
    """


def postgres_account_tariff_body(*, account_id="NEW.id", created_at="NEW.created_at", result="NEW"):
    cutoff = f"GREATEST({created_at}, {POSTGRES_MIN_TIMESTAMP})"
    return f"""BEGIN
        IF isfinite({created_at})
            AND {created_at} NOT BETWEEN {POSTGRES_MIN_TIMESTAMP} AND {POSTGRES_MAX_TIMESTAMP} THEN
            RAISE EXCEPTION 'Invalid creation timestamp for payment account' USING ERRCODE = '23514';
        END IF;
        {insert_initial_tariffs(cutoff, account_id=account_id)};
        RETURN {result};
    END;"""


SQLITE_ACCOUNT_CUTOFF = sqlite_publication_cutoff("NEW.created_at")
CREATE_ACCOUNT_TARIFF_TRIGGER = {
    "sqlite": (
        f"""CREATE TRIGGER {TRIGGER_NAME}
        AFTER INSERT ON payment_accounts FOR EACH ROW
        BEGIN
            SELECT RAISE(ABORT, 'Invalid creation timestamp for payment account')
                WHERE ({SQLITE_ACCOUNT_CUTOFF}) IS NULL;
            {insert_initial_tariffs(SQLITE_ACCOUNT_CUTOFF)};
        END""",
    ),
}
DROP_ACCOUNT_TARIFF_TRIGGER = {
    "sqlite": (f"DROP TRIGGER {TRIGGER_NAME}",),
}


def postgres_metadata_relation(table, connection, *, creating):
    schema = connection.schema_for_object(table)
    schema_map = connection.get_execution_options().get("schema_translate_map") or {}
    if table.schema in schema_map:
        schema = schema or connection.dialect.default_schema_name
    name = POSTGRES_IDENTIFIERS.format_table(table, use_schema=False)
    if schema:
        name = f"{POSTGRES_IDENTIFIERS.quote_schema(schema)}.{name}"
    # Keep physical names outside the dollar-quoted DO block's SQL syntax.
    relation = f"pg_catalog.convert_from(pg_catalog.decode('{name.encode().hex()}', 'hex'), 'UTF8')"
    if creating and not schema:
        return "pg_catalog.quote_ident(pg_catalog.current_schema()) || '.' || " + relation
    return relation


def run_tariff_ddl(
    table, statements, _metadata, connection, *, tables, _ddl_runner, retry=None, **_
):
    creating = retry is not None and table in tables
    if table not in tables:
        # SQLAlchemy reports only newly created tables in this event.
        requested = _ddl_runner.tables
        if retry is None or (requested is not None and table not in requested):
            return
        statements = retry
    relation = postgres_metadata_relation(table, connection, creating=creating)
    for sql in statements.get(connection.dialect.name, ()):
        sql = sql.replace(METADATA_CATALOGUE_RELATION, relation)
        connection.execute(DDL(sql.replace("%", "%%")))


def register_tariff_triggers(table, create, drop, *, retry):
    # All participating tables must exist when their triggers are installed or removed.
    event.listen(
        table.metadata,
        "after_create",
        partial(run_tariff_ddl, table, create, retry=retry),
    )
    event.listen(
        table.metadata,
        "before_drop",
        partial(run_tariff_ddl, table, drop),
    )
