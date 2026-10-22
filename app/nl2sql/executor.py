from __future__ import annotations

import datetime as dt
import decimal
import re
import time
import uuid
from typing import Any

from .models import ExecutionResult


def _compile_named_parameters(sql: str, parameters: dict[str, Any]) -> tuple[str, list[Any]]:
    values: list[Any] = []
    output: list[str] = []
    index = 0
    state = "normal"
    while index < len(sql):
        char = sql[index]
        following = sql[index + 1] if index + 1 < len(sql) else ""

        if state == "normal":
            if char == "'":
                state = "literal"
            elif char == "[":
                state = "identifier"
            elif char == "-" and following == "-":
                state = "line_comment"
            elif char == "/" and following == "*":
                state = "block_comment"
            elif char == ":" and following and (following.isalpha() or following == "_"):
                match = re.match(r":([A-Za-z_][A-Za-z0-9_]*)", sql[index:])
                assert match is not None
                name = match.group(1)
                if name not in parameters:
                    raise ValueError(f"Missing parameter: {name}")
                values.append(parameters[name])
                output.append("?")
                index += len(match.group(0))
                continue
        elif state == "literal" and char == "'":
            if following == "'":
                output.extend((char, following))
                index += 2
                continue
            state = "normal"
        elif state == "identifier" and char == "]":
            state = "normal"
        elif state == "line_comment" and char == "\n":
            state = "normal"
        elif state == "block_comment" and char == "*" and following == "/":
            output.extend((char, following))
            index += 2
            state = "normal"
            continue

        output.append(char)
        index += 1
    return "".join(output), values


def _json_safe(value: Any) -> Any:
    if value is None or isinstance(value, str | int | float | bool):
        return value
    if isinstance(value, decimal.Decimal):
        return float(value)
    if isinstance(value, dt.date | dt.datetime | dt.time | uuid.UUID):
        return str(value)
    if isinstance(value, bytes):
        return value.hex()
    return str(value)


class ReadOnlySQLServerExecutor:
    """Executes one validated query with row/time bounds and always rolls back."""

    def __init__(
        self,
        database_url: str,
        max_rows: int = 200,
        timeout_seconds: int = 15,
        lock_timeout_ms: int = 5_000,
    ):
        if not database_url:
            raise ValueError("NL2SQL_DATABASE_URL is required when execution is enabled")
        self.database_url = database_url
        self.max_rows = max_rows
        self.timeout_seconds = timeout_seconds
        self.lock_timeout_ms = lock_timeout_ms
        self._engine = None

    def _get_engine(self):
        if self._engine is None:
            try:
                from sqlalchemy import create_engine
            except ImportError as exc:
                raise RuntimeError("SQLAlchemy and pyodbc are required for SQL Server execution") from exc
            self._engine = create_engine(
                self.database_url,
                pool_pre_ping=True,
                pool_size=5,
                max_overflow=5,
                pool_recycle=900,
            )
        return self._engine

    def execute(self, sql: str, parameters: dict[str, Any]) -> ExecutionResult:
        compiled_sql, values = _compile_named_parameters(sql, parameters)
        started = time.perf_counter()
        raw_connection = self._get_engine().raw_connection()
        cursor = None
        try:
            cursor = raw_connection.cursor()
            if hasattr(cursor, "timeout"):
                cursor.timeout = self.timeout_seconds
            cursor.execute(f"SET LOCK_TIMEOUT {int(self.lock_timeout_ms)}")
            cursor.execute(f"SET ROWCOUNT {int(self.max_rows + 1)}")
            cursor.execute(compiled_sql, values)
            description = cursor.description or []
            columns = [str(item[0]) for item in description]
            raw_rows = cursor.fetchmany(self.max_rows + 1) if columns else []
            truncated = len(raw_rows) > self.max_rows
            raw_rows = raw_rows[: self.max_rows]
            rows = [
                {column: _json_safe(value) for column, value in zip(columns, row, strict=False)}
                for row in raw_rows
            ]
            return ExecutionResult(
                columns=columns,
                rows=rows,
                row_count=len(rows),
                truncated=truncated,
                duration_ms=int((time.perf_counter() - started) * 1000),
            )
        finally:
            try:
                if cursor is not None:
                    cursor.execute("SET ROWCOUNT 0")
            except Exception:
                pass
            try:
                raw_connection.rollback()
            finally:
                raw_connection.close()

    def ping(self) -> bool:
        from sqlalchemy import text

        with self._get_engine().connect() as connection:
            return connection.execute(text("SELECT 1")).scalar_one() == 1

    def close(self) -> None:
        if self._engine is not None:
            self._engine.dispose()
            self._engine = None
