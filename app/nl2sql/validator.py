from __future__ import annotations

import re

from .models import ForeignKey, ValidationResult

_FORBIDDEN = (
    "INSERT",
    "UPDATE",
    "DELETE",
    "MERGE",
    "DROP",
    "ALTER",
    "CREATE",
    "TRUNCATE",
    "GRANT",
    "REVOKE",
    "DENY",
    "EXEC",
    "EXECUTE",
    "BACKUP",
    "RESTORE",
    "DBCC",
    "KILL",
    "USE",
)
_DANGEROUS_OBJECTS = (
    "OPENROWSET",
    "OPENQUERY",
    "OPENDATASOURCE",
    "BULK",
    "INFORMATION_SCHEMA",
    "WAITFOR",
    "SUSER_SNAME",
    "SYSTEM_USER",
    "CURRENT_USER",
    "HOST_NAME",
    "APP_NAME",
)


def _without_comments_and_literals(sql: str) -> str:
    value = re.sub(r"/\*.*?\*/", " ", sql, flags=re.S)
    value = re.sub(r"--[^\n]*", " ", value)
    value = re.sub(r"N?'(?:''|[^'])*'", "''", value, flags=re.I)
    return value


class TSQLValidator:
    def __init__(
        self,
        known_tables: list[str],
        strict_schema: bool = True,
        known_columns: dict[str, set[str]] | None = None,
        foreign_keys: list[ForeignKey] | None = None,
    ):
        self.known_tables = {table.lower(): table for table in known_tables}
        self.known_columns = {
            table.lower(): {column.lower() for column in columns}
            for table, columns in (known_columns or {}).items()
        }
        self.strict_schema = strict_schema
        self.foreign_keys = list(foreign_keys or [])

    def validate(self, sql: str, parameters: dict[str, object] | None = None) -> ValidationResult:
        errors: list[str] = []
        warnings: list[str] = []
        parameters = parameters or {}
        stripped = sql.strip()
        if not stripped:
            return ValidationResult(valid=False, errors=["SQL is empty"])

        sanitized = _without_comments_and_literals(stripped)
        without_trailing_terminator = sanitized.rstrip().removesuffix(";")
        if ";" in without_trailing_terminator:
            errors.append("Exactly one SQL statement is allowed")
        if not re.match(r"^\s*(?:SELECT\b|WITH\b)", sanitized, flags=re.I):
            errors.append("Only SELECT or CTE followed by SELECT is allowed")
        for keyword in _FORBIDDEN:
            if re.search(rf"\b{keyword}\b", sanitized, flags=re.I):
                errors.append(f"Forbidden SQL operation: {keyword}")
        for object_name in _DANGEROUS_OBJECTS:
            if re.search(rf"\b{object_name}\b", sanitized, flags=re.I):
                errors.append(f"Dangerous SQL object is not allowed: {object_name}")
        if re.search(r"\bSELECT\s+.*?\bINTO\b", sanitized, flags=re.I | re.S):
            errors.append("SELECT INTO is not allowed")
        if re.search(r"\b(?:xp|sp)_[A-Za-z0-9_]+", sanitized, flags=re.I):
            errors.append("System stored procedures are not allowed")
        if "@@" in sanitized or re.search(r"\b(?:sys|master|msdb|tempdb)\s*\.", sanitized, re.I):
            errors.append("System metadata access is not allowed")
        if re.search(r"\bSELECT\s+\*", sanitized, flags=re.I):
            errors.append("SELECT * is not allowed; columns must be explicit")

        placeholders = set(re.findall(r"(?<!:):([A-Za-z_][A-Za-z0-9_]*)", sanitized))
        missing_parameters = sorted(placeholders - set(parameters))
        extra_parameters = sorted(set(parameters) - placeholders)
        if missing_parameters:
            errors.append(f"Missing SQL parameters: {', '.join(missing_parameters)}")
        if extra_parameters:
            warnings.append(f"Unused SQL parameters: {', '.join(extra_parameters)}")

        referenced_tables, referenced_columns, parse_errors = self._parse_references(stripped)
        errors.extend(parse_errors)
        if self.strict_schema and not referenced_tables:
            errors.append("Query must reference at least one indexed schema table")
        if self.strict_schema and referenced_tables:
            unknown = [table for table in referenced_tables if table.lower() not in self.known_tables]
            if unknown:
                errors.append(f"Unknown tables outside the indexed schema: {', '.join(sorted(unknown))}")
            errors.extend(self._validate_columns(referenced_columns, referenced_tables))
            errors.extend(self._validate_join_grounding(stripped))

        return ValidationResult(
            valid=not errors,
            errors=list(dict.fromkeys(errors)),
            warnings=warnings,
            referenced_tables=referenced_tables,
            referenced_columns=referenced_columns,
        )

    def _parse_references(self, sql: str) -> tuple[list[str], list[str], list[str]]:
        try:
            import sqlglot
            from sqlglot import exp
        except ImportError:
            tables = re.findall(
                r"\b(?:FROM|JOIN)\s+(?:\[[^\]]+\]\.)?\[?([A-Za-z_][A-Za-z0-9_]*)\]?",
                sql,
                re.I,
            )
            return sorted(set(tables), key=str.lower), [], []

        try:
            statements = sqlglot.parse(sql, read="tsql")
        except Exception as exc:
            return [], [], [f"T-SQL parse error: {str(exc)[:300]}"]
        if len(statements) != 1:
            return [], [], ["Exactly one SQL statement is allowed"]
        statement = statements[0]
        if not isinstance(statement, exp.Select | exp.Union) and not statement.find(exp.Select):
            return [], [], ["Statement must resolve to a SELECT query"]

        forbidden_types = tuple(
            kind
            for kind in (
                getattr(exp, "Insert", None),
                getattr(exp, "Update", None),
                getattr(exp, "Delete", None),
                getattr(exp, "Create", None),
                getattr(exp, "Drop", None),
                getattr(exp, "Alter", None),
                getattr(exp, "Command", None),
                getattr(exp, "Merge", None),
            )
            if kind is not None
        )
        if forbidden_types and any(statement.find(kind) for kind in forbidden_types):
            return [], [], ["Parsed syntax tree contains a forbidden operation"]

        cte_names = {cte.alias_or_name.lower() for cte in statement.find_all(exp.CTE)}
        physical_tables = [
            table
            for table in statement.find_all(exp.Table)
            if table.name and table.name.lower() not in cte_names
        ]
        qualification_errors: list[str] = []
        for table in physical_tables:
            if table.catalog:
                qualification_errors.append(
                    f"Cross-database table qualification is not allowed: {table.sql(dialect='tsql')}"
                )
        table_names = {
            id(table): f"{table.db}.{table.name}" if table.db else table.name
            for table in physical_tables
        }
        tables = set(table_names.values())
        aliases = {
            (table.alias_or_name or table.name).lower(): table_names[id(table)]
            for table in physical_tables
        }
        output_aliases = {alias.alias.lower() for alias in statement.find_all(exp.Alias) if alias.alias}
        columns: set[str] = set()
        for column in statement.find_all(exp.Column):
            name = column.name
            qualifier = column.table.lower() if column.table else ""
            if not name or name.lower() in output_aliases:
                continue
            if qualifier in cte_names:
                continue
            table_name = aliases.get(qualifier)
            columns.add(f"{table_name}.{name}" if table_name else name)
        return (
            sorted(tables, key=str.lower),
            sorted(columns, key=str.lower),
            qualification_errors,
        )

    def _validate_columns(
        self,
        referenced_columns: list[str],
        referenced_tables: list[str],
    ) -> list[str]:
        if not self.known_columns:
            return []
        errors: list[str] = []
        available_schemas = [
            self.known_columns[table.lower()]
            for table in referenced_tables
            if table.lower() in self.known_columns and self.known_columns[table.lower()]
        ]
        for reference in referenced_columns:
            if "." in reference:
                table, column = reference.rsplit(".", 1)
                schema = self.known_columns.get(table.lower())
                if schema and column.lower() not in schema:
                    errors.append(f"Unknown column outside indexed schema: {reference}")
            elif available_schemas and not any(reference.lower() in schema for schema in available_schemas):
                errors.append(f"Unknown unqualified column outside indexed schema: {reference}")
        return errors

    def _validate_join_grounding(self, sql: str) -> list[str]:
        """Reject physical-table equijoins that are absent from the indexed FK graph."""

        if not self.foreign_keys:
            return []
        try:
            import sqlglot
            from sqlglot import exp

            statement = sqlglot.parse_one(sql, read="tsql")
        except Exception:
            return []

        cte_names = {cte.alias_or_name.lower() for cte in statement.find_all(exp.CTE)}
        aliases: dict[str, str] = {}
        for table in statement.find_all(exp.Table):
            if not table.name or table.name.lower() in cte_names:
                continue
            name = f"{table.db}.{table.name}" if table.db else table.name
            aliases[(table.alias_or_name or table.name).lower()] = name

        documented = {
            (
                edge.child_table.lower().split(".")[-1],
                edge.child_column.lower(),
                edge.parent_table.lower().split(".")[-1],
                edge.parent_column.lower(),
            )
            for edge in self.foreign_keys
        }
        errors: list[str] = []
        for join in statement.find_all(exp.Join):
            condition = join.args.get("on")
            if condition is None:
                continue
            for equality in condition.find_all(exp.EQ):
                left, right = equality.left, equality.right
                if not isinstance(left, exp.Column) or not isinstance(right, exp.Column):
                    continue
                left_table = aliases.get(left.table.lower()) if left.table else None
                right_table = aliases.get(right.table.lower()) if right.table else None
                if not left_table or not right_table or left_table.lower() == right_table.lower():
                    continue
                candidate = (
                    left_table.lower().split(".")[-1],
                    left.name.lower(),
                    right_table.lower().split(".")[-1],
                    right.name.lower(),
                )
                reverse = (candidate[2], candidate[3], candidate[0], candidate[1])
                if candidate not in documented and reverse not in documented:
                    errors.append(
                        "Undocumented join outside indexed FK graph: "
                        f"{left.sql(dialect='tsql')} = {right.sql(dialect='tsql')}"
                    )
        return errors
