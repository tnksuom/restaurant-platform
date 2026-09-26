"""SQL helpers used by migrations."""

FUNCTION_SQL = """
CREATE OR REPLACE FUNCTION core_prevent_modification() RETURNS trigger AS $$
BEGIN
    RAISE EXCEPTION 'Table % is append-only: % is not allowed', TG_TABLE_NAME, TG_OP
        USING ERRCODE = 'integrity_constraint_violation';
END;
$$ LANGUAGE plpgsql;
"""

DROP_FUNCTION_SQL = "DROP FUNCTION IF EXISTS core_prevent_modification();"


def append_only_trigger_sql(table):
    return (
        f"CREATE TRIGGER {table}_append_only BEFORE UPDATE OR DELETE ON {table} "
        f"FOR EACH ROW EXECUTE FUNCTION core_prevent_modification();"
    )


def drop_append_only_trigger_sql(table):
    return f"DROP TRIGGER IF EXISTS {table}_append_only ON {table};"
