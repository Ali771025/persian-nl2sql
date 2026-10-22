from __future__ import annotations

import json
import re
from importlib.resources import files
from typing import Any

from .models import (
    EvaluationCaseResult,
    EvaluationReport,
    Principal,
    QueryRequest,
)


class DomainEvaluator:
    def __init__(self, service):
        self.service = service

    async def run(self) -> EvaluationReport:
        payload = self._load_suite()
        results: list[EvaluationCaseResult] = []
        principal = Principal(subject="evaluation", roles=["system_admin"])
        for item in payload["cases"]:
            response = await self.service.query(
                QueryRequest(question=item["question"], execute=False),
                principal=principal,
            )
            failures = self._failures(item, response)
            results.append(
                EvaluationCaseResult(
                    case_id=item["id"],
                    question=item["question"],
                    passed=not failures,
                    status=response.status,
                    recipe_id=(response.domain_plan.matched_recipe_id if response.domain_plan else None),
                    sql=response.sql,
                    parameters=response.parameters,
                    explanation_fa=response.explanation_fa,
                    validation_errors=(response.validation.errors if response.validation else []),
                    knowledge_snapshot_id=response.knowledge_snapshot_id,
                    failures=failures,
                )
            )
        passed = sum(item.passed for item in results)
        return EvaluationReport(
            suite=payload["suite"],
            ontology_version=self.service.semantic.version,
            total=len(results),
            passed=passed,
            pass_rate=round(passed / len(results), 4) if results else 0.0,
            cases=results,
        )

    @staticmethod
    def _load_suite() -> dict[str, Any]:
        path = files("nl2sql.evals").joinpath("commission100_gold.json")
        return json.loads(path.read_text(encoding="utf-8"))

    @staticmethod
    def _compact_sql(value: str | None) -> str:
        return re.sub(r"\s+", " ", value or "").strip().lower()

    def _failures(self, item: dict[str, Any], response) -> list[str]:
        failures: list[str] = []
        if response.status != item["expected_status"]:
            failures.append(f"status expected={item['expected_status']} actual={response.status}")
        recipe_id = response.domain_plan.matched_recipe_id if response.domain_plan else None
        if recipe_id != item["expected_recipe"]:
            failures.append(f"recipe expected={item['expected_recipe']} actual={recipe_id}")
        actual_tables = {
            table.lower() for table in (response.validation.referenced_tables if response.validation else [])
        }
        expected_tables = {table.lower() for table in item["expected_tables"]}
        missing_tables = sorted(expected_tables - actual_tables)
        if missing_tables:
            failures.append(f"missing tables: {', '.join(missing_tables)}")
        compact_sql = self._compact_sql(response.sql)
        for fragment in item["required_sql"]:
            if self._compact_sql(fragment) not in compact_sql:
                failures.append(f"missing SQL fragment: {fragment}")
        if response.parameters != item["expected_parameters"]:
            failures.append(f"parameters expected={item['expected_parameters']} actual={response.parameters}")
        return failures
