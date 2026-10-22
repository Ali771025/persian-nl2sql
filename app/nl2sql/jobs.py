from __future__ import annotations

from . import async_utils as asyncio
import inspect
import uuid
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime

from sqlalchemy import delete, select, update
from sqlalchemy.exc import IntegrityError

from .database import OperationalDatabase
from .db_models import AdminJobRecord
from .models import BackgroundJob, IngestResponse, JobState


class JobConflict(RuntimeError):
    def __init__(self, job_id: str):
        super().__init__(f"A job is already running: {job_id}")
        self.job_id = job_id


class JobRepository:
    def __init__(self, database: OperationalDatabase):
        self.database = database

    def upsert(self, job: BackgroundJob) -> None:
        record = AdminJobRecord(
            job_id=job.job_id,
            kind=job.kind,
            state=job.state.value,
            created_at=job.created_at,
            started_at=job.started_at,
            finished_at=job.finished_at,
            result=job.result.model_dump(mode="json") if job.result else None,
            error_code=job.error_code,
            phase=job.phase,
            progress_percent=job.progress_percent,
            heartbeat_at=job.heartbeat_at,
            snapshot_id=job.snapshot_id,
            attempt=job.attempt,
            correlation_id=job.correlation_id,
        )
        with self.database.session() as session:
            session.merge(record)

    def create(self, job: BackgroundJob) -> None:
        record = AdminJobRecord(
            job_id=job.job_id,
            kind=job.kind,
            state=job.state.value,
            created_at=job.created_at,
            phase=job.phase,
            progress_percent=job.progress_percent,
            heartbeat_at=job.heartbeat_at,
            snapshot_id=job.snapshot_id,
            attempt=job.attempt,
            correlation_id=job.correlation_id,
        )
        try:
            with self.database.session() as session:
                session.add(record)
                session.flush()
        except IntegrityError as exc:
            active = self.active(job.kind)
            raise JobConflict(active.job_id if active else "unknown") from exc

    def get(self, job_id: str) -> BackgroundJob | None:
        with self.database.session() as session:
            record = session.get(AdminJobRecord, job_id)
            return self._to_domain(record) if record else None

    def list(self, limit: int) -> list[BackgroundJob]:
        with self.database.session() as session:
            records = session.scalars(
                select(AdminJobRecord).order_by(AdminJobRecord.created_at.desc()).limit(limit)
            ).all()
            return [self._to_domain(record) for record in records]

    def active(self, kind: str) -> BackgroundJob | None:
        with self.database.session() as session:
            record = session.scalar(
                select(AdminJobRecord)
                .where(
                    AdminJobRecord.kind == kind,
                    AdminJobRecord.state.in_([JobState.QUEUED.value, JobState.RUNNING.value]),
                )
                .order_by(AdminJobRecord.created_at.desc())
                .limit(1)
            )
            return self._to_domain(record) if record else None

    def mark_interrupted(self) -> int:
        with self.database.session() as session:
            result = session.execute(
                update(AdminJobRecord)
                .where(AdminJobRecord.state.in_([JobState.QUEUED.value, JobState.RUNNING.value]))
                .values(
                    state=JobState.FAILED.value,
                    finished_at=datetime.now(UTC),
                    error_code="ServiceRestarted",
                    phase="interrupted",
                )
            )
            return int(result.rowcount or 0)

    def trim(self, history_limit: int) -> None:
        with self.database.session() as session:
            completed_ids = session.scalars(
                select(AdminJobRecord.job_id)
                .where(AdminJobRecord.state.in_([JobState.SUCCEEDED.value, JobState.FAILED.value]))
                .order_by(AdminJobRecord.created_at.desc())
                .offset(history_limit)
            ).all()
            if completed_ids:
                session.execute(delete(AdminJobRecord).where(AdminJobRecord.job_id.in_(completed_ids)))

    @staticmethod
    def _to_domain(record: AdminJobRecord) -> BackgroundJob:
        return BackgroundJob(
            job_id=record.job_id,
            kind=record.kind,
            state=JobState(record.state),
            created_at=record.created_at,
            started_at=record.started_at,
            finished_at=record.finished_at,
            result=IngestResponse.model_validate(record.result) if record.result else None,
            error_code=record.error_code,
            phase=record.phase,
            progress_percent=record.progress_percent,
            heartbeat_at=record.heartbeat_at,
            snapshot_id=record.snapshot_id,
            attempt=record.attempt,
            correlation_id=record.correlation_id,
        )


