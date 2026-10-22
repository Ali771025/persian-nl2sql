from __future__ import annotations

import json
import logging
import os
import shutil
import threading
import time
import uuid
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from pathlib import Path

from . import __version__, async_utils as asyncio
from .async_utils import ensure_blocking_executor
from .audit import AuditRepository, QueryAuditEvent
from .config import Settings
from .context import ContextBuilder
from .database import OperationalDatabase
from .embeddings import build_embedder
from .executor import ReadOnlySQLServerExecutor
from .index import HybridKnowledgeBase
from .ingestion import DocumentIngestor
from .jobs import BackgroundJobManager
from .knowledge_factory import build_knowledge_base
from .llm import SQLGenerator, build_sql_generator
from .models import (
    ColumnInfo,
    DatabaseStatus,
    DomainItemSummary,
    DomainOverviewResponse,
    DomainRecipeSummary,
    ExecutionResult,
    FeedbackRequest,
    FeedbackResponse,
    FeedbackSummary,
    IndexStatus,
    IngestResponse,
    KnowledgeConsistencyResponse,
    PipelineTrace,
    Principal,
    ProductionCheckItem,
    ProductionCheckResponse,
    QueryRequest,
    QueryResponse,
    SchemaGraphEdge,
    SchemaGraphNode,
    SchemaGraphResponse,
    SchemaOverviewResponse,
    SchemaTableSummary,
    SchemaVersionsResponse,
    ServiceMeta,
    SQLDraft,
)
from .normalization import QueryAnalyzer
from .planner import SemanticPlanner
from .policy import RolePolicy
from .rate_limit import InMemoryRateLimiter, RedisRateLimiter
from .reranker import Reranker, build_reranker
from .retrieval import (
    build_retrieval_queries,
    multi_query_search,
    select_context_hits,
)
from .security import AccessController, SensitiveDataMasker
from .semantic import Commission100SemanticLayer
from .sql_normalization import strip_unindexed_qualifiers
from .validator import TSQLValidator

logger = logging.getLogger("nl2sql.service")

LLM_PROVIDER_LABELS = {
    "openai": "OpenAI",
    "gapgpt": "GapGPT",
    "deepseek": "DeepSeek",
    "qwen": "Qwen",
    "openai_compatible": "سازگار با OpenAI / محلی",
}


def _describe_llm_failure(exc: Exception) -> tuple[str, str]:
    """Return a safe, actionable API status without exposing provider details."""
    error_code = str(getattr(exc, "code", "") or "").lower()
    status_code = getattr(exc, "status_code", None)
    exception_name = type(exc).__name__.lower()

    if error_code in {"billing_not_active", "insufficient_quota"} or status_code == 402:
        return (
            "llm_billing_inactive",
            "حساب یا پروژه سرویس LLM اعتبار فعال ندارد؛ Billing و اعتبار Provider مربوط به این کلید را بررسی کنید.",
        )
    if status_code in {401, 403} or "authentication" in exception_name:
        return (
            "llm_authentication_failed",
            "کلید سرویس LLM نامعتبر است یا به مدل انتخاب‌شده دسترسی ندارد.",
        )
    if status_code == 429 or "ratelimit" in exception_name:
        return (
            "llm_rate_limited",
            "سهمیه یا محدودیت نرخ سرویس LLM مانع تولید SQL شد؛ کمی بعد دوباره تلاش کنید.",
        )
    if "timeout" in exception_name or isinstance(exc, TimeoutError):
        return (
            "llm_timeout",
            "ارتباط با Provider مدل در مهلت تعیین‌شده پاسخ نداد؛ دسترسی شبکه و endpoint را بررسی کنید.",
        )
    return ("generation_failed", "تولید SQL با خطا مواجه شد.")


class StaleFencingToken(RuntimeError):
    pass


