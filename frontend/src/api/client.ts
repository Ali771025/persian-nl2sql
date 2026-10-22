import type {
  AuditSummary,
  BackgroundJob,
  DatabaseStatus,
  DomainOverview,
  EvaluationReport,
  FeedbackSummary,
  Health,
  IndexStatus,
  KnowledgeConsistency,
  ProductionCheck,
  QueryResponse,
  SchemaGraph,
  SchemaOverview,
  SchemaVersions,
  ServiceMeta,
} from './types'

export interface AuthConfig {
  apiBase: string
  apiKey: string
  adminKey: string
  bearerToken: string
}

const STORAGE_KEY = 'nl2sql-console-auth-v1'

export const defaultAuthConfig: AuthConfig = {
  apiBase: import.meta.env.VITE_API_BASE_URL || '/api',
  apiKey: '',
  adminKey: '',
  bearerToken: '',
}

export function normalizeAuthConfig(value: AuthConfig): AuthConfig {
  return {
    apiBase: value.apiBase.trim().replace(/\/$/, '') || '/api',
    apiKey: value.apiKey.trim(),
    adminKey: value.adminKey.trim(),
    bearerToken: value.bearerToken.trim(),
  }
}

export function loadAuthConfig(): AuthConfig {
  try {
    const saved = sessionStorage.getItem(STORAGE_KEY)
    return saved
      ? normalizeAuthConfig({ ...defaultAuthConfig, ...JSON.parse(saved) })
      : defaultAuthConfig
  } catch {
    return defaultAuthConfig
  }
}

export function saveAuthConfig(value: AuthConfig): void {
  sessionStorage.setItem(STORAGE_KEY, JSON.stringify(normalizeAuthConfig(value)))
}

export class ApiError extends Error {
  constructor(
    message: string,
    public status: number,
  ) {
    super(message)
  }
}

export class ApiClient {
  constructor(private config: AuthConfig) {}

  private async request<T>(path: string, init: RequestInit = {}, admin = false): Promise<T> {
    const headers = new Headers(init.headers)
    headers.set('Content-Type', 'application/json')
    if (admin) {
      if (this.config.adminKey) headers.set('X-Admin-API-Key', this.config.adminKey)
    } else if (this.config.apiKey) {
      headers.set('X-API-Key', this.config.apiKey)
    } else if (this.config.bearerToken) {
      headers.set('Authorization', `Bearer ${this.config.bearerToken}`)
    }
    const response = await fetch(`${this.config.apiBase}${path}`, { ...init, headers })
    if (!response.ok) {
      let message = `خطای سرویس (${response.status})`
      try {
        const payload = (await response.json()) as { detail?: unknown }
        message = typeof payload.detail === 'string' ? payload.detail : JSON.stringify(payload.detail)
      } catch {
        // Keep the generic message for non-JSON responses.
      }
      throw new ApiError(message, response.status)
    }
    return (await response.json()) as T
  }

  meta = () => this.request<ServiceMeta>('/v1/meta')
  health = () => this.request<Health>('/healthz')
  query = (
    question: string,
    execute: boolean,
    includeContext = false,
    schemaVersion = '',
    llmProvider = '',
    llmModel = '',
  ) =>
    this.request<QueryResponse>('/v1/query', {
      method: 'POST',
      body: JSON.stringify({
        question,
        execute,
        include_context: includeContext,
        schema_version: schemaVersion || null,
        llm_provider: llmProvider || null,
        llm_model: llmModel || null,
      }),
    })
  feedback = (requestId: string, rating: string, reason: string) =>
    this.request<{ accepted: boolean }>('/v1/feedback', {
      method: 'POST',
      body: JSON.stringify({ request_id: requestId, rating, reason }),
    })
  schema = () => this.request<SchemaOverview>('/v1/schema/overview')
  schemaVersions = () => this.request<SchemaVersions>('/v1/schema/versions')
  schemaGraph = () => this.request<SchemaGraph>('/v1/schema/graph')
  domain = () => this.request<DomainOverview>('/v1/domain/overview')
  indexStatus = () => this.request<IndexStatus>('/v1/admin/index/status', {}, true)
  knowledgeConsistency = () =>
    this.request<KnowledgeConsistency>('/v1/admin/knowledge/consistency', {}, true)
  databaseStatus = () => this.request<DatabaseStatus>('/v1/admin/database/status', {}, true)
  startIngest = () => this.request<BackgroundJob>('/v1/admin/ingest/jobs', { method: 'POST' }, true)
  jobs = () => this.request<BackgroundJob[]>('/v1/admin/jobs', {}, true)
  evaluate = () => this.request<EvaluationReport>('/v1/admin/evaluate', { method: 'POST' }, true)
  productionCheck = () =>
    this.request<ProductionCheck>('/v1/admin/production-check', { method: 'POST' }, true)
  auditSummary = () => this.request<AuditSummary>('/v1/admin/audit/summary', {}, true)
  feedbackSummary = () => this.request<FeedbackSummary>('/v1/admin/feedback/summary', {}, true)
}
