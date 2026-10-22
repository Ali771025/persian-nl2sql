from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field, field_validator


class ArtifactKind(StrEnum):
    TABLE = "table"
    RELATIONSHIP = "relationship"
    SQL_EXAMPLE = "sql_example"
    BUSINESS_RULE = "business_rule"
    SCHEMA_CHANGE = "schema_change"
    TECHNICAL_METADATA = "technical_metadata"


class ColumnInfo(BaseModel):
    name: str
    data_type: str = ""
    nullable: bool | None = None
    description: str = ""
    aliases: list[str] = Field(default_factory=list)


class ForeignKey(BaseModel):
    name: str
    child_table: str
    child_column: str
    parent_table: str
    parent_column: str
    description: str = ""
    source_path: str = ""
    document_version: str = "unversioned"


class Artifact(BaseModel):
    id: str
    kind: ArtifactKind
    title: str
    text: str
    source_path: str
    tables: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)
    doc_id: str = ""
    document_version: str = "unversioned"
    content_hash: str = ""


class Catalog(BaseModel):
    artifacts: list[Artifact]
    foreign_keys: list[ForeignKey]
    content_hash: str
    source_file_count: int = 0
    databases: list[str] = Field(default_factory=list)
    schema_versions: list[str] = Field(default_factory=list)
    snapshot_version: str = ""
    active_schema_version: str = ""


class IndexBuildStats(BaseModel):
    added: int = 0
    updated: int = 0
    reused: int = 0
    deleted: int = 0

    @property
    def reuse_ratio(self) -> float:
        total = self.added + self.updated + self.reused
        return self.reused / total if total else 0.0


class Intent(StrEnum):
    LOOKUP = "lookup"
    LATEST = "latest"
    AGGREGATE = "aggregate"
    LIST = "list"
    COMPARE = "compare"
    UNKNOWN = "unknown"


class QueryAnalysis(BaseModel):
    normalized_question: str
    intent: Intent
    numbers: list[str] = Field(default_factory=list)
    table_mentions: list[str] = Field(default_factory=list)
    temporal_terms: list[str] = Field(default_factory=list)
    domain_terms: list[str] = Field(default_factory=list)


class DomainPlan(BaseModel):
    domain: str = "commission100"
    ontology_version: str
    concepts: list[str] = Field(default_factory=list)
    metrics: list[str] = Field(default_factory=list)
    required_tables: list[str] = Field(default_factory=list)
    required_columns: list[str] = Field(default_factory=list)
    business_rules: list[str] = Field(default_factory=list)
    expanded_query: str
    matched_recipe_id: str | None = None
    confidence: float = Field(default=0.0, ge=0, le=1)


class Principal(BaseModel):
    subject: str
    roles: list[str] = Field(default_factory=list)
    tenant_id: str | None = None
    city: str | None = None
    area: str | None = None


class AccessDecision(BaseModel):
    allowed: bool
    errors: list[str] = Field(default_factory=list)
    applied_scopes: dict[str, str] = Field(default_factory=dict)


class SearchHit(BaseModel):
    artifact: Artifact
    score: float = Field(ge=0)
    dense_score: float = 0.0
    keyword_score: float = 0.0
    graph_expanded: bool = False
    rerank_score: float | None = None
    retrieval_channels: list[str] = Field(default_factory=list)
    graph_distance: int | None = None


class SQLParameter(BaseModel):
    name: str = Field(pattern=r"^[A-Za-z_][A-Za-z0-9_]*$")
    value: str | int | float | bool | None


class SQLDraft(BaseModel):
    sql: str
    parameters: list[SQLParameter] = Field(default_factory=list)
    explanation_fa: str
    confidence: float = Field(ge=0, le=1)
    tables_used: list[str] = Field(default_factory=list)
    assumptions: list[str] = Field(default_factory=list)
    needs_clarification: bool = False
    clarification_question: str | None = None

    @field_validator("sql")
    @classmethod
    def normalize_sql(cls, value: str) -> str:
        value = value.strip()
        if value.startswith("```"):
            lines = value.splitlines()
            value = "\n".join(lines[1:-1]).strip()
        return value

    def parameter_dict(self) -> dict[str, Any]:
        return {item.name: item.value for item in self.parameters}


class ValidationResult(BaseModel):
    valid: bool
    errors: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    referenced_tables: list[str] = Field(default_factory=list)
    referenced_columns: list[str] = Field(default_factory=list)


class ExecutionResult(BaseModel):
    columns: list[str]
    rows: list[dict[str, Any]]
    row_count: int
    truncated: bool
    duration_ms: int


