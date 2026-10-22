from __future__ import annotations

import hashlib
import hmac
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import func, select

from .database import OperationalDatabase, database_from_sqlite_path
from .db_models import QueryAuditRecord, QueryFeedbackRecord
from .models import (
    FeedbackRequest,
    FeedbackSummary,
    Principal,
    QueryRequest,
    QueryResponse,
)


@dataclass(frozen=True)
class QueryAuditEvent:
    request: QueryRequest
    response: QueryResponse
    principal: Principal
    duration_ms: int


class AuditRepository:
    """SQLAlchemy repository that persists metadata, never questions, SQL or rows."""

    def __init__(
        self,
        database: OperationalDatabase | Path,
        hmac_key: str,
        *,
        create_schema_for_tests: bool | None = None,
    ):
        path_mode = isinstance(database, Path)
        self.database = database_from_sqlite_path(database) if path_mode else database
        self.hmac_key = hmac_key.encode("utf-8")
        if create_schema_for_tests is True or (create_schema_for_tests is None and path_mode):
            self.database.create_schema_for_tests()

    def record(self, event: QueryAuditEvent) -> None:
        response = event.response
        tables = response.validation.referenced_tables if response.validation else []
        row_count = response.result.row_count if response.result else 0
        record = QueryAuditRecord(
            request_id=response.request_id,
            created_at=datetime.now(UTC),
            principal_hash=self._digest(event.principal.subject),
            tenant_hash=(self._digest(event.principal.tenant_id) if event.principal.tenant_id else None),
            roles=sorted(event.principal.roles),
            question_hash=self._digest(event.request.question),
            intent=response.analysis.intent.value,
            generation_mode=response.generation_mode,
            tables=sorted(tables),
            status=response.status,
            duration_ms=event.duration_ms,
            row_count=row_count,
            knowledge_snapshot_id=response.knowledge_snapshot_id,
            knowledge_generation=response.knowledge_generation,
            schema_version=response.schema_version,
            recipe_id=(response.domain_plan.matched_recipe_id if response.domain_plan else None),
        )
        with self.database.session() as session:
            session.merge(record)

    def summary(self) -> dict[str, object]:
        with self.database.session() as session:
            total = session.scalar(select(func.count()).select_from(QueryAuditRecord)) or 0
            rows = session.execute(
                select(QueryAuditRecord.status, func.count())
                .group_by(QueryAuditRecord.status)
                .order_by(QueryAuditRecord.status)
            ).all()
            average_duration = session.scalar(
                select(func.coalesce(func.avg(QueryAuditRecord.duration_ms), 0))
            )
        return {
            "total_queries": int(total),
            "by_status": {str(status): int(count) for status, count in rows},
            "average_duration_ms": round(float(average_duration or 0), 2),
            "stores_sensitive_payloads": False,
        }

    def record_feedback(self, feedback: FeedbackRequest, principal: Principal) -> bool:
        principal_hash = self._digest(principal.subject)
        with self.database.session() as session:
            if session.get(QueryAuditRecord, feedback.request_id) is None:
                return False
            key = (feedback.request_id, principal_hash)
            record = session.get(QueryFeedbackRecord, key)
            if record is None:
                record = QueryFeedbackRecord(
                    request_id=feedback.request_id,
                    principal_hash=principal_hash,
                    created_at=datetime.now(UTC),
                    rating=feedback.rating.value,
                    reason_code=feedback.reason.value,
                    expected_recipe_id=feedback.expected_recipe_id,
                )
                session.add(record)
            else:
                record.created_at = datetime.now(UTC)
                record.rating = feedback.rating.value
                record.reason_code = feedback.reason.value
                record.expected_recipe_id = feedback.expected_recipe_id
        return True

    def feedback_summary(self) -> FeedbackSummary:
        with self.database.session() as session:
            total = session.scalar(select(func.count()).select_from(QueryFeedbackRecord)) or 0
            ratings = session.execute(
                select(QueryFeedbackRecord.rating, func.count())
                .group_by(QueryFeedbackRecord.rating)
                .order_by(QueryFeedbackRecord.rating)
            ).all()
            reasons = session.execute(
                select(QueryFeedbackRecord.reason_code, func.count())
                .group_by(QueryFeedbackRecord.reason_code)
                .order_by(QueryFeedbackRecord.reason_code)
            ).all()
        return FeedbackSummary(
            total=int(total),
            by_rating={str(name): int(count) for name, count in ratings},
            by_reason={str(name): int(count) for name, count in reasons},
        )

    def _digest(self, value: str) -> str:
        return hmac.new(
            self.hmac_key,
            value.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()
