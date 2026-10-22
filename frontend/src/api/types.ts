export interface LLMProviderOption {
  provider: string
  label: string
  models: string[]
}

export interface ServiceMeta {
  name: string
  version: string
  api_version: string
  auth_mode: 'api_key' | 'jwt'
  execution_enabled: boolean
  llm_enabled: boolean
  execution_available: boolean
  semantic_mode: string
  domain_packs: string[]
  degraded_reasons: string[]
  features: string[]
  default_llm_provider: string | null
  default_llm_model: string | null
  llm_options: LLMProviderOption[]
}

export interface Health {
  status: string
  index_ready: boolean
  artifact_count: number
  table_count: number
  embedding_model: string
  reranker_model: string
  llm_model: string | null
  llm_provider: string | null
  ontology_version: string
  jwt_enabled: boolean
  audit_enabled: boolean
  execution_enabled: boolean
  knowledge_mode?: string
}

export interface QueryAnalysis {
  normalized_question: string
  intent: string
  numbers: string[]
  table_mentions: string[]
  temporal_terms: string[]
  domain_terms: string[]
}

export interface DomainPlan {
  domain: string
  ontology_version: string
  concepts: string[]
  metrics: string[]
  required_tables: string[]
  required_columns: string[]
  business_rules: string[]
  expanded_query: string
  matched_recipe_id: string | null
  confidence: number
}

export interface PipelineTrace {
  stage: string
  duration_ms: number
  detail: string
}

export interface ValidationResult {
  valid: boolean
  errors: string[]
  warnings: string[]
  referenced_tables: string[]
  referenced_columns: string[]
}

export interface ExecutionResult {
  columns: string[]
  rows: Record<string, unknown>[]
  row_count: number
  truncated: boolean
  duration_ms: number
}

export interface QueryResponse {
  request_id: string
  status: string
  question: string
  analysis: QueryAnalysis
  domain_plan: DomainPlan | null
  generation_mode: string | null
  access: { allowed: boolean; errors: string[]; applied_scopes: Record<string, string> } | null
  sql: string | null
  parameters: Record<string, unknown>
  explanation_fa: string | null
  answer_fa: string | null
  confidence: number
  validation: ValidationResult | null
  result: ExecutionResult | null
  clarification_question: string | null
  error: string | null
  retrieved_sources: string[]
  retrieval_candidates: string[]
  context_sources: string[]
  context: string | null
  failure_stage: string | null
  trace: PipelineTrace[]
  schema_version: string | null
  knowledge_snapshot_id: string
  knowledge_generation: number
  llm_provider: string | null
  llm_model: string | null
}

export interface SchemaColumn {
  name: string
  data_type: string
  nullable: boolean | null
  description: string
}

export interface SchemaTable {
  name: string
  schema_name: string
  description: string
  columns: SchemaColumn[]
  source_path: string
}

export interface SchemaOverview {
  table_count: number
  relationship_count: number
  tables: SchemaTable[]
}

export interface SchemaVersions {
  active_version: string
  versions: string[]
  databases: string[]
  generation: number
}

export interface SchemaGraph {
  nodes: { id: string; label: string; column_count: number }[]
  edges: {
    id: string
    source: string
    target: string
    source_column: string
    target_column: string
    label: string
  }[]
}

export interface DomainOverview {
  domain: string
  ontology_version: string
  concepts: { id: string; label: string; tables: string[]; columns: string[] }[]
  metrics: { id: string; label: string; tables: string[]; columns: string[] }[]
  business_rules: string[]
  recipes: { id: string; required_tables: string[]; requires_case_number: boolean }[]
}

export interface IndexStatus {
  ready: boolean
  generation: number
  content_hash: string
  artifact_count: number
  table_count: number
  foreign_key_count: number
  embedding_model: string
  embedding_dimensions: number
  artifacts_added: number
  artifacts_updated: number
  artifacts_reused: number
  artifacts_deleted: number
  vector_reuse_ratio: number
  mode: 'local' | 'distributed'
  source_file_count: number
  databases: string[]
  schema_versions: string[]
  snapshot_version: string
  active_schema_version: string
  stores: Record<string, { enabled: boolean; connected: boolean; backend: string; [key: string]: unknown }>
  consistent: boolean
  consistency_issues: string[]
}

export interface KnowledgeConsistency {
  consistent: boolean
  expected_snapshot_id: string
  published: Record<string, unknown> | null
  issues: string[]
  stores: IndexStatus['stores']
}

export interface DatabaseStatus {
  connected: boolean
  dialect: string
  migration_revision: string | null
  tables: string[]
}

export interface EvaluationReport {
  suite: string
  ontology_version: string
  total: number
  passed: number
  pass_rate: number
  cases: {
    case_id: string
    question: string
    passed: boolean
    status: string
    recipe_id: string | null
    sql: string | null
    parameters: Record<string, unknown>
    explanation_fa: string | null
    validation_errors: string[]
    knowledge_snapshot_id: string
    failures: string[]
  }[]
}

export interface ProductionCheck {
  ready_for_production: boolean
  version: string
  checked_at: string
  checks: { name: string; passed: boolean; detail: string }[]
  gold_evaluation: EvaluationReport
  critical_question: string
  critical_status: string
  critical_recipe_id: string | null
  critical_sql: string | null
  critical_validation_errors: string[]
  knowledge_snapshot_id: string
}

export interface BackgroundJob {
  job_id: string
  kind: string
  state: 'queued' | 'running' | 'succeeded' | 'failed'
  created_at: string
  started_at: string | null
  finished_at: string | null
  result: Record<string, unknown> | null
  error_code: string | null
  phase: string
  progress_percent: number
  heartbeat_at: string | null
  snapshot_id: string
  attempt: number
  correlation_id: string
}

export interface FeedbackSummary {
  total: number
  by_rating: Record<string, number>
  by_reason: Record<string, number>
  stores_free_text: boolean
}

export interface AuditSummary {
  enabled: boolean
  total_queries?: number
  by_status?: Record<string, number>
  average_duration_ms?: number
  stores_sensitive_payloads?: boolean
}
