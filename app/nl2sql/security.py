from __future__ import annotations

from typing import Any

from .config import Settings
from .models import (
    AccessDecision,
    ExecutionResult,
    Principal,
    SQLDraft,
    SQLParameter,
    ValidationResult,
)
from .policy import RolePolicy
from .semantic import Commission100SemanticLayer


class AuthenticationError(RuntimeError):
    pass


class JWTAuthenticator:
    """Verifies internal JWTs locally; it never calls a remote JWKS endpoint."""

    def __init__(self, settings: Settings):
        self.settings = settings

    def authenticate(self, token: str) -> Principal:
        try:
            import jwt
        except ImportError as exc:
            raise AuthenticationError("PyJWT is required when JWT authentication is enabled") from exc
        key = (
            self.settings.jwt_secret
            if self.settings.jwt_algorithm == "HS256"
            else self.settings.jwt_public_key.replace("\\n", "\n")
        )
        if not key:
            raise AuthenticationError("JWT verification key is not configured")
        options = {
            "require": ["exp", "sub"],
            "verify_aud": bool(self.settings.jwt_audience),
        }
        kwargs: dict[str, Any] = {
            "algorithms": [self.settings.jwt_algorithm],
            "options": options,
        }
        if self.settings.jwt_issuer:
            kwargs["issuer"] = self.settings.jwt_issuer
        if self.settings.jwt_audience:
            kwargs["audience"] = self.settings.jwt_audience
        try:
            claims = jwt.decode(token, key, **kwargs)
        except Exception as exc:
            raise AuthenticationError("JWT is invalid or expired") from exc
        roles = claims.get("roles", claims.get("role", []))
        if isinstance(roles, str):
            roles = [roles]
        if not isinstance(roles, list):
            roles = []
        return Principal(
            subject=str(claims["sub"]),
            roles=[str(role) for role in roles],
            tenant_id=str(claims["tenant_id"]) if claims.get("tenant_id") else None,
            city=str(claims["city"]) if claims.get("city") else None,
            area=str(claims["area"]) if claims.get("area") else None,
        )


