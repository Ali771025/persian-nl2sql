from __future__ import annotations


def strip_unindexed_qualifiers(
    sql: str,
    known_tables: list[str],
) -> tuple[str, bool]:
    """Remove invented prefixes only from known, unqualified indexed tables."""

    try:
        import sqlglot
        from sqlglot import exp

        statement = sqlglot.parse_one(sql, read="tsql")
    except Exception:
        return sql, False

    allowed_unqualified = {
        table.lower()
        for table in known_tables
        if table and "." not in table
    }
    cte_names = {
        cte.alias_or_name.lower()
        for cte in statement.find_all(exp.CTE)
        if cte.alias_or_name
    }
    changed = False
    for table in statement.find_all(exp.Table):
        base_name = table.name
        if (
            not base_name
            or base_name.lower() in cte_names
            or base_name.lower() not in allowed_unqualified
            or (not table.catalog and not table.db)
        ):
            continue
        table.set("catalog", None)
        table.set("db", None)
        changed = True

    if not changed:
        return sql, False
    return statement.sql(dialect="tsql"), True