class QueryRequest(BaseModel):
    question: str = Field(min_length=3, max_length=2_000)
    execute: bool = False
    include_context: bool = False
    schema_version: str | None = Field(default=None, min_length=1, max_length=120)
    llm_provider: str | None = Field(
        default=None,
        min_length=1,
        max_length=40,
        pattern=r"^[a-z0-9_]+$",
    )
    llm_model: str | None = Field(
        default=None,
        min_length=1,
        max_length=120,
        pattern=r"^[A-Za-z0-9._:/-]+$",
    )


class PipelineTrace(BaseModel):
    stage: str
    duration_ms: int
    detail: str = ""


class QueryResponse(BaseModel):
    request_id: str
    status: str
    question: str
    analysis: QueryAnalysis
    domain_plan: DomainPlan | None = None
    generation_mode: str | None = None
    access: AccessDecision | None = None
    sql: str | None = None
    parameters: dict[str, Any] = Field(default_factory=dict)
    explanation_fa: str | None = None
    answer_fa: str | None = None
    confidence: float = 0.0
    validation: ValidationResult | None = None
    result: ExecutionResult | None = None
    clarification_question: str | None = None
    error: str | None = None
    retrieved_sources: list[str] = Field(default_factory=list)
    retrieval_candidates: list[str] = Field(default_factory=list)
    context_sources: list[str] = Field(default_factory=list)
    context: str | None = None
    trace: list[PipelineTrace] = Field(default_factory=list)
    schema_version: str | None = None
    knowledge_snapshot_id: str = ""
    knowledge_generation: int = 0
    failure_stage: str | None = None
    llm_provider: str | None = None
    llm_model: str | None = None


class IngestResponse(BaseModel):
    files_scanned: int
    artifacts_indexed: int
    tables_found: int
    foreign_keys_found: int
    sql_examples_found: int
    content_hash: str
    embedding_model: str
    artifacts_added: int = 0
    artifacts_updated: int = 0
    artifacts_reused: int = 0
    artifacts_deleted: int = 0
    vector_reuse_ratio: float = 0.0
    schema_versions: list[str] = Field(default_factory=list)
    snapshot_version: str = ""
    active_schema_version: str = ""
    qdrant_points: int = 0
    graph_nodes: int = 0
    distributed_errors: list[str] = Field(default_factory=list)
    snapshot_id: str = ""
    generation: int = 0
    publication_phase: str = "published"
    fencing_token: int = 0


class KnowledgeConsistencyResponse(BaseModel):
    consistent: bool
    expected_snapshot_id: str = ""
    published: dict[str, Any] | None = None
    issues: list[str] = Field(default_factory=list)
    stores: dict[str, dict[str, Any]] = Field(default_factory=dict)


class EvaluationCaseResult(BaseModel):
    case_id: str
    question: str
    passed: bool
    status: str
    recipe_id: str | None = None
    sql: str | None = None
    parameters: dict[str, Any] = Field(default_factory=dict)
    explanation_fa: str | None = None
    validation_errors: list[str] = Field(default_factory=list)
    knowledge_snapshot_id: str = ""
    failures: list[str] = Field(default_factory=list)


class EvaluationReport(BaseModel):
    suite: str
    ontology_version: str
    total: int
    passed: int
    pass_rate: float
    cases: list[EvaluationCaseResult] = Field(default_factory=list)


class ProductionCheckItem(BaseModel):
    name: str
    passed: bool
    detail: str = ""


class ProductionCheckResponse(BaseModel):
    ready_for_production: bool
    version: str
    checked_at: datetime
    checks: list[ProductionCheckItem] = Field(default_factory=list)
    gold_evaluation: EvaluationReport
    critical_question: str
    critical_status: str
    critical_recipe_id: str | None = None
    critical_sql: str | None = None
    critical_validation_errors: list[str] = Field(default_factory=list)
    knowledge_snapshot_id: str = ""


class LLMProviderOption(BaseModel):
    provider: str
    label: str
    models: list[str] = Field(default_factory=list)


class ServiceMeta(BaseModel):
    name: str = "Persian Schema-RAG NL2SQL"
    version: str
    api_version: str = "v1"
    auth_mode: str
    execution_enabled: bool
    llm_enabled: bool = False
    execution_available: bool = False
    semantic_mode: str = "hybrid"
    domain_packs: list[str] = Field(default_factory=list)
    degraded_reasons: list[str] = Field(default_factory=list)
    features: list[str] = Field(default_factory=list)
    default_llm_provider: str | None = None
    default_llm_model: str | None = None
    llm_options: list[LLMProviderOption] = Field(default_factory=list)