class AccessController:
    def __init__(self, policy: RolePolicy | Commission100SemanticLayer):
        self.policy = (
            policy
            if isinstance(policy, RolePolicy)
            else RolePolicy(fallback_semantic=policy)
        )

    def preflight(self, required_tables: list[str], principal: Principal) -> AccessDecision:
        allowed_tables = self.policy.allowed_tables(principal.roles)
        if not allowed_tables:
            return AccessDecision(
                allowed=False,
                errors=["Principal has no recognized data-access role"],
            )
        if "*" in allowed_tables:
            return AccessDecision(allowed=True)
        unauthorized = sorted(
            table
            for table in required_tables
            if not self.policy.table_allowed(table, allowed_tables)
        )
        if unauthorized:
            return AccessDecision(
                allowed=False,
                errors=[f"Role cannot access tables: {', '.join(unauthorized)}"],
            )
        return AccessDecision(allowed=True)

    def enforce(
        self,
        draft: SQLDraft,
        validation: ValidationResult,
        principal: Principal,
        known_columns: dict[str, set[str]],
    ) -> tuple[SQLDraft, AccessDecision]:
        errors: list[str] = []
        allowed_tables = self.policy.allowed_tables(principal.roles)
        if not allowed_tables:
            errors.append("Principal has no recognized data-access role")
        elif "*" not in allowed_tables:
            unauthorized = sorted(
                table
                for table in validation.referenced_tables
                if not self.policy.table_allowed(table, allowed_tables)
            )
            if unauthorized:
                errors.append(f"Role cannot access tables: {', '.join(unauthorized)}")

        sensitive_columns = self.policy.sensitive_columns
        sensitive_names = {value.rsplit(".", 1)[-1] for value in sensitive_columns}
        sensitive_references = sorted(
            reference
            for reference in validation.referenced_columns
            if reference.lower() in sensitive_columns
            or reference.rsplit(".", 1)[-1].lower() in sensitive_names
        )
        if sensitive_references:
            errors.append(f"Sensitive columns cannot be selected: {', '.join(sensitive_references)}")
        if errors:
            return draft, AccessDecision(allowed=False, errors=errors)

        try:
            scoped_draft, applied = self._apply_row_scopes(
                draft,
                principal,
                known_columns,
            )
        except Exception as exc:
            return draft, AccessDecision(
                allowed=False,
                errors=[f"Unable to enforce row scope: {type(exc).__name__}"],
            )
        return scoped_draft, AccessDecision(
            allowed=True,
            applied_scopes=applied,
        )

    def _apply_row_scopes(
        self,
        draft: SQLDraft,
        principal: Principal,
        known_columns: dict[str, set[str]],
    ) -> tuple[SQLDraft, dict[str, str]]:
        scopes = {
            name: value
            for name, value in (
                ("tenantid", principal.tenant_id),
                ("city", principal.city),
                ("area", principal.area),
            )
            if value is not None
        }
        if not scopes:
            return draft, {}
        try:
            import sqlglot
            from sqlglot import exp
        except ImportError as exc:
            raise RuntimeError("sqlglot is required for ABAC row-scope enforcement") from exc

        statement = sqlglot.parse_one(draft.sql, read="tsql")
        table_nodes = list(statement.find_all(exp.Table))
        predicates_by_select: dict[int, tuple[Any, list[Any]]] = {}
        applied: dict[str, str] = {}
        for table in table_nodes:
            qualified_name = f"{table.db}.{table.name}" if table.db else table.name
            schema = known_columns.get(
                qualified_name.lower(),
                known_columns.get(table.name.lower(), set()),
            )
            select = AccessController._nearest_select(table, exp.Select)
            if select is None:
                continue
            key = id(select)
            predicates_by_select.setdefault(key, (select, []))
            alias = table.alias_or_name or table.name
            for scope_name, scope_value in scopes.items():
                configured_column = self.policy.scope_column(
                    principal.roles,
                    qualified_name,
                    scope_name,
                )
                if self.policy.configured and configured_column is None:
                    continue
                column_name = configured_column or AccessController._scope_column(scope_name)
                if column_name.lower() not in schema:
                    continue
                parameter_name = f"scope_{scope_name}"
                predicate = sqlglot.parse_one(
                    f"{alias}.{column_name} = :{parameter_name}",
                    read="tsql",
                )
                predicates_by_select[key][1].append(predicate)
                applied[scope_name] = str(scope_value)

        missing_scopes = sorted(set(scopes) - set(applied))
        if missing_scopes:
            raise RuntimeError("query has no documented column for scopes: " + ", ".join(missing_scopes))
        for select, predicates in predicates_by_select.values():
            for predicate in predicates:
                select.where(predicate, copy=False)

        reserved = {f"scope_{name}" for name in scopes}
        parameters = [item for item in draft.parameters if item.name not in reserved]
        parameters.extend(
            SQLParameter(name=f"scope_{name}", value=value)
            for name, value in scopes.items()
            if name in applied
        )
        return (
            draft.model_copy(
                update={
                    "sql": statement.sql(dialect="tsql"),
                    "parameters": parameters,
                }
            ),
            applied,
        )

    @staticmethod
    def _scope_column(scope_name: str) -> str:
        return {"tenantid": "TenantID", "city": "City", "area": "Area"}[scope_name]

    @staticmethod
    def _nearest_select(node: Any, select_type: type) -> Any | None:
        current = node.parent
        while current is not None:
            if isinstance(current, select_type):
                return current
            current = current.parent
        return None


class SensitiveDataMasker:
    def __init__(self, patterns: list[str]):
        self.patterns = [value.lower() for value in patterns]

    def mask(self, result: ExecutionResult) -> ExecutionResult:
        sensitive = {
            column for column in result.columns if any(pattern in column.lower() for pattern in self.patterns)
        }
        if not sensitive:
            return result
        rows = [
            {
                column: self._mask_value(value) if column in sensitive else value
                for column, value in row.items()
            }
            for row in result.rows
        ]
        return result.model_copy(update={"rows": rows})

    @staticmethod
    def _mask_value(value: Any) -> Any:
        if value is None:
            return None
        text = str(value)
        if "@" in text:
            _, domain = text.rsplit("@", 1)
            return f"***@{domain}"
        return f"***{text[-4:]}" if len(text) > 4 else "***"
