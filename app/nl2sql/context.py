from __future__ import annotations

from dataclasses import dataclass

from .models import ArtifactKind, DomainPlan, ForeignKey, QueryAnalysis, SearchHit


@dataclass(frozen=True)
class BuiltContext:
    text: str
    sources: list[str]


class ContextBuilder:
    def __init__(self, max_chars: int = 30_000):
        self.max_chars = max_chars

    def build(
        self,
        analysis: QueryAnalysis,
        hits: list[SearchHit],
        foreign_keys: list[ForeignKey],
        domain_plan: DomainPlan | None = None,
        schema_version: str | None = None,
    ) -> str:
        return self.build_with_sources(
            analysis,
            hits,
            foreign_keys,
            domain_plan,
            schema_version,
        ).text

    def build_with_sources(
        self,
        analysis: QueryAnalysis,
        hits: list[SearchHit],
        foreign_keys: list[ForeignKey],
        domain_plan: DomainPlan | None = None,
        schema_version: str | None = None,
    ) -> BuiltContext:
        selected_tables = {table.lower() for hit in hits for table in hit.artifact.tables}
        relevant_edges = [
            edge
            for edge in foreign_keys
            if edge.child_table.lower() in selected_tables or edge.parent_table.lower() in selected_tables
            if (
                not schema_version
                or schema_version.startswith("snapshot-")
                or edge.document_version in {"unversioned", schema_version}
            )
        ]
        sections = [
            "QUERY ANALYSIS",
            f"intent={analysis.intent.value}",
            f"numbers={analysis.numbers}",
            f"domain_terms={analysis.domain_terms}",
            f"requested_schema_version={schema_version or 'active'}",
            "",
            "DOMAIN SEMANTIC PLAN",
            f"ontology_version={domain_plan.ontology_version if domain_plan else 'none'}",
            f"concepts={domain_plan.concepts if domain_plan else []}",
            f"metrics={domain_plan.metrics if domain_plan else []}",
            f"required_tables={domain_plan.required_tables if domain_plan else []}",
            f"required_columns={domain_plan.required_columns if domain_plan else []}",
            "business_rules:",
            *(f"- {rule}" for rule in (domain_plan.business_rules if domain_plan else [])),
            "",
            "FOREIGN KEY GRAPH",
        ]
        for edge in relevant_edges[:40]:
            sections.append(
                f"- {edge.name}: {edge.child_table}.{edge.child_column} "
                f"= {edge.parent_table}.{edge.parent_column}"
            )

        sections.append("\nRETRIEVED SCHEMA, RULES, AND SQL PATTERNS")
        current_size = sum(len(value) + 1 for value in sections)
        required = {
            table.lower() for table in (domain_plan.required_tables if domain_plan else [])
        }
        kind_priority = {
            ArtifactKind.TABLE: 0,
            ArtifactKind.RELATIONSHIP: 1,
            ArtifactKind.BUSINESS_RULE: 2,
            ArtifactKind.SQL_EXAMPLE: 3,
            ArtifactKind.SCHEMA_CHANGE: 4,
            ArtifactKind.TECHNICAL_METADATA: 9,
        }
        ordered = sorted(
            enumerate(hits),
            key=lambda item: (
                0
                if item[1].artifact.kind == ArtifactKind.TABLE
                and any(table.lower() in required for table in item[1].artifact.tables)
                else 1,
                kind_priority.get(item[1].artifact.kind, 8),
                item[0],
            ),
        )
        included_sources: list[str] = []
        for rank, (_, hit) in enumerate(ordered, start=1):
            block = (
                f"\n[SOURCE {rank}] kind={hit.artifact.kind.value} "
                f"version={hit.artifact.document_version} "
                f"channels={','.join(hit.retrieval_channels)} "
                f"path={hit.artifact.source_path}\n{hit.artifact.text.strip()}\n"
            )
            if current_size + len(block) > self.max_chars:
                continue
            sections.append(block)
            current_size += len(block)
            included_sources.append(hit.artifact.source_path)
        return BuiltContext(
            text="\n".join(sections).strip(),
            sources=list(dict.fromkeys(included_sources)),
        )
