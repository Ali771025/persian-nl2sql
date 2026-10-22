from __future__ import annotations

from .models import DomainPlan, QueryAnalysis, SQLDraft
from .normalization import normalize_persian
from .semantic import Commission100SemanticLayer


class SemanticPlanner:
    """Generic schema planner with optional deterministic domain packs."""

    _COMMISSION_MARKERS = (
        "ماده 100",
        "ماده صد",
        "کمیسیون",
        "وضعیت خلاف",
        "پرونده خلاف",
        "tinfcommission100",
    )
    _COMMISSION_CONCEPTS = {
        "commission_case",
        "case_state",
        "verdict",
        "verdict_detail",
        "foul",
        "execution",
    }

    def __init__(self, commission100: Commission100SemanticLayer | None = None):
        self.commission100 = commission100 or Commission100SemanticLayer()
        self.version = self.commission100.version
        self.domain_packs = [self.commission100.domain]

    def plan(self, question: str, analysis: QueryAnalysis) -> DomainPlan:
        normalized = normalize_persian(question).lower()
        commission = self.commission100.plan(question, analysis)
        if commission.matched_recipe_id or any(
            marker in normalized for marker in self._COMMISSION_MARKERS
        ) or self._COMMISSION_CONCEPTS.intersection(commission.concepts):
            return commission
        tables = list(dict.fromkeys(analysis.table_mentions))
        return DomainPlan(
            domain="generic",
            ontology_version="schema-dynamic-v1",
            required_tables=tables,
            required_columns=[],
            business_rules=[],
            expanded_query=analysis.normalized_question,
            confidence=0.45 if tables else 0.25,
        )

    def deterministic_draft(
        self,
        question: str,
        available_tables: set[str],
    ) -> SQLDraft | None:
        return self.commission100.deterministic_draft(question, available_tables)

    @property
    def payload(self):
        return self.commission100.payload

    @property
    def domain(self) -> str:
        return self.commission100.domain