class BackgroundJobManager:
    """In-process runner with optional SQLAlchemy-backed job history."""

    def __init__(
        self,
        history_limit: int = 50,
        database: OperationalDatabase | None = None,
    ):
        self.history_limit = history_limit
        self._jobs: dict[str, BackgroundJob] = {}
        self._tasks: set[asyncio.Task[None]] = set()
        self._lock = asyncio.Lock()
        self.repository = JobRepository(database) if database else None
        if self.repository:
            self.repository.mark_interrupted()

    async def start_ingest(
        self,
        runner: Callable[..., Awaitable[IngestResponse]],
    ) -> BackgroundJob:
        async with self._lock:
            active = (
                await asyncio.to_thread(self.repository.active, "ingest")
                if self.repository
                else next(
                    (
                        job
                        for job in self._jobs.values()
                        if job.kind == "ingest" and job.state in {JobState.QUEUED, JobState.RUNNING}
                    ),
                    None,
                )
            )
            if active:
                raise JobConflict(active.job_id)
            job = BackgroundJob(
                job_id=uuid.uuid4().hex,
                kind="ingest",
                state=JobState.QUEUED,
                created_at=datetime.now(UTC),
                phase="queued",
                progress_percent=0,
                heartbeat_at=datetime.now(UTC),
                correlation_id=uuid.uuid4().hex,
            )
            self._jobs[job.job_id] = job
            if self.repository:
                try:
                    await asyncio.to_thread(self.repository.create, job)
                except JobConflict:
                    self._jobs.pop(job.job_id, None)
                    raise
            task = asyncio.create_task(self._run(job.job_id, runner))
            self._tasks.add(task)
            task.add_done_callback(self._tasks.discard)
            if self.repository:
                await asyncio.to_thread(self.repository.trim, self.history_limit)
            else:
                self._trim()
            return job.model_copy(deep=True)

    async def get(self, job_id: str) -> BackgroundJob | None:
        if self.repository:
            return await asyncio.to_thread(self.repository.get, job_id)
        async with self._lock:
            job = self._jobs.get(job_id)
            return job.model_copy(deep=True) if job else None

    async def list(self, limit: int = 20) -> list[BackgroundJob]:
        if self.repository:
            return await asyncio.to_thread(self.repository.list, limit)
        async with self._lock:
            jobs = sorted(
                self._jobs.values(),
                key=lambda item: item.created_at,
                reverse=True,
            )[:limit]
            return [job.model_copy(deep=True) for job in jobs]

    async def _run(
        self,
        job_id: str,
        runner: Callable[..., Awaitable[IngestResponse]],
    ) -> None:
        async with self._lock:
            job = self._jobs[job_id]
            self._jobs[job_id] = job.model_copy(
                update={
                    "state": JobState.RUNNING,
                    "started_at": datetime.now(UTC),
                    "heartbeat_at": datetime.now(UTC),
                    "phase": "starting",
                    "progress_percent": 1,
                }
            )
            running = self._jobs[job_id]
        if self.repository:
            await asyncio.to_thread(self.repository.upsert, running)
        try:
            reporter = self._reporter(job_id)
            result = (
                await runner(reporter) if len(inspect.signature(runner).parameters) > 0 else await runner()
            )
        except asyncio.CancelledError:
            await self._mark_failed(job_id, "ServiceShutdown", "interrupted")
            raise
        except Exception as exc:
            await self._mark_failed(job_id, type(exc).__name__, "failed")
            return
        async with self._lock:
            job = self._jobs[job_id]
            self._jobs[job_id] = job.model_copy(
                update={
                    "state": JobState.SUCCEEDED,
                    "finished_at": datetime.now(UTC),
                    "result": result,
                    "phase": "published",
                    "progress_percent": 100,
                    "heartbeat_at": datetime.now(UTC),
                    "snapshot_id": result.snapshot_id or result.content_hash,
                }
            )
            succeeded = self._jobs[job_id]
        if self.repository:
            await asyncio.to_thread(self.repository.upsert, succeeded)

    def _reporter(self, job_id: str):
        async def report(phase: str, progress_percent: int, snapshot_id: str = "") -> None:
            async with self._lock:
                job = self._jobs[job_id]
                updated = job.model_copy(
                    update={
                        "phase": phase,
                        "progress_percent": max(0, min(99, progress_percent)),
                        "heartbeat_at": datetime.now(UTC),
                        "snapshot_id": snapshot_id or job.snapshot_id,
                    }
                )
                self._jobs[job_id] = updated
            if self.repository:
                await asyncio.to_thread(self.repository.upsert, updated)

        return report

    async def _mark_failed(self, job_id: str, error_code: str, phase: str) -> None:
        async with self._lock:
            job = self._jobs[job_id]
            failed = job.model_copy(
                update={
                    "state": JobState.FAILED,
                    "finished_at": datetime.now(UTC),
                    "heartbeat_at": datetime.now(UTC),
                    "error_code": error_code,
                    "phase": phase,
                }
            )
            self._jobs[job_id] = failed
        if self.repository:
            await asyncio.to_thread(self.repository.upsert, failed)

    async def shutdown(self) -> None:
        tasks = list(self._tasks)
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    def _trim(self) -> None:
        completed = sorted(
            (job for job in self._jobs.values() if job.state in {JobState.SUCCEEDED, JobState.FAILED}),
            key=lambda item: item.created_at,
            reverse=True,
        )
        for job in completed[self.history_limit :]:
            self._jobs.pop(job.job_id, None)
