from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from .models import ArtifactKind, DomainPlan, QueryAnalysis, SearchHit
from .normalization import normalize_persian


@dataclass(frozen=True)
class RetrievalQuery:
    text: str
    channel: str
    weight: float
    graph_depth: int


class SearchableKnowledgeBase(Protocol):
    def search(
        self,
        query: str,
        top_k: int = 36,
        graph_depth: int = 1,
        schema_version: str | None = None,
    ) -> list[SearchHit]: ...


def build_retrieval_queries(
    analysis: QueryAnalysis,
    domain_plan: DomainPlan,
    graph_depth: int,
) -> list[RetrievalQuery]:
    """Split a Persian request into focused schema, relation, and semantic searches."""

    variants = [
        RetrievalQuery(
            text=analysis.normalized_question,
            channel="query_original",
            weight=1.0,
            graph_depth=graph_depth,
        )
    ]
    tables = list(dict.fromkeys(domain_plan.required_tables + analysis.table_mentions))
    if tables or domain_plan.required_columns:
        variants.append(
            RetrievalQuery(
                text=" ".join(
                    [
                        "schema table columns جدول ستون",
                        *tables,
                        *domain_plan.required_columns,
                    ]
                ),
                channel="query_schema",
                weight=0.96,
                graph_depth=0,
            )
        )
    if len(tables) >= 2:
        variants.append(
            RetrievalQuery(
                text=" ".join(
                    [
                        "foreign key relationship join رابطه اتصال کلید خارجی",
                        *tables,
                    ]
                ),
                channel="query_relationship",
                weight=0.92,
                # A second hop is useful only for the focused relationship query.
                graph_depth=min(2, max(1, graph_depth + 1)),
            )
        )
    semantic_parts = [
        *domain_plan.concepts,
        *domain_plan.metrics,
        *domain_plan.business_rules,
    ]
    if semantic_parts:
        variants.append(
            RetrievalQuery(
                text=" ".join(["business lookup rule مقدار مرجع قاعده", *semantic_parts]),
                channel="query_semantic",
                weight=0.88,
                graph_depth=0,
            )
        )

    deduplicated: list[RetrievalQuery] = []
    seen: set[str] = set()
    for variant in variants:
        key = normalize_persian(variant.text).lower()
        if not key or key in seen:
            continue
        seen.add(key)
        deduplicated.append(variant)
    return deduplicated


def multi_query_search(
    knowledge_base: SearchableKnowledgeBase,
    variants: list[RetrievalQuery],
    top_k: int,
    schema_version: str | None,
) -> list[SearchHit]:
    """Fuse focused searches with weighted reciprocal-rank fusion."""

    combined: dict[str, SearchHit] = {}
    fused_scores: dict[str, float] = {}
    channels: dict[str, set[str]] = {}
    graph_distances: dict[str, list[int]] = {}
    per_query_top_k = max(12, top_k)

    for variant in variants:
        hits = knowledge_base.search(
            variant.text,
            per_query_top_k,
            variant.graph_depth,
            schema_version,
        )
        for rank, hit in enumerate(hits, start=1):
            artifact_id = hit.artifact.id
            fused_scores[artifact_id] = fused_scores.get(artifact_id, 0.0) + (
                variant.weight / (20.0 + rank)
            )
            channels.setdefault(artifact_id, set()).update(hit.retrieval_channels)
            channels[artifact_id].add(variant.channel)
            if hit.graph_distance is not None:
                graph_distances.setdefault(artifact_id, []).append(hit.graph_distance)
            existing = combined.get(artifact_id)
            if existing is None or hit.score > existing.score:
                combined[artifact_id] = hit

    limit = max(top_k * 2, top_k + 12)
    ordered_ids = sorted(
        combined,
        key=lambda artifact_id: fused_scores[artifact_id],
        reverse=True,
    )[:limit]
    return [
        combined[artifact_id].model_copy(
            update={
                "score": fused_scores[artifact_id],
                "retrieval_channels": sorted(channels[artifact_id]),
                "graph_distance": (
                    min(graph_distances[artifact_id])
                    if graph_distances.get(artifact_id)
                    else combined[artifact_id].graph_distance
                ),
            }
        )
        for artifact_id in ordered_ids
    ]


def select_context_hits(
    reranked_hits: list[SearchHit],
    candidates: list[SearchHit],
    required_tables: list[str],
    limit: int,
) -> list[SearchHit]:
    """Keep relevance order while guaranteeing useful schema-context diversity."""

    if limit <= 0:
        return []
    candidate_order = sorted(candidates, key=lambda hit: hit.score, reverse=True)
    selected: list[SearchHit] = []
    selected_ids: set[str] = set()

    def add(hit: SearchHit) -> None:
        if len(selected) >= limit or hit.artifact.id in selected_ids:
            return
        selected.append(hit)
        selected_ids.add(hit.artifact.id)

    # Required table definitions cannot be displaced by generic prose.
    for table in dict.fromkeys(required_tables):
        table_key = table.lower()
        match = next(
            (
                hit
                for hit in candidate_order
                if hit.artifact.kind == ArtifactKind.TABLE
                and any(item.lower() == table_key for item in hit.artifact.tables)
            ),
            None,
        )
        if match is not None:
            add(match)

    # Reserve small, bounded slots for joins, lookup/business semantics and examples.
    quotas = {
        ArtifactKind.RELATIONSHIP: 2,
        ArtifactKind.BUSINESS_RULE: 2,
        ArtifactKind.SQL_EXAMPLE: 2,
    }
    for kind, quota in quotas.items():
        added = 0
        for hit in candidate_order:
            if hit.artifact.kind != kind or hit.artifact.id in selected_ids:
                continue
            add(hit)
            added += 1
            if added >= quota or len(selected) >= limit:
                break

    for hit in reranked_hits:
        add(hit)
    for hit in candidate_order:
        add(hit)
    return selected
