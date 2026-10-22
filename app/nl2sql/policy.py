from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .semantic import Commission100SemanticLayer


class RolePolicy:
    """Versioned, fail-closed table and column policy for generic schemas."""

    def __init__(
        self,
        path: Path | None = None,
        *,
        fallback_semantic: Commission100SemanticLayer | None = None,
    ):
        self.version = "commission100-fallback"
        self._roles: dict[str, dict[str, Any]] = {}
        self._global_sensitive: set[str] = set()
        self._mask_patterns: list[str] = []
        self._fallback = fallback_semantic
        if path is not None:
            payload = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(payload, dict) or int(payload.get("version", 0)) != 1:
                raise ValueError("Role policy must be a JSON object with version=1")
            roles = payload.get("roles")
            if not isinstance(roles, dict) or not roles:
                raise ValueError("Role policy must define at least one role")
            self.version = str(payload["version"])
            self._roles = {
                str(role): value
                for role, value in roles.items()
                if isinstance(value, dict)
            }
            self._global_sensitive = {
                str(value).lower() for value in payload.get("sensitive_columns", [])
            }
            self._mask_patterns = [str(value) for value in payload.get("mask_patterns", [])]

    @property
    def configured(self) -> bool:
        return bool(self._roles)

    @property
    def sensitive_columns(self) -> set[str]:
        values = set(self._global_sensitive)
        if self._fallback is not None:
            values.update(self._fallback.sensitive_columns)
        for role in self._roles.values():
            values.update(str(value).lower() for value in role.get("sensitive_columns", []))
        return values

    @property
    def mask_patterns(self) -> list[str]:
        if self._mask_patterns:
            return list(self._mask_patterns)
        return self._fallback.mask_patterns if self._fallback is not None else []

    def allowed_tables(self, roles: list[str]) -> set[str]:
        if not self._roles and self._fallback is not None:
            return self._fallback.allowed_tables(roles)
        output: set[str] = set()
        for role in roles:
            policy = self._roles.get(role, {})
            tables = policy.get("tables", [])
            if isinstance(tables, list):
                output.update(str(value) for value in tables)
        return output

    def scope_column(self, roles: list[str], table: str, claim: str) -> str | None:
        table_lower = table.lower()
        for role in roles:
            scopes = self._roles.get(role, {}).get("row_scopes", {})
            if not isinstance(scopes, dict):
                continue
            for configured_table, mapping in scopes.items():
                if (
                    configured_table != "*"
                    and configured_table.lower() != table_lower
                    and configured_table.lower().split(".")[-1] != table_lower.split(".")[-1]
                ):
                    continue
                if isinstance(mapping, dict) and claim in mapping:
                    return str(mapping[claim])
        return None

    @staticmethod
    def table_allowed(table: str, allowed: set[str]) -> bool:
        lowered = table.lower()
        allowed_lower = {value.lower() for value in allowed}
        return (
            "*" in allowed_lower
            or lowered in allowed_lower
            or f"dbo.{lowered}" in allowed_lower
            or any(value.endswith(f".{lowered}") for value in allowed_lower)
        )
