from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import (
    JSON,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from .database import Base


class QueryAuditRecord(Base):
    __tablename__ = "query_audit"
    __table_args__ = (
        Index("ix_query_audit_created_at", "created_at"),
        Index("ix_query_audit_status_created_at", "status", "created_at"),
        Index(
            "ix_query_audit_snapshot_created_at",
            "knowledge_snapshot_id",
            "created_at",
        ),
    )

    request_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    principal_hash: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    tenant_hash: Mapped[str | None] = mapped_column(String(64))
    roles: Mapped[list[str]] = mapped_column("roles_json", JSON, nullable=False, default=list)
    question_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    intent: Mapped[str] = mapped_column(String(32), nullable=False)
    generation_mode: Mapped[str | None] = mapped_column(String(32))
    tables: Mapped[list[str]] = mapped_column("tables_json", JSON, nullable=False, default=list)
    status: Mapped[str] = mapped_column(String(40), nullable=False)
    duration_ms: Mapped[int] = mapped_column(Integer, nullable=False)
    row_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    knowledge_snapshot_id: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    knowledge_generation: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    schema_version: Mapped[str | None] = mapped_column(String(120))
    recipe_id: Mapped[str | None] = mapped_column(String(100))


class QueryFeedbackRecord(Base):
    __tablename__ = "query_feedback"
    __table_args__ = (
        Index("ix_query_feedback_created_at", "created_at"),
        Index("ix_query_feedback_rating", "rating"),
    )

    request_id: Mapped[str] = mapped_column(
        String(64),
        ForeignKey("query_audit.request_id", ondelete="CASCADE"),
        primary_key=True,
    )
    principal_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    rating: Mapped[str] = mapped_column(String(20), nullable=False)
    reason_code: Mapped[str] = mapped_column(String(40), nullable=False)
    expected_recipe_id: Mapped[str | None] = mapped_column(String(100))


class AdminJobRecord(Base):
    __tablename__ = "admin_job"
    __table_args__ = (
        Index("ix_admin_job_kind_state", "kind", "state"),
        Index("ix_admin_job_created_at", "created_at"),
        Index(
            "uq_admin_job_active_kind",
            "kind",
            unique=True,
            sqlite_where=text("state IN ('queued', 'running')"),
            postgresql_where=text("state IN ('queued', 'running')"),
            mssql_where=text("state IN ('queued', 'running')"),
        ),
    )

    job_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    kind: Mapped[str] = mapped_column(String(40), nullable=False)
    state: Mapped[str] = mapped_column(String(20), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    result: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    error_code: Mapped[str | None] = mapped_column(String(120))
    phase: Mapped[str] = mapped_column(String(40), nullable=False, default="queued")
    progress_percent: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    snapshot_id: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    attempt: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    correlation_id: Mapped[str] = mapped_column(String(64), nullable=False, default="")
