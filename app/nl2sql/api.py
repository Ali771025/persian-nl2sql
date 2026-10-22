from __future__ import annotations

import hmac
import time
from typing import Annotated

from fastapi import APIRouter, Header, HTTPException, Query, Request, status

from .evaluation import DomainEvaluator
from .jobs import JobConflict
from .models import (
    BackgroundJob,
    DatabaseStatus,
    DomainOverviewResponse,
    EvaluationReport,
    FeedbackRequest,
    FeedbackResponse,
    FeedbackSummary,
    IndexStatus,
    IngestResponse,
    KnowledgeConsistencyResponse,
    Principal,
    ProductionCheckResponse,
    QueryRequest,
    QueryResponse,
    SchemaGraphResponse,
    SchemaOverviewResponse,
    SchemaVersionsResponse,
    ServiceMeta,
)
from .security import AuthenticationError, JWTAuthenticator
from .service import NL2SQLService

router = APIRouter()


def _service(request: Request) -> NL2SQLService:
    return request.app.state.nl2sql_service


def _check(value: str | None, expected: str) -> None:
    if not value or not hmac.compare_digest(value, expected):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid API key")


def _principal(
    service: NL2SQLService,
    x_api_key: str | None,
    authorization: str | None,
) -> Principal:
    if service.settings.jwt_enabled:
        scheme, _, token = (authorization or "").partition(" ")
        if scheme.lower() != "bearer" or not token:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Bearer token is required",
            )
        try:
            return JWTAuthenticator(service.settings).authenticate(token)
        except AuthenticationError as exc:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail=str(exc),
            ) from exc
    _check(x_api_key, service.settings.api_key)
    return Principal(
        subject="api-key-client",
        roles=[service.settings.default_api_role],
    )


async def _enforce_rate_limit(service: NL2SQLService, principal: Principal) -> None:
    key = f"{principal.tenant_id or '-'}:{principal.subject}"
    allowed, retry_after = await service.rate_limiter.allow(key)
    if not allowed:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="rate limit exceeded",
            headers={"Retry-After": str(retry_after)},
        )


@router.get("/healthz")
async def health(request: Request) -> dict[str, object]:
    return _service(request).health()


@router.get("/readyz")
async def readiness(request: Request) -> dict[str, object]:
    payload = _service(request).readiness()
    if not payload["ready"]:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=payload,
        )
    return payload


@router.get("/v1/meta", response_model=ServiceMeta)
async def meta(request: Request) -> ServiceMeta:
    return _service(request).service_meta()


@router.post("/v1/query", response_model=QueryResponse)
async def query(
    payload: QueryRequest,
    request: Request,
    x_api_key: Annotated[str | None, Header()] = None,
    authorization: Annotated[str | None, Header()] = None,
) -> QueryResponse:
    service = _service(request)
    principal = _principal(service, x_api_key, authorization)
    await _enforce_rate_limit(service, principal)
    started = time.perf_counter()
    response = await service.query(payload, principal=principal)
    duration_ms = int((time.perf_counter() - started) * 1000)
    await service.audit_query(payload, response, principal, duration_ms)
    return response


@router.post("/v1/feedback", response_model=FeedbackResponse)
async def feedback(
    payload: FeedbackRequest,
    request: Request,
    x_api_key: Annotated[str | None, Header()] = None,
    authorization: Annotated[str | None, Header()] = None,
) -> FeedbackResponse:
    service = _service(request)
    principal = _principal(service, x_api_key, authorization)
    try:
        response = await service.submit_feedback(payload, principal)
    except RuntimeError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="feedback storage is disabled",
        ) from exc
    if not response.accepted:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="query request_id was not found",
        )
    return response


@router.post("/v1/admin/ingest", response_model=IngestResponse)
async def ingest(
    request: Request,
    x_admin_api_key: Annotated[str | None, Header()] = None,
) -> IngestResponse:
    service = _service(request)
    _check(x_admin_api_key, service.settings.admin_api_key)
    return await service.ingest()


@router.post(
    "/v1/admin/ingest/jobs",
    response_model=BackgroundJob,
    status_code=status.HTTP_202_ACCEPTED,
)
async def start_ingest_job(
    request: Request,
    x_admin_api_key: Annotated[str | None, Header()] = None,
) -> BackgroundJob:
    service = _service(request)
    _check(x_admin_api_key, service.settings.admin_api_key)
    try:
        return await service.job_manager.start_ingest(
            lambda reporter: service.ingest(progress_callback=reporter)
        )
    except JobConflict as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"message": "an ingest job is already active", "job_id": exc.job_id},
        ) from exc


@router.get("/v1/admin/jobs", response_model=list[BackgroundJob])
async def list_jobs(
    request: Request,
    limit: Annotated[int, Query(ge=1, le=50)] = 20,
    x_admin_api_key: Annotated[str | None, Header()] = None,
) -> list[BackgroundJob]:
    service = _service(request)
    _check(x_admin_api_key, service.settings.admin_api_key)
    return await service.job_manager.list(limit)


