from __future__ import annotations

import json
import re
from importlib.resources import files
from typing import Any

from .models import DomainPlan, QueryAnalysis, SQLDraft, SQLParameter
from .normalization import normalize_persian, tokenize

_SEMANTIC_STOPWORDS = {
    "از",
    "است",
    "به",
    "با",
    "بر",
    "برای",
    "در",
    "را",
    "شده",
    "شکل",
    "صورت",
    "که",
    "و",
    "های",
}


class Commission100SemanticLayer:
    def __init__(self, payload: dict[str, Any] | None = None):
        if payload is None:
            path = files("nl2sql.domain").joinpath("commission100.json")
            payload = json.loads(path.read_text(encoding="utf-8"))
        self.payload = payload
        self.version = str(payload["version"])
        self.domain = str(payload["domain"])

    def plan(self, question: str, analysis: QueryAnalysis) -> DomainPlan:
        normalized = normalize_persian(question).lower()
        concepts = [item for item in self.payload["concepts"] if self._matches(normalized, item["synonyms"])]
        if (
            any(item["id"] == "commission_case" for item in concepts)
            and any(item["id"] == "foul" for item in concepts)
            and "وضعیت خلاف" in normalized
            and not any(
                term in normalized
                for term in ("نوع خلاف", "تخلف", "تخلفات", "طبقه وقوع خلاف")
            )
        ):
            concepts = [item for item in concepts if item["id"] != "foul"]
        metrics = [item for item in self.payload["metrics"] if self._matches(normalized, item["synonyms"])]
        rules = [
            item for item in self.payload["business_rules"] if self._matches(normalized, item["triggers"])
        ]

        tables = list(dict.fromkeys(table for item in concepts + metrics for table in item["tables"]))
        columns = list(dict.fromkeys(column for item in concepts + metrics for column in item["columns"]))
        recipe = self.match_recipe(
            question,
            available_tables=set(tables) | set(analysis.table_mentions),
            dry_run=True,
        )
        if recipe:
            tables = list(dict.fromkeys(tables + recipe["required_tables"]))
            columns = list(dict.fromkeys(columns + recipe.get("required_columns", [])))
        expanded_parts = [analysis.normalized_question]
        expanded_parts.extend(f"concept:{item['id']} tables:{' '.join(item['tables'])}" for item in concepts)
        expanded_parts.extend(f"metric:{item['id']} definition:{item['definition']}" for item in metrics)
        expanded_parts.extend(f"rule:{item['text']}" for item in rules)
        evidence_count = len(concepts) + len(metrics) + len(rules)
        confidence = min(1.0, 0.25 + evidence_count * 0.16 + (0.2 if recipe else 0.0))
        return DomainPlan(
            domain=self.domain,
            ontology_version=self.version,
            concepts=[item["id"] for item in concepts],
            metrics=[item["id"] for item in metrics],
            required_tables=tables,
            required_columns=columns,
            business_rules=[item["text"] for item in rules],
            expanded_query="\n".join(expanded_parts),
            matched_recipe_id=recipe["id"] if recipe else None,
            confidence=confidence,
        )

    def match_recipe(
        self,
        question: str,
        available_tables: set[str],
        *,
        dry_run: bool = False,
    ) -> dict[str, Any] | None:
        normalized = normalize_persian(question).lower()
        recipes = sorted(
            enumerate(self.payload["recipes"]),
            key=lambda item: (-int(item[1].get("priority", 0)), item[0]),
        )
        for _, recipe in recipes:
            if any(normalize_persian(term).lower() in normalized for term in recipe["forbidden_terms"]):
                continue
            if not all(
                any(normalize_persian(term).lower() in normalized for term in group)
                for group in recipe["required_groups"]
            ):
                continue
            if not dry_run and not set(recipe["required_tables"]).issubset(available_tables):
                continue
            if recipe.get("parameter") and self._case_number(normalized) is None and not dry_run:
                continue
            return recipe
        return None

    def deterministic_draft(
        self,
        question: str,
        available_tables: set[str],
    ) -> SQLDraft | None:
        recipe = self.match_recipe(question, available_tables)
        if recipe is None:
            normalized = normalize_persian(question).lower()
            execution_requested = any(
                term in normalized for term in ("اجرای احکام", "اجرای حکم", "مرحله اجرا")
            )
            verdict_requested = "رای" in normalized or "رأی" in normalized
            stage_specified = any(
                term in normalized
                for term in ("بدوی", "تجدید نظر", "تجدیدنظر", "دیوان عدالت", "هم عرض")
            )
            if execution_requested and verdict_requested and not stage_specified:
                return SQLDraft(
                    sql="",
                    explanation_fa="نوع مرحله رأی برای گزارش اجرای احکام مشخص نشده است.",
                    confidence=0.99,
                    needs_clarification=True,
                    clarification_question="اجرای احکام مربوط به رأی بدوی است یا تجدیدنظر؟",
                )
            incomplete_recipe = self.match_recipe(
                question,
                available_tables,
                dry_run=True,
            )
            if incomplete_recipe and incomplete_recipe.get("parameter"):
                return SQLDraft(
                    sql="",
                    explanation_fa=(
                        "برای اجرای دقیق درخواست، شماره پرونده ماده ۱۰۰ لازم است."
                    ),
                    confidence=0.99,
                    tables_used=incomplete_recipe["required_tables"],
                    needs_clarification=True,
                    clarification_question=(
                        "شماره پرونده ماده ۱۰۰ را وارد می‌کنید؟"
                    ),
                )
            return None
        parameters: list[SQLParameter] = []
        if recipe.get("parameter"):
            value = self._case_number(normalize_persian(question).lower())
            if value is None:
                return None
            parameters.append(SQLParameter(name=recipe["parameter"], value=value))
        return SQLDraft(
            sql=recipe["sql"],
            parameters=parameters,
            explanation_fa=recipe["explanation_fa"],
            confidence=0.99,
            tables_used=recipe["required_tables"],
            assumptions=[],
        )

    @property
    def sensitive_columns(self) -> set[str]:
        return {value.lower() for value in self.payload.get("sensitive_columns", [])}

    @property
    def mask_patterns(self) -> list[str]:
        return list(self.payload.get("mask_patterns", []))

    def allowed_tables(self, roles: list[str]) -> set[str]:
        output: set[str] = set()
        for role in roles:
            policy = self.payload.get("roles", {}).get(role, {})
            output.update(policy.get("tables", []))
        return output

    @staticmethod
    def _matches(normalized_question: str, terms: list[str]) -> bool:
        question_tokens = set(tokenize(normalized_question))
        for term in terms:
            normalized_term = normalize_persian(term).lower()
            term_tokens = set(tokenize(normalized_term))
            content_tokens = term_tokens - _SEMANTIC_STOPWORDS
            if len(term_tokens) > 1 and normalized_term in normalized_question:
                return True
            if content_tokens and content_tokens.issubset(question_tokens):
                return True
        return False

    @staticmethod
    def _case_number(normalized_question: str) -> str | None:
        explicit = re.findall(
            r"(?:شماره(?:ی)?|پرونده(?:ی)?)(?:\s+(?:به|با))?\s+(\d+)",
            normalized_question,
        )
        if explicit:
            return explicit[-1]
        numbers = re.findall(r"(?<!\w)\d+(?!\w)", normalized_question)
        business_numbers = [value for value in numbers if value != "100"]
        return business_numbers[-1] if business_numbers else None
