from __future__ import annotations

import logging
import time
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.middleware.trustedhost import TrustedHostMiddleware
from fastapi.responses import JSONResponse

from .api import router
from .config import Settings, get_settings
from .observability import bind_request_id, configure_logging, reset_request_id
from .service import NL2SQLService

logger = logging.getLogger("nl2sql.http")


def create_app(settings: Settings | None = None) -> FastAPI:
    resolved = settings or get_settings()

    @asynccontextmanager
    async def lifespan(application: FastAPI):
        configure_logging(resolved.log_level, json_logs=resolved.json_logs)
        service = NL2SQLService.from_settings(resolved)
        application.state.nl2sql_service = service
        try:
            yield
        finally:
            await service.close()

    application = FastAPI(
        title="Persian Schema-RAG NL2SQL",
        version="0.9.0",
        docs_url="/docs" if resolved.expose_api_docs else None,
        redoc_url=None,
        openapi_url="/openapi.json" if resolved.expose_api_docs else None,
        lifespan=lifespan,
    )
    application.add_middleware(GZipMiddleware, minimum_size=1_000)
    application.add_middleware(TrustedHostMiddleware, allowed_hosts=resolved.trusted_hosts)
    if resolved.cors_origins:
        application.add_middleware(
            CORSMiddleware,
            allow_origins=resolved.cors_origins,
            allow_credentials=False,
            allow_methods=["GET", "POST", "OPTIONS"],
            allow_headers=[
                "Authorization",
                "Content-Type",
                "X-API-Key",
                "X-Admin-API-Key",
                "X-Request-ID",
            ],
            expose_headers=["X-Request-ID"],
        )

    @application.middleware("http")
    async def request_context(request: Request, call_next):
        request_id, token = bind_request_id(request.headers.get("X-Request-ID"))
        request.state.request_id = request_id
        started = time.perf_counter()
        status_code = 500
        try:
            response = await call_next(request)
            status_code = response.status_code
            response.headers["X-Request-ID"] = request_id
            response.headers["X-Content-Type-Options"] = "nosniff"
            response.headers["X-Frame-Options"] = "DENY"
            response.headers["Referrer-Policy"] = "no-referrer"
            if request.url.path.startswith("/v1/"):
                response.headers["Cache-Control"] = "no-store"
            return response
        finally:
            logger.info(
                "http_request_completed",
                extra={
                    "method": request.method,
                    "path": request.url.path,
                    "status_code": status_code,
                    "duration_ms": int((time.perf_counter() - started) * 1_000),
                },
            )
            reset_request_id(token)

    @application.exception_handler(Exception)
    async def unhandled_exception(request: Request, exc: Exception):
        request_id = getattr(request.state, "request_id", "-")
        logger.exception("unhandled_application_error")
        return JSONResponse(
            status_code=500,
            content={"detail": "internal server error", "request_id": request_id},
            headers={"X-Request-ID": request_id, "Cache-Control": "no-store"},
        )

    application.include_router(router)
    return application


app = create_app()