@router.get("/v1/admin/jobs/{job_id}", response_model=BackgroundJob)
async def get_job(
    job_id: str,
    request: Request,
    x_admin_api_key: Annotated[str | None, Header()] = None,
) -> BackgroundJob:
    service = _service(request)
    _check(x_admin_api_key, service.settings.admin_api_key)
    job = await service.job_manager.get(job_id)
    if job is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="job not found")
    return job


@router.post("/v1/admin/evaluate", response_model=EvaluationReport)
async def evaluate(
    request: Request,
    x_admin_api_key: Annotated[str | None, Header()] = None,
) -> EvaluationReport:
    service = _service(request)
    _check(x_admin_api_key, service.settings.admin_api_key)
    return await DomainEvaluator(service).run()


@router.post("/v1/admin/production-check", response_model=ProductionCheckResponse)
async def production_check(
    request: Request,
    x_admin_api_key: Annotated[str | None, Header()] = None,
) -> ProductionCheckResponse:
    service = _service(request)
    _check(x_admin_api_key, service.settings.admin_api_key)
    return await service.production_check()


@router.get("/v1/admin/audit/summary")
async def audit_summary(
    request: Request,
    x_admin_api_key: Annotated[str | None, Header()] = None,
) -> dict[str, object]:
    service = _service(request)
    _check(x_admin_api_key, service.settings.admin_api_key)
    return await service.audit_summary()


@router.get("/v1/admin/feedback/summary", response_model=FeedbackSummary)
async def feedback_summary(
    request: Request,
    x_admin_api_key: Annotated[str | None, Header()] = None,
) -> FeedbackSummary:
    service = _service(request)
    _check(x_admin_api_key, service.settings.admin_api_key)
    return await service.feedback_summary()


@router.get("/v1/admin/index/status", response_model=IndexStatus)
async def index_status(
    request: Request,
    x_admin_api_key: Annotated[str | None, Header()] = None,
) -> IndexStatus:
    service = _service(request)
    _check(x_admin_api_key, service.settings.admin_api_key)
    return service.index_status()


@router.get(
    "/v1/admin/knowledge/consistency",
    response_model=KnowledgeConsistencyResponse,
)
async def knowledge_consistency(
    request: Request,
    x_admin_api_key: Annotated[str | None, Header()] = None,
) -> KnowledgeConsistencyResponse:
    service = _service(request)
    _check(x_admin_api_key, service.settings.admin_api_key)
    return service.knowledge_consistency()


@router.get("/v1/admin/database/status", response_model=DatabaseStatus)
async def database_status(
    request: Request,
    x_admin_api_key: Annotated[str | None, Header()] = None,
) -> DatabaseStatus:
    service = _service(request)
    _check(x_admin_api_key, service.settings.admin_api_key)
    return service.database_status()


@router.get("/v1/schema/tables")
async def tables(
    request: Request,
    x_api_key: Annotated[str | None, Header()] = None,
    authorization: Annotated[str | None, Header()] = None,
) -> dict[str, object]:
    service = _service(request)
    principal = _principal(service, x_api_key, authorization)
    allowed = service.role_policy.allowed_tables(principal.roles)
    tables = service.knowledge_base.known_tables
    if "*" not in {value.lower() for value in allowed}:
        tables = [table for table in tables if service.role_policy.table_allowed(table, allowed)]
    return {"count": len(tables), "tables": tables}


@router.get("/v1/schema/overview", response_model=SchemaOverviewResponse)
async def schema_overview(
    request: Request,
    x_api_key: Annotated[str | None, Header()] = None,
    authorization: Annotated[str | None, Header()] = None,
) -> SchemaOverviewResponse:
    service = _service(request)
    principal = _principal(service, x_api_key, authorization)
    return service.schema_overview(principal)


@router.get("/v1/schema/versions", response_model=SchemaVersionsResponse)
async def schema_versions(
    request: Request,
    x_api_key: Annotated[str | None, Header()] = None,
    authorization: Annotated[str | None, Header()] = None,
) -> SchemaVersionsResponse:
    service = _service(request)
    _principal(service, x_api_key, authorization)
    return service.schema_versions()


@router.get("/v1/schema/graph", response_model=SchemaGraphResponse)
async def schema_graph(
    request: Request,
    x_api_key: Annotated[str | None, Header()] = None,
    authorization: Annotated[str | None, Header()] = None,
) -> SchemaGraphResponse:
    service = _service(request)
    principal = _principal(service, x_api_key, authorization)
    return service.schema_graph(principal)


@router.get("/v1/domain/overview", response_model=DomainOverviewResponse)
async def domain_overview(
    request: Request,
    x_api_key: Annotated[str | None, Header()] = None,
    authorization: Annotated[str | None, Header()] = None,
) -> DomainOverviewResponse:
    service = _service(request)
    principal = _principal(service, x_api_key, authorization)
    return service.domain_overview(principal)
