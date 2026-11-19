"""Shared database constraints for billing tables."""

from sqlalchemy import CheckConstraint

FIELD_DOCUMENTATION_TABLE = "tariff_field_documentation"
# Match the whitespace rejected by str.strip() for tariff field names.
FIELD_WHITESPACE = (
    " \t\n\r\v\f\x1c\x1d\x1e\x1f\x85\xa0\u1680"
    "\u2000\u2001\u2002\u2003\u2004\u2005\u2006\u2007\u2008\u2009\u200a"
    "\u2028\u2029\u202f\u205f\u3000"
)
FIELD_WHITESPACE_HEX = "|".join(character.encode("utf-8").hex() for character in FIELD_WHITESPACE)


class BillingTextCheck(CheckConstraint):
    """Preserve a textual billing check's dialect rule in metadata copies."""

    def _copy(self, *, target_table=None, **_):
        # These checks use literal SQL and have no column objects to rebind to the copy.
        copied = type(self)(
            self.sqltext,
            name=self.name,
            initially=self.initially,
            deferrable=self.deferrable,
            table=target_table,
            comment=self.comment,
            _create_rule=self._create_rule,
            _autoattach=False,
            _type_bound=self._type_bound,
            **self.dialect_kwargs,
        )
        copied._ddl_if = self._ddl_if
        return self._schema_item_copy(copied)


def billing_uuid_check(column="id", *, nullable=False):
    canonical = (
        f"typeof({column}) = 'text' AND length({column}) = 32 "
        f"AND instr({column}, char(0)) = 0 AND {column} NOT GLOB '*[^0-9a-f]*'"
    )
    constraint = f"{column} IS NULL OR ({canonical})" if nullable else canonical
    return BillingTextCheck(constraint, name=f"{column}_canonical_uuid").ddl_if(dialect="sqlite")


def billing_text_check(column):
    return BillingTextCheck(
        f"{column} IS NULL OR (typeof({column}) = 'text' AND instr({column}, char(0)) = 0)",
        name=f"{column}_storage",
    ).ddl_if(dialect="sqlite")


def field_documentation_checks():
    return (
        billing_uuid_check(),
        *(billing_text_check(column) for column in ("field_name", "language_code", "comment")),
        *(
            BillingTextCheck(condition, name=f"{column}_nonblank").ddl_if(dialect=dialect)
            for column in ("field_name", "language_code")
            for dialect, condition in (
                ("sqlite", f"trim({column}, '{FIELD_WHITESPACE}') <> ''"),
                # Keep the DDL representable in PostgreSQL databases with non-UTF-8 encodings.
                (
                    "postgresql",
                    f"pg_catalog.encode(pg_catalog.convert_to({column}, 'UTF8'), 'hex') "
                    f"COLLATE pg_catalog.\"C\" !~ '^({FIELD_WHITESPACE_HEX})*$'",
                ),
            )
        ),
    )