class NL2SQLService:
    def __init__(
        self,
        settings: Settings,
        knowledge_base: HybridKnowledgeBase,
        reranker: Reranker,
        generator: SQLGenerator | None,
        executor: ReadOnlySQLServerExecutor | None = None,
        audit_repository: AuditRepository | None = None,
        operational_database: OperationalDatabase | None = None,
        generators: dict[tuple[str, str], SQLGenerator] | None = None,
    ):
        self.settings = settings
        self.knowledge_base = knowledge_base
        self.reranker = reranker
        self.generator = generator
        self.generators = dict(generators or {})
        if generator is not None:
            self.generators.setdefault(
                (generator.provider_name, generator.model_name),
                generator,
            )
        self.executor = executor
        self.analyzer = QueryAnalyzer()
        self.domain_semantic = Commission100SemanticLayer()
        self.semantic = SemanticPlanner(self.domain_semantic)
        self.role_policy = RolePolicy(
            settings.role_policy_path,
            fallback_semantic=self.domain_semantic,
        )
        self.access_controller = AccessController(self.role_policy)
        self.masker = SensitiveDataMasker(self.role_policy.mask_patterns)
        self.audit_repository = audit_repository
        self.operational_database = operational_database or (
            audit_repository.database if audit_repository else None
        )
        cache = getattr(knowledge_base, "cache", None)
        self.rate_limiter = (
            RedisRateLimiter(
                cache,
                settings.rate_limit_per_minute,
                fail_open=settings.distributed_fail_open,
            )
            if cache is not None
            else InMemoryRateLimiter(settings.rate_limit_per_minute)
        )
        self.job_manager = BackgroundJobManager(database=self.operational_database)
        self.context_builder = ContextBuilder(max_chars=settings.max_context_chars)
        self._ingest_lock = asyncio.Lock()
        self._knowledge_refresh_lock = threading.Lock()
        self._closed = False

    @classmethod
    def from_settings(cls, settings: Settings) -> NL2SQLService:
        embedder = build_embedder(
            settings.embedding_provider,
            settings.embedding_model,
            settings.embedding_device,
            settings.embedding_batch_size,
            settings.embedding_revision,
        )
        knowledge_base = build_knowledge_base(settings, embedder)
        if (settings.index_dir / "manifest.json").exists() or (settings.index_dir / "CURRENT").exists():
            try:
                knowledge_base.load()
            except RuntimeError as exc:
                if "Configured embedding model differs from the built index" not in str(exc):
                    raise
                logger.warning(
                    "knowledge_index_embedding_mismatch",
                    extra={"index_dir": str(settings.index_dir)},
                )
                # Keep the API available for authenticated re-ingestion. The old
                # immutable snapshot remains until a compatible one is published.
                knowledge_base = knowledge_base.empty_clone()
        reranker = build_reranker(
            settings.reranker_provider,
            settings.reranker_model,
            settings.reranker_device,
            settings.reranker_revision,
            settings.reranker_batch_size,
            settings.reranker_max_length,
            settings.reranker_onnx_model,
            settings.reranker_onnx_revision,
            settings.reranker_onnx_file,
        )
        if settings.reranker_warmup:
            reranker.warmup()
        generators: dict[tuple[str, str], SQLGenerator] = {}
        for provider in ("openai", "gapgpt", "deepseek", "qwen", "openai_compatible"):
            api_key = settings.llm_api_key_for(provider)
            base_url = settings.llm_base_url_for(provider)
            if not api_key or (provider == "openai_compatible" and not base_url):
                continue
            for model_name in settings.llm_models_for(provider):
                built = build_sql_generator(
                    provider=provider,
                    api_key=api_key,
                    model_name=model_name,
                    base_url=base_url,
                    reasoning_effort=settings.llm_reasoning_effort,
                    thinking_enabled=settings.llm_thinking_enabled,
                    timeout_seconds=settings.llm_timeout_seconds,
                )
                if built is not None:
                    generators[(provider, model_name)] = built
        generator = generators.get((settings.llm_provider, settings.resolved_llm_model))
        executor = None
        if settings.allow_execution:
            executor = ReadOnlySQLServerExecutor(
                database_url=settings.database_url,
                max_rows=settings.max_rows,
                timeout_seconds=settings.query_timeout_seconds,
                lock_timeout_ms=settings.lock_timeout_ms,
            )
        operational_database = OperationalDatabase(settings.resolved_operational_database_url)
        if settings.operational_auto_create:
            operational_database.create_schema_for_tests()
        audit_repository = None
        if settings.audit_enabled:
            audit_repository = AuditRepository(
                operational_database,
                settings.audit_hmac_key,
            )
        return cls(
            settings,
            knowledge_base,
            reranker,
            generator,
            executor,
            audit_repository,
            operational_database,
            generators,
        )

    async def ingest(
        self,
        docs_root: Path | None = None,
        *,
        progress_callback: Callable[[str, int, str], Awaitable[None]] | None = None,
    ) -> IngestResponse:
        async def progress(phase: str, percent: int, snapshot_id: str = "") -> None:
            if progress_callback is not None:
                try:
                    await progress_callback(phase, percent, snapshot_id)
                except Exception:
                    # Telemetry persistence must not control the knowledge transaction.
                    return

        ensure_blocking_executor()
        async with self._ingest_lock:
            await progress("acquiring_lease", 3)
            active_knowledge_base = self.knowledge_base
            distributed_lock = None
            heartbeat_stop = asyncio.Event()
            lease_lost = asyncio.Event()
            heartbeat_task: asyncio.Task[None] | None = None
            if hasattr(active_knowledge_base, "acquire_ingestion_lock"):
                distributed_lock = await asyncio.to_thread(active_knowledge_base.acquire_ingestion_lock)
                if distributed_lock is None:
                    raise RuntimeError("A distributed knowledge ingestion is already active")
                heartbeat_task = asyncio.create_task(
                    self._lease_heartbeat(
                        active_knowledge_base,
                        distributed_lock,
                        heartbeat_stop,
                        lease_lost,
                    )
                )
            staged_knowledge_base = None
            distributed_stage = None
            staging_dir: Path | None = None
            published_pointer = False
            commit_published = False
            previous_pointer: str | None = None
            try:
                await progress("scanning_documents", 10)
                root = (docs_root or self.settings.docs_root).resolve()
                catalog, files_scanned = await asyncio.to_thread(self._scan_documents, root)
                await progress("building_local_snapshot", 25, catalog.content_hash)
                self._assert_lease(lease_lost)
                staging_dir = self._create_staging_index(active_knowledge_base)
                staged_knowledge_base = active_knowledge_base.clone_at(staging_dir)
                build_method = getattr(
                    staged_knowledge_base,
                    "build_local_incremental",
                    staged_knowledge_base.build_incremental,
                )
                build_stats = await asyncio.to_thread(
                    build_method,
                    catalog,
                )
                self._assert_lease(lease_lost)
                if hasattr(staged_knowledge_base, "stage_distributed"):
                    await progress("staging_distributed_stores", 55, catalog.content_hash)
                    distributed_stage = await asyncio.to_thread(
                        staged_knowledge_base.stage_distributed,
                        catalog,
                    )
                self._assert_lease(lease_lost)
                if distributed_stage is not None:
                    await progress("activating_read_heads", 78, catalog.content_hash)
                    await asyncio.to_thread(
                        staged_knowledge_base.activate_distributed,
                        distributed_stage,
                    )
                previous_pointer = self._read_current_pointer(active_knowledge_base.index_dir)
                await progress("publishing_local_pointer", 88, catalog.content_hash)
                await asyncio.to_thread(
                    self._publish_local_snapshot,
                    staged_knowledge_base,
                    staging_dir,
                )
                published_pointer = True
                self._assert_lease(lease_lost)
                fencing_token = (
                    active_knowledge_base.ingestion_fencing_token(distributed_lock)
                    if hasattr(active_knowledge_base, "ingestion_fencing_token")
                    else 0
                )
                if hasattr(staged_knowledge_base, "publish_commit"):
                    accepted = await asyncio.to_thread(
                        staged_knowledge_base.publish_commit,
                        fencing_token,
                    )
                    if not accepted:
                        raise StaleFencingToken("A newer ingestion fencing token rejected this publication")
                commit_published = True
                self.knowledge_base = staged_knowledge_base
                if distributed_stage is not None:
                    try:
                        await asyncio.to_thread(
                            staged_knowledge_base.finalize_distributed,
                            distributed_stage,
                        )
                    except Exception:
                        # Retention cleanup is post-commit housekeeping and must not undo publication.
                        staged_knowledge_base.sync_report.errors.append("snapshot_cleanup_failed")
                try:
                    await asyncio.to_thread(
                        self._prune_local_snapshots,
                        staged_knowledge_base.index_dir,
                        self.settings.local_retained_snapshots,
                    )
                except Exception:
                    if sync_report := getattr(staged_knowledge_base, "sync_report", None):
                        sync_report.errors.append("local_snapshot_cleanup_failed")
                await progress("commit_marker_published", 97, catalog.content_hash)
            except StaleFencingToken:
                reconciled_knowledge_base = active_knowledge_base
                if published_pointer:
                    authoritative = None
                    cache = getattr(staged_knowledge_base, "cache", None)
                    if cache is not None:
                        try:
                            authoritative = await asyncio.to_thread(cache.active_snapshot)
                        except Exception:
                            authoritative = None
                    restored = await asyncio.to_thread(
                        self._restore_authoritative_pointer,
                        active_knowledge_base.index_dir,
                        authoritative,
                    )
                    if not restored:
                        await asyncio.to_thread(
                            self._restore_current_pointer,
                            active_knowledge_base.index_dir,
                            previous_pointer,
                        )
                    else:
                        reconciled_knowledge_base = active_knowledge_base.clone_at(
                            active_knowledge_base.index_dir
                        )
                        await asyncio.to_thread(reconciled_knowledge_base.load)
                if distributed_stage is not None and hasattr(staged_knowledge_base, "abort_distributed"):
                    await asyncio.to_thread(
                        staged_knowledge_base.abort_distributed,
                        distributed_stage,
                    )
                self.knowledge_base = reconciled_knowledge_base
                raise
            except BaseException:
                if commit_published:
                    self.knowledge_base = staged_knowledge_base
                    raise
                if published_pointer:
                    await asyncio.to_thread(
                        self._restore_current_pointer,
                        active_knowledge_base.index_dir,
                        previous_pointer,
                    )
                if distributed_stage is not None and staged_knowledge_base is not None:
                    await asyncio.to_thread(
                        staged_knowledge_base.rollback_distributed,
                        distributed_stage,
                    )
                self.knowledge_base = active_knowledge_base
                raise
            finally:
                heartbeat_stop.set()
                if heartbeat_task is not None:
                    await heartbeat_task
                if hasattr(active_knowledge_base, "release_ingestion_lock"):
                    await asyncio.to_thread(
                        active_knowledge_base.release_ingestion_lock,
                        distributed_lock,
                    )
                if staging_dir is not None and staging_dir.exists():
                    await asyncio.to_thread(shutil.rmtree, staging_dir, True)
        sync_report = getattr(self.knowledge_base, "sync_report", None)
        return IngestResponse(
            files_scanned=files_scanned,
            artifacts_indexed=len(catalog.artifacts),
            tables_found=sum(1 for item in catalog.artifacts if item.kind.value == "table"),
            foreign_keys_found=len(catalog.foreign_keys),
            sql_examples_found=sum(1 for item in catalog.artifacts if item.kind.value == "sql_example"),
            content_hash=catalog.content_hash,
            embedding_model=self.knowledge_base.embedder.model_name,
            artifacts_added=build_stats.added,
            artifacts_updated=build_stats.updated,
            artifacts_reused=build_stats.reused,
            artifacts_deleted=build_stats.deleted,
            vector_reuse_ratio=round(build_stats.reuse_ratio, 6),
            schema_versions=catalog.schema_versions,
            snapshot_version=catalog.snapshot_version,
            active_schema_version=catalog.active_schema_version,
            qdrant_points=sync_report.qdrant_points if sync_report else 0,
            graph_nodes=sync_report.graph_nodes if sync_report else 0,
            distributed_errors=list(sync_report.errors) if sync_report else [],
            snapshot_id=catalog.content_hash,
            generation=int(self.knowledge_base.manifest.get("generation", 0)),
            publication_phase=sync_report.phase if sync_report else "published",
            fencing_token=fencing_token,
        )

    async def _lease_heartbeat(
        self,
        knowledge_base,
        lock: object,
        stop: asyncio.Event,
        lost: asyncio.Event,
    ) -> None:
        interval = max(1.0, float(self.settings.redis_ingest_lock_seconds) / 3)
        while True:
            try:
                await asyncio.wait_for(stop.wait(), timeout=interval)
                return
            except TimeoutError:
                if not hasattr(knowledge_base, "renew_ingestion_lock"):
                    continue
                renewed = await asyncio.to_thread(knowledge_base.renew_ingestion_lock, lock)
                if not renewed:
                    lost.set()
                    return

    @staticmethod
    def _assert_lease(lost: asyncio.Event) -> None:
        if lost.is_set():
            raise RuntimeError("Distributed ingestion lease was lost before publication")

    @staticmethod
    def _create_staging_index(knowledge_base: HybridKnowledgeBase) -> Path:
        root = knowledge_base.index_dir
        staging = root / ".staging" / uuid.uuid4().hex
        staging.mkdir(parents=True, exist_ok=False)
        if knowledge_base.is_ready:
            source = knowledge_base.storage_dir
            for name in ("artifacts.jsonl", "embeddings.npy", "foreign_keys.json", "manifest.json"):
                shutil.copy2(source / name, staging / name)
        return staging

    @staticmethod
    def _read_current_pointer(index_root: Path) -> str | None:
        pointer = index_root / "CURRENT"
        return pointer.read_text(encoding="utf-8").strip() if pointer.exists() else None

    @staticmethod
    def _publish_local_snapshot(knowledge_base: HybridKnowledgeBase, staging_dir: Path) -> None:
        root = knowledge_base.index_dir.parents[1]
        generation = int(knowledge_base.manifest.get("generation", 0))
        snapshot_id = str(knowledge_base.manifest.get("content_hash", ""))
        # The attempt suffix keeps a rejected publication retryable without deleting recovery data.
        name = f"g{generation:08d}_{snapshot_id[:12]}_{uuid.uuid4().hex[:8]}"
        snapshots = root / "snapshots"
        snapshots.mkdir(parents=True, exist_ok=True)
        destination = snapshots / name
        os.replace(staging_dir, destination)
        temporary = root / f"CURRENT.{uuid.uuid4().hex}.tmp"
        temporary.write_text(name, encoding="utf-8")
        os.replace(temporary, root / "CURRENT")
        knowledge_base.index_dir = root

    @staticmethod
    def _restore_current_pointer(index_root: Path, previous: str | None) -> None:
        pointer = index_root / "CURRENT"
        if previous is None:
            pointer.unlink(missing_ok=True)
            return
        temporary = index_root / f"CURRENT.rollback.{uuid.uuid4().hex}.tmp"
        temporary.write_text(previous, encoding="utf-8")
        os.replace(temporary, pointer)

    @classmethod
    def _restore_authoritative_pointer(
        cls,
        index_root: Path,
        authoritative: dict[str, object] | None,
    ) -> bool:
        if not authoritative:
            return False
        expected_id = str(authoritative.get("snapshot_id", ""))
        expected_generation = int(authoritative.get("generation", -1))
        candidates: list[tuple[int, str]] = []
        snapshots = index_root / "snapshots"
        if not snapshots.exists():
            return False
        for directory in snapshots.iterdir():
            manifest_path = directory / "manifest.json"
            if not directory.is_dir() or not manifest_path.exists():
                continue
            try:
                manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            if (
                str(manifest.get("content_hash", "")) == expected_id
                and int(manifest.get("generation", -1)) == expected_generation
            ):
                candidates.append((directory.stat().st_mtime_ns, directory.name))
        if not candidates:
            return False
        cls._restore_current_pointer(index_root, max(candidates)[1])
        return True

    @staticmethod
    def _prune_local_snapshots(index_root: Path, retained: int) -> None:
        snapshots = index_root / "snapshots"
        if not snapshots.exists():
            return
        current = NL2SQLService._read_current_pointer(index_root)
        directories = sorted(
            (item for item in snapshots.iterdir() if item.is_dir()),
            key=lambda item: item.stat().st_mtime_ns,
            reverse=True,
        )
        keep = {item.name for item in directories[: max(1, retained)]}
        if current:
            keep.add(current)
        for directory in directories:
            if directory.name not in keep:
                shutil.rmtree(directory)

    @staticmethod
    def _scan_documents(root: Path):
        ingestor = DocumentIngestor(root)
        return ingestor.scan(), ingestor.files_scanned

    async def query(
        self,
        request: QueryRequest,
        principal: Principal | None = None,
    ) -> QueryResponse:
        ensure_blocking_executor()
        principal = principal or Principal(subject="internal", roles=["system_admin"])
        await asyncio.to_thread(self.synchronize_knowledge_head)
        # A request is pinned to one immutable knowledge object even when an ingest publishes mid-flight.
        knowledge_base = self.knowledge_base
        request_id = uuid.uuid4().hex
        trace: list[PipelineTrace] = []
        if not knowledge_base.is_ready:
            return self._error_response(
                request_id,
                request,
                "index_not_ready",
                "ابتدا اسناد را index کنید.",
                trace,
            )
        if (
            self.settings.snapshot_consistency_required
            and hasattr(knowledge_base, "publication_matches_active")
            and not await asyncio.to_thread(knowledge_base.publication_matches_active)
        ):
            return self._error_response(
                request_id,
                request,
                "knowledge_snapshot_inconsistent",
                "نسخه دانش این replica با snapshot منتشرشده همگام نیست؛ درخواست موقتاً متوقف شد.",
                trace,
            )
        available_versions = set(knowledge_base.manifest.get("schema_versions", []))
        if request.schema_version and request.schema_version not in available_versions:
            return self._error_response(
                request_id,
                request,
                "schema_version_not_found",
                "نسخه درخواستی در index موجود نیست؛ ابتدا همان نسخه اسناد را ingest کنید.",
                trace,
            )
        schema_version = request.schema_version or str(
            knowledge_base.manifest.get(
                "active_schema_version",
                knowledge_base.manifest.get("snapshot_version", ""),
            )
        )
        version_tables = knowledge_base.known_tables_for_version(schema_version)
        version_columns = knowledge_base.table_columns_for_version(schema_version)
        started = time.perf_counter()
        analysis = self.analyzer.analyze(request.question, version_tables)
        trace.append(self._trace("intent_and_entities", started, analysis.intent.value))

        started = time.perf_counter()
        domain_plan = self.semantic.plan(request.question, analysis)
        trace.append(
            self._trace(
                "domain_semantic_plan",
                started,
                f"concepts={len(domain_plan.concepts)} metrics={len(domain_plan.metrics)}",
            )
        )
        preflight_tables = list(dict.fromkeys(domain_plan.required_tables + analysis.table_mentions))
        preflight = self.access_controller.preflight(preflight_tables, principal)
        if not preflight.allowed:
            trace.append(self._trace("rbac_preflight", time.perf_counter(), "denied"))
            return QueryResponse(
                request_id=request_id,
                status="access_denied",
                question=request.question,
                analysis=analysis,
                domain_plan=domain_plan,
                access=preflight,
                error="نقش فعلی اجازه دسترسی به دامنه داده درخواستی را ندارد.",
                trace=trace,
            )

        started = time.perf_counter()
        retrieval_queries = build_retrieval_queries(
            analysis,
            domain_plan,
            self.settings.graph_depth,
        )
        candidates = await asyncio.to_thread(
            multi_query_search,
            knowledge_base,
            retrieval_queries,
            self.settings.retrieval_top_k,
            schema_version,
        )
        candidates = self._filter_hits_for_principal(candidates, principal)
        trace.append(
            self._trace(
                "hybrid_retrieval_and_fk_graph",
                started,
                f"queries={len(retrieval_queries)} candidates={len(candidates)}",
            )
        )

        started = time.perf_counter()
        reranker_candidates = candidates[: self.settings.reranker_candidate_k]
        reranked_hits = await asyncio.to_thread(
            self.reranker.rerank,
            analysis.normalized_question,
            reranker_candidates,
            self.settings.rerank_top_k,
        )
        hits = select_context_hits(
            reranked_hits,
            candidates,
            domain_plan.required_tables,
            self.settings.rerank_top_k,
        )
        trace.append(
            self._trace(
                "rerank",
                started,
                (
                    f"model={self.reranker.model_name} "
                    f"candidates={len(reranker_candidates)} selected={len(hits)}"
                ),
            )
        )

        retrieval_candidates = list(
            dict.fromkeys(hit.artifact.source_path for hit in hits)
        )
        built_context = self.context_builder.build_with_sources(
            analysis,
            hits,
            knowledge_base.foreign_keys,
            domain_plan,
            schema_version,
        )
        context = built_context.text
        sources = built_context.sources

        started = time.perf_counter()
        draft = self.semantic.deterministic_draft(
            request.question,
            set(version_tables),
        )
        selected_generator: SQLGenerator | None = None
        generation_mode = "domain_recipe" if draft else "llm"
        if draft is not None:
            trace.append(
                self._trace(
                    "deterministic_domain_recipe",
                    started,
                    domain_plan.matched_recipe_id or "matched",
                )
            )
        else:
            selected_generator = self._select_generator(request)
            if selected_generator is None:
                requested_provider = request.llm_provider or self.settings.llm_provider
                requested_model = request.llm_model or (
                    self.settings.resolved_llm_model
                    if requested_provider == self.settings.llm_provider
                    else ""
                )
                available_for_provider = any(
                    provider == requested_provider for provider, _ in self.generators
                )
                if (
                    request.llm_provider is None
                    and request.llm_model is None
                    and not self.generators
                ):
                    status = "llm_not_configured"
                    message = "این درخواست recipe قطعی ندارد و کلید سرویس LLM تنظیم نشده است."
                elif available_for_provider and requested_model:
                    status = "llm_model_not_allowed"
                    message = "مدل انتخاب‌شده در فهرست مجاز این Provider نیست."
                else:
                    status = "llm_provider_not_available"
                    message = "Provider انتخاب‌شده در بک‌اند کلید فعال یا تنظیم معتبر ندارد."
                return self._error_response(
                    request_id,
                    request,
                    status,
                    message,
                    trace,
                    analysis=analysis,
                    sources=sources,
                    domain_plan=domain_plan,
                    generation_mode="llm",
                    retrieval_candidates=retrieval_candidates,
                    context=context if request.include_context else None,
                    resolved_schema_version=schema_version,
                    knowledge_base=knowledge_base,
                    failure_stage="sql_generation",
                    llm_provider=requested_provider,
                    llm_model=requested_model or None,
                )
            try:
                draft = await selected_generator.generate(request.question, analysis, context)
            except Exception as exc:
                logger.warning(
                    "llm_generation_failed",
                    extra={
                        "request_id": request_id,
                        "llm_provider": selected_generator.provider_name,
                        "llm_model": selected_generator.model_name,
                        "error_type": type(exc).__name__,
                        "error_detail": str(exc.__cause__ or exc)[:1_000],
                    },
                    exc_info=True,
                )
                trace.append(self._trace("llm_generation", started, type(exc).__name__))
                failure_status, failure_message = _describe_llm_failure(exc)
                return self._error_response(
                    request_id,
                    request,
                    failure_status,
                    failure_message,
                    trace,
                    analysis=analysis,
                    sources=sources,
                    domain_plan=domain_plan,
                    generation_mode="llm",
                    retrieval_candidates=retrieval_candidates,
                    context=context if request.include_context else None,
                    resolved_schema_version=schema_version,
                    knowledge_base=knowledge_base,
                    failure_stage="sql_generation",
                    llm_provider=selected_generator.provider_name,
                    llm_model=selected_generator.model_name,
                )
            trace.append(
                self._trace(
                    "llm_generation",
                    started,
                    f"{selected_generator.provider_name}/{selected_generator.model_name}",
                )
            )

        if draft.needs_clarification:
            return QueryResponse(
                request_id=request_id,
                status="needs_clarification",
                question=request.question,
                analysis=analysis,
                domain_plan=domain_plan,
                generation_mode=generation_mode,
                explanation_fa=draft.explanation_fa,
                confidence=draft.confidence,
                clarification_question=draft.clarification_question,
                retrieved_sources=sources,
                retrieval_candidates=retrieval_candidates,
                context_sources=sources,
                context=context if request.include_context else None,
                trace=trace,
                schema_version=schema_version,
                knowledge_snapshot_id=str(knowledge_base.manifest.get("content_hash", "")),
                knowledge_generation=int(knowledge_base.manifest.get("generation", 0)),
            )

        validator = TSQLValidator(
            version_tables,
            strict_schema=self.settings.strict_schema_validation,
            known_columns=version_columns,
            foreign_keys=[
                edge
                for edge in knowledge_base.foreign_keys
                if (
                    not schema_version
                    or schema_version.startswith("snapshot-")
                    or edge.document_version in {"unversioned", schema_version}
                )
            ],
        )
        repairs = 0
        result: ExecutionResult | None = None
        validation = None
        access = None

        while True:
            normalized_sql, qualifiers_removed = strip_unindexed_qualifiers(
                draft.sql,
                version_tables,
            )
            if qualifiers_removed:
                draft = draft.model_copy(update={"sql": normalized_sql})
                trace.append(
                    PipelineTrace(
                        stage="table_qualifier_normalization",
                        duration_ms=0,
                        detail="removed_unindexed_prefixes_from_known_tables",
                    )
                )
            started = time.perf_counter()
            validation = validator.validate(draft.sql, draft.parameter_dict())
            trace.append(self._trace("sql_validation", started, "valid" if validation.valid else "invalid"))
            if not validation.valid:
                if repairs >= self.settings.max_repair_attempts:
                    return QueryResponse(
                        request_id=request_id,
                        status="validation_failed",
                        question=request.question,
                        analysis=analysis,
                        domain_plan=domain_plan,
                        generation_mode=generation_mode,
                        sql=draft.sql,
                        parameters=draft.parameter_dict(),
                        explanation_fa=draft.explanation_fa,
                        confidence=draft.confidence,
                        validation=validation,
                        error="SQL تولیدشده پس از تلاش اصلاح نیز معتبر نبود.",
                        retrieved_sources=sources,
                        context=context if request.include_context else None,
                        trace=trace,
                    )
                if selected_generator is None:
                    return QueryResponse(
                        request_id=request_id,
                        status="validation_failed",
                        question=request.question,
                        analysis=analysis,
                        domain_plan=domain_plan,
                        generation_mode=generation_mode,
                        sql=draft.sql,
                        parameters=draft.parameter_dict(),
                        explanation_fa=draft.explanation_fa,
                        confidence=draft.confidence,
                        validation=validation,
                        error="recipe تخصصی با schema فعلی معتبر نبود.",
                        retrieved_sources=sources,
                        trace=trace,
                    )
                try:
                    draft = await self._repair(
                        selected_generator,
                        request,
                        analysis,
                        context,
                        draft,
                        "; ".join(validation.errors),
                        trace,
                    )
                except Exception as exc:
                    trace.append(
                        PipelineTrace(stage="bounded_repair", duration_ms=0, detail=type(exc).__name__)
                    )
                    return self._repair_failure_response(
                        request_id, request, analysis, draft, validation, sources, context, trace
                    )
                repairs += 1
                if draft.needs_clarification:
                    return self._clarification_response(
                        request_id, request, analysis, draft, sources, context, trace
                    )
                continue

            base_draft = draft
            started = time.perf_counter()
            draft, access = self.access_controller.enforce(
                base_draft,
                validation,
                principal,
                version_columns,
            )
            trace.append(
                self._trace(
                    "rbac_abac_enforcement",
                    started,
                    "allowed" if access.allowed else "denied",
                )
            )
            if not access.allowed:
                return QueryResponse(
                    request_id=request_id,
                    status="access_denied",
                    question=request.question,
                    analysis=analysis,
                    domain_plan=domain_plan,
                    generation_mode=generation_mode,
                    access=access,
                    explanation_fa=draft.explanation_fa,
                    confidence=draft.confidence,
                    validation=validation,
                    error="دسترسی به این داده یا دامنه برای نقش فعلی مجاز نیست.",
                    retrieved_sources=sources,
                    context=context if request.include_context else None,
                    trace=trace,
                )
            if draft.sql != base_draft.sql:
                started = time.perf_counter()
                validation = validator.validate(draft.sql, draft.parameter_dict())
                trace.append(
                    self._trace(
                        "scope_revalidation",
                        started,
                        "valid" if validation.valid else "invalid",
                    )
                )
                if not validation.valid:
                    access = access.model_copy(
                        update={
                            "allowed": False,
                            "errors": ["Server-side row scope failed validation"],
                        }
                    )
                    return QueryResponse(
                        request_id=request_id,
                        status="access_denied",
                        question=request.question,
                        analysis=analysis,
                        domain_plan=domain_plan,
                        generation_mode=generation_mode,
                        access=access,
                        validation=validation,
                        error="اعمال محدودیت سطح ردیف با schema فعلی ممکن نبود.",
                        retrieved_sources=sources,
                        trace=trace,
                    )

            if not request.execute:
                break
            if not self.settings.allow_execution or self.executor is None:
                return QueryResponse(
                    request_id=request_id,
                    status="execution_disabled",
                    question=request.question,
                    analysis=analysis,
                    domain_plan=domain_plan,
                    generation_mode=generation_mode,
                    access=access,
                    sql=draft.sql,
                    parameters=draft.parameter_dict(),
                    explanation_fa=draft.explanation_fa,
                    confidence=draft.confidence,
                    validation=validation,
                    error="اجرای دیتابیس غیرفعال است؛ SQL فقط تولید و اعتبارسنجی شد.",
                    retrieved_sources=sources,
                    context=context if request.include_context else None,
                    trace=trace,
                )

            started = time.perf_counter()
            try:
                result = await asyncio.to_thread(self.executor.execute, draft.sql, draft.parameter_dict())
                result = self.masker.mask(result)
                trace.append(self._trace("read_only_execution", started, f"rows={result.row_count}"))
                break
            except Exception as exc:
                safe_error = f"{type(exc).__name__}: {str(exc)[:1000]}"
                trace.append(self._trace("read_only_execution", started, type(exc).__name__))
                if repairs >= self.settings.max_repair_attempts:
                    return QueryResponse(
                        request_id=request_id,
                        status="execution_failed",
                        question=request.question,
                        analysis=analysis,
                        domain_plan=domain_plan,
                        generation_mode=generation_mode,
                        sql=draft.sql,
                        parameters=draft.parameter_dict(),
                        explanation_fa=draft.explanation_fa,
                        confidence=draft.confidence,
                        validation=validation,
                        error="اجرای امن SQL پس از تلاش اصلاح با خطا مواجه شد.",
                        retrieved_sources=sources,
                        context=context if request.include_context else None,
                        trace=trace,
                    )
                try:
                    draft = await self._repair(
                        selected_generator,
                        request,
                        analysis,
                        context,
                        base_draft,
                        safe_error,
                        trace,
                    )
                except Exception as repair_exc:
                    trace.append(
                        PipelineTrace(
                            stage="bounded_repair",
                            duration_ms=0,
                            detail=type(repair_exc).__name__,
                        )
                    )
                    return self._repair_failure_response(
                        request_id, request, analysis, draft, validation, sources, context, trace
                    )
                repairs += 1
                if draft.needs_clarification:
                    return self._clarification_response(
                        request_id, request, analysis, draft, sources, context, trace
                    )

        return QueryResponse(
            request_id=request_id,
            status="executed" if result else "sql_generated",
            question=request.question,
            analysis=analysis,
            domain_plan=domain_plan,
            generation_mode=generation_mode,
            access=access,
            sql=draft.sql,
            parameters=draft.parameter_dict(),
            explanation_fa=draft.explanation_fa,
            answer_fa=self._present_result(result) if result else None,
            confidence=draft.confidence,
            validation=validation,
            result=result,
            retrieved_sources=sources,
            retrieval_candidates=retrieval_candidates,
            context_sources=sources,
            context=context if request.include_context else None,
            trace=trace,
            schema_version=schema_version,
            knowledge_snapshot_id=str(knowledge_base.manifest.get("content_hash", "")),
            knowledge_generation=int(knowledge_base.manifest.get("generation", 0)),
            llm_provider=selected_generator.provider_name if selected_generator else None,
            llm_model=selected_generator.model_name if selected_generator else None,
        )

    async def _repair(self, generator, request, analysis, context, draft, error, trace) -> SQLDraft:
        started = time.perf_counter()
        repaired = await generator.repair(request.question, analysis, context, draft, error)
        trace.append(
            self._trace(
                "bounded_repair",
                started,
                f"{generator.provider_name}/{generator.model_name}",
            )
        )
        return repaired

    def _select_generator(self, request: QueryRequest) -> SQLGenerator | None:
        if request.llm_provider is None and request.llm_model is None:
            return self.generator
        provider = request.llm_provider or self.settings.llm_provider
        if request.llm_model:
            model = request.llm_model
        elif provider == self.settings.llm_provider:
            model = self.settings.resolved_llm_model
        else:
            model = next(
                (candidate for item_provider, candidate in self.generators if item_provider == provider),
                "",
            )
        return self.generators.get((provider, model))

    @staticmethod
    def _present_result(result: ExecutionResult | None) -> str | None:
        if result is None:
            return None
        if not result.rows:
            return "نتیجه‌ای برای این درخواست پیدا نشد."
        if result.row_count == 1 and len(result.columns) == 1:
            column = result.columns[0]
            return f"{column}: {result.rows[0].get(column)}"
        suffix = "؛ نتیجه به سقف نمایش محدود شده است." if result.truncated else "."
        return f"کوئری با موفقیت اجرا شد و {result.row_count} رکورد برگرداند{suffix}"

    def health(self) -> dict[str, object]:
        consistency = self.knowledge_consistency()
        degraded_reasons = self._capability_issues()
        return {
            "status": "ok" if self.knowledge_base.is_ready and not degraded_reasons else "degraded",
            "index_ready": self.knowledge_base.is_ready,
            "artifact_count": len(self.knowledge_base.artifacts),
            "table_count": len(self.knowledge_base.known_tables),
            "embedding_model": self.knowledge_base.embedder.model_name,
            "reranker_model": self.reranker.model_name,
            "llm_model": self.generator.model_name if self.generator else None,
            "llm_provider": self.generator.provider_name if self.generator else None,
            "ontology_version": self.semantic.version,
            "jwt_enabled": self.settings.jwt_enabled,
            "audit_enabled": self.audit_repository is not None,
            "operational_database": (
                self.operational_database.engine.dialect.name if self.operational_database else None
            ),
            "knowledge_mode": self.knowledge_base.mode,
            "execution_enabled": self.settings.allow_execution and self.executor is not None,
            "knowledge_snapshot_id": str(self.knowledge_base.manifest.get("content_hash", "")),
            "knowledge_consistent": consistency.consistent,
            "llm_enabled": bool(self.generators),
            "execution_available": self.settings.allow_execution and self.executor is not None,
            "semantic_mode": "hybrid",
            "domain_packs": self.semantic.domain_packs,
            "degraded_reasons": degraded_reasons,
        }

    def readiness(self) -> dict[str, object]:
        knowledge_head_synchronized = (
            self.synchronize_knowledge_head()
            if self.settings.distributed_knowledge_enabled
            else True
        )
        stores = (
            self.knowledge_base.store_statuses() if hasattr(self.knowledge_base, "store_statuses") else {}
        )
        disconnected = [
            name for name, status in stores.items() if status.get("enabled") and not status.get("connected")
        ]
        consistency = self.knowledge_consistency()
        migration_revision = None
        try:
            database_status = self.operational_database.status() if self.operational_database else {}
            database_connected = bool(database_status.get("connected"))
            migration_revision = database_status.get("migration_revision")
        except Exception:
            database_connected = False
        migration_current = (
            not self.settings.require_migration_head
            or migration_revision == self.settings.expected_migration_revision
        )
        domain_database_connected = True
        if self.settings.allow_execution:
            try:
                domain_database_connected = bool(self.executor and self.executor.ping())
            except Exception:
                domain_database_connected = False
        consistency_blocked = (
            self.settings.distributed_knowledge_enabled
            and self.settings.snapshot_consistency_required
            and not consistency.consistent
        )
        capability_issues = self._capability_issues()
        ready = (
            database_connected
            and migration_current
            and domain_database_connected
            and self.knowledge_base.is_ready
            and knowledge_head_synchronized
            and (not disconnected or self.settings.distributed_fail_open)
            and not consistency_blocked
            and not capability_issues
        )
        return {
            "ready": ready,
            "operational_database": database_connected,
            "migration_revision": migration_revision,
            "migration_current": migration_current,
            "domain_database": domain_database_connected,
            "knowledge_head_synchronized": knowledge_head_synchronized,
            "knowledge_stores": stores,
            "index_ready": self.knowledge_base.is_ready,
            "issues": [
                *[f"{name}_unavailable" for name in disconnected],
                *consistency.issues,
                *([] if migration_current else ["operational_migration_outdated"]),
                *([] if domain_database_connected else ["domain_database_unavailable"]),
                *([] if knowledge_head_synchronized else ["knowledge_head_not_synchronized"]),
                *capability_issues,
            ],
            "knowledge_consistency": consistency.model_dump(mode="json"),
        }

    def synchronize_knowledge_head(self) -> bool:
        """Reload a committed snapshot published by another replica without disrupting in-flight queries."""
        knowledge_base = self.knowledge_base
        matcher = getattr(knowledge_base, "publication_matches_active", None)
        if matcher is None:
            return True
        try:
            if matcher():
                return True
        except Exception:
            return False
        with self._knowledge_refresh_lock:
            knowledge_base = self.knowledge_base
            matcher = getattr(knowledge_base, "publication_matches_active", None)
            try:
                if matcher is None or matcher():
                    return True
                replacement = knowledge_base.clone_at(self.settings.index_dir)
                replacement.load()
                replacement_matcher = getattr(replacement, "publication_matches_active", None)
                if replacement_matcher is not None and not replacement_matcher():
                    return False
            except Exception:
                return False
            self.knowledge_base = replacement
            return True

    def knowledge_consistency(self) -> KnowledgeConsistencyResponse:
        if hasattr(self.knowledge_base, "consistency_status"):
            return KnowledgeConsistencyResponse.model_validate(self.knowledge_base.consistency_status())
        return KnowledgeConsistencyResponse(
            consistent=True,
            expected_snapshot_id=str(self.knowledge_base.manifest.get("content_hash", "")),
        )

    async def audit_query(
        self,
        request: QueryRequest,
        response: QueryResponse,
        principal: Principal,
        duration_ms: int,
    ) -> None:
        ensure_blocking_executor()
        if self.audit_repository is None:
            return
        event = QueryAuditEvent(
            request=request,
            response=response,
            principal=principal,
            duration_ms=duration_ms,
        )
        await asyncio.to_thread(self.audit_repository.record, event)

    async def audit_summary(self) -> dict[str, object]:
        ensure_blocking_executor()
        if self.audit_repository is None:
            return {"enabled": False}
        summary = await asyncio.to_thread(self.audit_repository.summary)
        return {"enabled": True, **summary}

    async def submit_feedback(
        self,
        feedback: FeedbackRequest,
        principal: Principal,
    ) -> FeedbackResponse:
        ensure_blocking_executor()
        if self.audit_repository is None:
            raise RuntimeError("feedback storage is disabled")
        accepted = await asyncio.to_thread(
            self.audit_repository.record_feedback,
            feedback,
            principal,
        )
        return FeedbackResponse(accepted=accepted, request_id=feedback.request_id)

    async def feedback_summary(self) -> FeedbackSummary:
        ensure_blocking_executor()
        if self.audit_repository is None:
            return FeedbackSummary(total=0)
        return await asyncio.to_thread(self.audit_repository.feedback_summary)

    def service_meta(self) -> ServiceMeta:
        capability_issues = self._capability_issues()
        options = []
        for provider, label in LLM_PROVIDER_LABELS.items():
            models = [
                model
                for item_provider, model in self.generators
                if item_provider == provider
            ]
            if models:
                options.append(
                    {
                        "provider": provider,
                        "label": label,
                        "models": models,
                    }
                )
        return ServiceMeta(
            version=__version__,
            auth_mode="jwt" if self.settings.jwt_enabled else "api_key",
            execution_enabled=self.settings.allow_execution and self.executor is not None,
            llm_enabled=bool(self.generators),
            execution_available=self.settings.allow_execution and self.executor is not None,
            semantic_mode="hybrid",
            domain_packs=self.semantic.domain_packs,
            degraded_reasons=capability_issues,
            default_llm_provider=(
                self.generator.provider_name if self.generator is not None else None
            ),
            default_llm_model=(
                self.generator.model_name if self.generator is not None else None
            ),
            llm_options=options,
            features=[
                "commission100_semantic_layer",
                "generic_schema_semantic_planner",
                "hybrid_schema_rag",
                f"{self.settings.reranker_provider}_reranker",
                "deterministic_recipes",
                "tsql_validation",
                "rbac_abac",
                "incremental_ingestion",
                "metadata_audit",
                "feedback",
                "sqlalchemy_operational_store",
                "alembic_migrations",
                "persistent_admin_jobs",
                "qdrant_vector_retrieval",
                "neo4j_temporal_schema_graph",
                "redis_retrieval_cache_and_lock",
                "versioned_schema_snapshots",
                "coordinated_snapshot_publication",
                "renewable_ingestion_lease_and_fencing",
                "snapshot_pinned_queries",
                "cross_store_consistency_barrier",
                "observable_ingestion_state_machine",
            ],
        )

    def _capability_issues(self) -> list[str]:
        issues: list[str] = []
        if self.settings.llm_required and self.generator is None:
            issues.append("llm_not_configured")
        if self.settings.environment == "production" and not self.role_policy.configured:
            issues.append("role_policy_not_configured")
        if self.settings.allow_execution and self.executor is None:
            issues.append("domain_database_not_configured")
        return issues

    def index_status(self) -> IndexStatus:
        manifest = self.knowledge_base.manifest
        consistency = self.knowledge_consistency()
        stores = (
            self.knowledge_base.store_statuses()
            if hasattr(self.knowledge_base, "store_statuses")
            else {
                name: {"enabled": False, "connected": False, "backend": name}
                for name in ("redis", "qdrant", "neo4j")
            }
        )
        return IndexStatus(
            ready=self.knowledge_base.is_ready,
            generation=int(manifest.get("generation", 0)),
            content_hash=str(manifest.get("content_hash", "")),
            artifact_count=len(self.knowledge_base.artifacts),
            table_count=len(self.knowledge_base.known_tables),
            foreign_key_count=len(self.knowledge_base.foreign_keys),
            embedding_model=self.knowledge_base.embedder.model_name,
            embedding_dimensions=int(manifest.get("embedding_dimensions", 0)),
            artifacts_added=int(manifest.get("artifacts_added", 0)),
            artifacts_updated=int(manifest.get("artifacts_updated", 0)),
            artifacts_reused=int(manifest.get("artifacts_reused", 0)),
            artifacts_deleted=int(manifest.get("artifacts_deleted", 0)),
            vector_reuse_ratio=float(manifest.get("vector_reuse_ratio", 0.0)),
            mode=self.knowledge_base.mode,
            source_file_count=int(manifest.get("source_file_count", 0)),
            databases=[str(value) for value in manifest.get("databases", [])],
            schema_versions=[str(value) for value in manifest.get("schema_versions", [])],
            snapshot_version=str(manifest.get("snapshot_version", "")),
            active_schema_version=str(
                manifest.get("active_schema_version", manifest.get("snapshot_version", ""))
            ),
            stores=stores,
            consistent=consistency.consistent,
            consistency_issues=consistency.issues,
        )

    def database_status(self) -> DatabaseStatus:
        if self.operational_database is None:
            return DatabaseStatus(connected=False, dialect="disabled")
        return DatabaseStatus.model_validate(self.operational_database.status())

    async def production_check(self) -> ProductionCheckResponse:
        from .evaluation import DomainEvaluator

        question = "آخرین پرونده های ماده ۱۰۰ را نشان بده"
        readiness = self.readiness()
        evaluation = await DomainEvaluator(self).run()
        response = await self.query(
            QueryRequest(question=question, execute=False),
            principal=Principal(subject="production-acceptance", roles=["system_admin"]),
        )
        compact_sql = " ".join((response.sql or "").split()).lower()
        required_fragments = (
            "top (20)",
            "left join tbascommission100state",
            "c.fregisterdate desc, c.id desc",
        )
        critical_passed = bool(
            response.status == "sql_generated"
            and response.domain_plan
            and response.domain_plan.matched_recipe_id == "latest_commission_cases"
            and response.validation
            and response.validation.valid
            and all(fragment in compact_sql for fragment in required_fragments)
        )
        configuration_issues = self.settings.production_violations()
        checks = [
            ProductionCheckItem(
                name="production_configuration",
                passed=self.settings.environment == "production" and not configuration_issues,
                detail=("ok" if not configuration_issues else "; ".join(configuration_issues)),
            ),
            ProductionCheckItem(
                name="readiness",
                passed=bool(readiness["ready"]),
                detail=", ".join(str(item) for item in readiness.get("issues", [])) or "ok",
            ),
            ProductionCheckItem(
                name="gold_evaluation",
                passed=evaluation.pass_rate == 1.0,
                detail=f"{evaluation.passed}/{evaluation.total}",
            ),
            ProductionCheckItem(
                name="critical_customer_chat",
                passed=critical_passed,
                detail=f"status={response.status}",
            ),
        ]
        return ProductionCheckResponse(
            ready_for_production=all(item.passed for item in checks),
            version=__version__,
            checked_at=datetime.now(UTC),
            checks=checks,
            gold_evaluation=evaluation,
            critical_question=question,
            critical_status=response.status,
            critical_recipe_id=(response.domain_plan.matched_recipe_id if response.domain_plan else None),
            critical_sql=response.sql,
            critical_validation_errors=(response.validation.errors if response.validation else []),
            knowledge_snapshot_id=response.knowledge_snapshot_id,
        )

    async def close(self) -> None:
        ensure_blocking_executor()
        if self._closed:
            return
        self._closed = True
        await self.job_manager.shutdown()
        if self.executor is not None and hasattr(self.executor, "close"):
            await asyncio.to_thread(self.executor.close)
        if hasattr(self.knowledge_base, "close_distributed"):
            await asyncio.to_thread(self.knowledge_base.close_distributed)
        if self.operational_database is not None:
            await asyncio.to_thread(self.operational_database.dispose)

    def schema_overview(self, principal: Principal) -> SchemaOverviewResponse:
        allowed = self._allowed_table_names(principal)
        tables: list[SchemaTableSummary] = []
        seen_tables: set[str] = set()
        for artifact in self.knowledge_base.artifacts:
            if artifact.kind.value != "table" or not artifact.tables:
                continue
            table_name = artifact.tables[0]
            schema_name = str(artifact.metadata.get("schema", "dbo"))
            table_key = (
                table_name.lower()
                if schema_name.lower() == "dbo"
                else f"{schema_name}.{table_name}".lower()
            )
            if table_key not in allowed or table_key in seen_tables:
                continue
            seen_tables.add(table_key)
            columns = [
                ColumnInfo.model_validate(item)
                for item in artifact.metadata.get("columns", [])
                if isinstance(item, dict) and item.get("name")
            ]
            tables.append(
                SchemaTableSummary(
                    name=table_name,
                    schema_name=schema_name,
                    qualified_name=f"{schema_name}.{table_name}",
                    description=str(artifact.metadata.get("description", "")),
                    columns=columns,
                    source_path=artifact.source_path,
                )
            )
        tables.sort(key=lambda item: item.name.lower())
        relationship_count = sum(
            1
            for edge in self.knowledge_base.foreign_keys
            if edge.child_table.lower() in allowed and edge.parent_table.lower() in allowed
        )
        return SchemaOverviewResponse(
            table_count=len(tables),
            relationship_count=relationship_count,
            tables=tables,
        )

    def schema_versions(self) -> SchemaVersionsResponse:
        manifest = self.knowledge_base.manifest
        return SchemaVersionsResponse(
            active_version=str(manifest.get("active_schema_version", manifest.get("snapshot_version", ""))),
            versions=[str(value) for value in manifest.get("schema_versions", [])],
            databases=[str(value) for value in manifest.get("databases", [])],
            generation=int(manifest.get("generation", 0)),
        )

    def schema_graph(self, principal: Principal) -> SchemaGraphResponse:
        allowed = self._allowed_table_names(principal)
        columns = self.knowledge_base.table_columns
        nodes = [
            SchemaGraphNode(
                id=table,
                label=table,
                column_count=len(columns.get(table.lower(), set())),
            )
            for table in self.knowledge_base.known_tables
            if table.lower() in allowed
        ]
        edges = [
            SchemaGraphEdge(
                id=edge.name,
                source=edge.child_table,
                target=edge.parent_table,
                source_column=edge.child_column,
                target_column=edge.parent_column,
                label=f"{edge.child_column} → {edge.parent_column}",
            )
            for edge in self.knowledge_base.foreign_keys
            if edge.child_table.lower() in allowed and edge.parent_table.lower() in allowed
        ]
        return SchemaGraphResponse(nodes=nodes, edges=edges)

    def domain_overview(self, principal: Principal) -> DomainOverviewResponse:
        payload = self.domain_semantic.payload
        allowed = self._allowed_table_names(principal)

        def permitted(item) -> bool:
            return all(str(table).lower() in allowed for table in item.get("tables", []))

        concepts = [
            DomainItemSummary(
                id=item["id"],
                label=item["synonyms"][0],
                tables=item["tables"],
                columns=item["columns"],
            )
            for item in payload["concepts"]
            if permitted(item)
        ]
        metrics = [
            DomainItemSummary(
                id=item["id"],
                label=item["synonyms"][0],
                tables=item["tables"],
                columns=item["columns"],
            )
            for item in payload["metrics"]
            if permitted(item)
        ]
        recipes = [
            DomainRecipeSummary(
                id=item["id"],
                required_tables=item["required_tables"],
                requires_case_number=bool(item.get("parameter")),
            )
            for item in payload["recipes"]
            if all(table.lower() in allowed for table in item["required_tables"])
        ]
        return DomainOverviewResponse(
            domain=self.domain_semantic.domain,
            ontology_version=self.domain_semantic.version,
            concepts=concepts,
            metrics=metrics,
            business_rules=([item["text"] for item in payload["business_rules"]] if allowed else []),
            recipes=recipes,
        )

    def _allowed_table_names(self, principal: Principal) -> set[str]:
        allowed = self.role_policy.allowed_tables(principal.roles)
        if "*" in {value.lower() for value in allowed}:
            return {table.lower() for table in self.knowledge_base.known_tables}
        return {
            table.lower()
            for table in self.knowledge_base.known_tables
            if self.role_policy.table_allowed(table, allowed)
        }

    def _filter_hits_for_principal(self, hits, principal: Principal):
        allowed = self.role_policy.allowed_tables(principal.roles)
        if "*" in {value.lower() for value in allowed}:
            return hits
        return [
            hit
            for hit in hits
            if not hit.artifact.tables
            or all(self.role_policy.table_allowed(table, allowed) for table in hit.artifact.tables)
        ]

    @staticmethod
    def _trace(stage: str, started: float, detail: str = "") -> PipelineTrace:
        return PipelineTrace(
            stage=stage,
            duration_ms=int((time.perf_counter() - started) * 1000),
            detail=detail,
        )

    def _error_response(
        self,
        request_id,
        request,
        status,
        error,
        trace,
        analysis=None,
        sources=None,
        domain_plan=None,
        generation_mode=None,
        retrieval_candidates=None,
        context=None,
        resolved_schema_version=None,
        knowledge_base=None,
        failure_stage=None,
        llm_provider=None,
        llm_model=None,
    ) -> QueryResponse:
        analysis = analysis or self.analyzer.analyze(request.question, self.knowledge_base.known_tables)
        active = knowledge_base or self.knowledge_base
        return QueryResponse(
            request_id=request_id,
            status=status,
            question=request.question,
            analysis=analysis,
            domain_plan=domain_plan,
            generation_mode=generation_mode,
            error=error,
            retrieved_sources=sources or [],
            retrieval_candidates=retrieval_candidates or [],
            context_sources=sources or [],
            context=context,
            trace=trace,
            schema_version=resolved_schema_version or request.schema_version,
            knowledge_snapshot_id=str(active.manifest.get("content_hash", "")),
            knowledge_generation=int(active.manifest.get("generation", 0)),
            failure_stage=failure_stage,
            llm_provider=llm_provider,
            llm_model=llm_model,
        )

    @staticmethod
    def _clarification_response(request_id, request, analysis, draft, sources, context, trace):
        return QueryResponse(
            request_id=request_id,
            status="needs_clarification",
            question=request.question,
            analysis=analysis,
            explanation_fa=draft.explanation_fa,
            confidence=draft.confidence,
            clarification_question=draft.clarification_question,
            retrieved_sources=sources,
            context=context if request.include_context else None,
            trace=trace,
            schema_version=request.schema_version,
        )

    @staticmethod
    def _repair_failure_response(
        request_id,
        request,
        analysis,
        draft,
        validation,
        sources,
        context,
        trace,
    ):
        return QueryResponse(
            request_id=request_id,
            status="repair_failed",
            question=request.question,
            analysis=analysis,
            sql=draft.sql,
            parameters=draft.parameter_dict(),
            explanation_fa=draft.explanation_fa,
            confidence=draft.confidence,
            validation=validation,
            error="اصلاح خودکار SQL با خطا مواجه شد.",
            retrieved_sources=sources,
            context=context if request.include_context else None,
            trace=trace,
            schema_version=request.schema_version,
        )