class SchemaTableSummary(BaseModel):
    name: str
    schema_name: str = "dbo"
    qualified_name: str = ""
    description: str = ""
    columns: list[ColumnInfo] = Field(default_factory=list)
    source_path: str = ""


class SchemaOverviewResponse(BaseModel):
    table_count: int
    relationship_count: int
    tables: list[SchemaTableSummary] = Field(default_factory=list)


class SchemaVersionsResponse(BaseModel):
    active_version: str = ""
    versions: list[str] = Field(default_factory=list)
    databases: list[str] = Field(default_factory=list)
    generation: int = 0


class SchemaGraphNode(BaseModel):
    id: str
    label: str
    column_count: int = 0


class SchemaGraphEdge(BaseModel):
    id: str
    source: str
    target: str
    source_column: str
    target_column: str
    label: str


class SchemaGraphResponse(BaseModel):
    nodes: list[SchemaGraphNode] = Field(default_factory=list)
    edges: list[SchemaGraphEdge] = Field(default_factory=list)


class DomainItemSummary(BaseModel):
    id: str
    label: str
    tables: list[str] = Field(default_factory=list)
    columns: list[str] = Field(default_factory=list)


class DomainRecipeSummary(BaseModel):
    id: str
    required_tables: list[str] = Field(default_factory=list)
    requires_case_number: bool = False


class DomainOverviewResponse(BaseModel):
    domain: str
    ontology_version: str
    concepts: list[DomainItemSummary] = Field(default_factory=list)
    metrics: list[DomainItemSummary] = Field(default_factory=list)
    business_rules: list[str] = Field(default_factory=list)
    recipes: list[DomainRecipeSummary] = Field(default_factory=list)


class IndexStatus(BaseModel):
    ready: bool
    generation: int = 0
    content_hash: str = ""
    artifact_count: int = 0
    table_count: int = 0
    foreign_key_count: int = 0
    embedding_model: str
    embedding_dimensions: int = 0
    artifacts_added: int = 0
    artifacts_updated: int = 0
    artifacts_reused: int = 0
    artifacts_deleted: int = 0
    vector_reuse_ratio: float = 0.0
    mode: str = "local"
    source_file_count: int = 0
    databases: list[str] = Field(default_factory=list)
    schema_versions: list[str] = Field(default_factory=list)
    snapshot_version: str = ""
    active_schema_version: str = ""
    stores: dict[str, dict[str, Any]] = Field(default_factory=dict)
    consistent: bool = True
    consistency_issues: list[str] = Field(default_factory=list)


class DatabaseStatus(BaseModel):
    connected: bool
    dialect: str
    migration_revision: str | None = None
    tables: list[str] = Field(default_factory=list)


class FeedbackRating(StrEnum):
    HELPFUL = "helpful"
    NOT_HELPFUL = "not_helpful"


class FeedbackReason(StrEnum):
    CORRECT = "correct"
    WRONG_TABLE = "wrong_table"
    WRONG_JOIN = "wrong_join"
    WRONG_FILTER = "wrong_filter"
    WRONG_AGGREGATION = "wrong_aggregation"
    MISSING_CONTEXT = "missing_context"
    ACCESS_ISSUE = "access_issue"
    OTHER = "other"


class FeedbackRequest(BaseModel):
    request_id: str = Field(min_length=16, max_length=64, pattern=r"^[A-Fa-f0-9-]+$")
    rating: FeedbackRating
    reason: FeedbackReason
    expected_recipe_id: str | None = Field(
        default=None,
        max_length=100,
        pattern=r"^[A-Za-z0-9_-]+$",
    )


class FeedbackResponse(BaseModel):
    accepted: bool
    request_id: str


class FeedbackSummary(BaseModel):
    total: int
    by_rating: dict[str, int] = Field(default_factory=dict)
    by_reason: dict[str, int] = Field(default_factory=dict)
    stores_free_text: bool = False


class JobState(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"


class BackgroundJob(BaseModel):
    job_id: str
    kind: str
    state: JobState
    created_at: datetime
    started_at: datetime | None = None
    finished_at: datetime | None = None
    result: IngestResponse | None = None
    error_code: str | None = None
    phase: str = "queued"
    progress_percent: int = Field(default=0, ge=0, le=100)
    heartbeat_at: datetime | None = None
    snapshot_id: str = ""
    attempt: int = 1
    correlation_id: str = ""
