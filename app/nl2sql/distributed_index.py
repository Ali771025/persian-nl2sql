from __future__ import annotations

import hashlib
import json
import os
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from .index import HybridKnowledgeBase
from .knowledge_protocols import (
    GraphKnowledgeStore,
    KnowledgeCache,
    KnowledgeSyncReport,
    StagedStoreSnapshot,
    VectorKnowledgeStore,
)
from .models import Catalog, SearchHit
from .normalization import normalize_persian, tokenize


@dataclass
class DistributedStage:
    snapshot_id: str
    generation: int
    vector: StagedStoreSnapshot | None = None
    graph: StagedStoreSnapshot | None = None
    previous_vector: str | None = None
    previous_graph: str | None = None


class DistributedKnowledgeBase(HybridKnowledgeBase):
    """Local lexical catalog plus Qdrant dense search, Neo4j graph and Redis cache."""

    def __init__(
        self,
        index_dir,
        embedder,
        *,
        vector_store: VectorKnowledgeStore | None,
        graph_store: GraphKnowledgeStore | None,
        cache: KnowledgeCache | None,
        cache_ttl_seconds: int = 900,
        ingest_lock_seconds: int = 3_600,
        fail_open: bool = True,
    ):
        super().__init__(index_dir, embedder)
        self.vector_store = vector_store
        self.graph_store = graph_store
        self.cache = cache
        self.cache_ttl_seconds = cache_ttl_seconds
        self.ingest_lock_seconds = ingest_lock_seconds
        self.fail_open = fail_open
        self.sync_report = KnowledgeSyncReport()

    @property
    def mode(self) -> str:
        return "distributed"

    def empty_clone(self) -> DistributedKnowledgeBase:
        return self.clone_at(self.index_dir)

    def clone_at(self, index_dir: Path) -> DistributedKnowledgeBase:
        return DistributedKnowledgeBase(
            index_dir,
            self.embedder,
            vector_store=self.vector_store,
            graph_store=self.graph_store,
            cache=self.cache,
            cache_ttl_seconds=self.cache_ttl_seconds,
            ingest_lock_seconds=self.ingest_lock_seconds,
            fail_open=self.fail_open,
        )

    def build(self, catalog: Catalog):
        stats = super().build(catalog)
        self._sync_distributed(catalog)
        return stats

    def build_incremental(self, catalog: Catalog):
        stats = super().build_incremental(catalog)
        self._sync_distributed(catalog)
        return stats

    def _sync_distributed(self, catalog: Catalog) -> None:
        stage = self.stage_distributed(catalog)
        self.activate_distributed(stage)
        self.finalize_distributed(stage)

    def build_local_incremental(self, catalog: Catalog):
        """Build only the immutable local snapshot; publication is coordinated by the service."""
        return super().build_incremental(catalog)

    def stage_distributed(self, catalog: Catalog) -> DistributedStage:
        generation = int(self.manifest.get("generation", 1))
        report = KnowledgeSyncReport(
            snapshot_id=catalog.content_hash,
            generation=generation,
            phase="staging",
        )
        stage = DistributedStage(snapshot_id=catalog.content_hash, generation=generation)
        if self.vector_store is not None:
            try:
                stage.previous_vector = self.vector_store.active_reference()
                stage.vector = self.vector_store.stage(
                    catalog,
                    self.embeddings,
                    self.embedder.model_name,
                    generation,
                )
                report.qdrant_points = stage.vector.item_count
            except Exception as exc:
                self._handle_store_error(report, self.vector_store.name, exc)
        if self.graph_store is not None:
            try:
                stage.previous_graph = self.graph_store.active_reference()
                stage.graph = self.graph_store.stage(catalog, generation)
                report.graph_nodes = stage.graph.item_count
            except Exception as exc:
                if not self.fail_open and stage.vector is not None:
                    self.vector_store.abort(stage.vector)  # type: ignore[union-attr]
                self._handle_store_error(report, self.graph_store.name, exc)
        self.sync_report = report
        return stage

    def activate_distributed(self, stage: DistributedStage) -> None:
        activated_vector = False
        activated_graph = False
        try:
            if stage.vector is not None and self.vector_store is not None:
                self.vector_store.activate(stage.vector)
                activated_vector = True
            if stage.graph is not None and self.graph_store is not None:
                self.graph_store.activate(stage.graph)
                activated_graph = True
        except Exception:
            if activated_graph and self.graph_store is not None:
                self.graph_store.restore(stage.previous_graph)
            if activated_vector and self.vector_store is not None:
                self.vector_store.restore(stage.previous_vector)
            self.abort_distributed(stage)
            self.sync_report.phase = "rolled_back"
            raise
        self.sync_report.phase = "activated"
        report = self.sync_report
        self.manifest["distributed_sync"] = {
            "qdrant_points": report.qdrant_points,
            "graph_nodes": report.graph_nodes,
            "errors": report.errors,
            "snapshot_id": report.snapshot_id,
            "generation": report.generation,
            "phase": report.phase,
        }
        self._rewrite_manifest()

    def rollback_distributed(self, stage: DistributedStage) -> None:
        """Restore both read heads to their pre-publication references and remove staging data."""
        if self.graph_store is not None:
            self.graph_store.restore(stage.previous_graph)
        if self.vector_store is not None:
            self.vector_store.restore(stage.previous_vector)
        self.abort_distributed(stage)
        self.sync_report.phase = "rolled_back"

    def finalize_distributed(self, stage: DistributedStage) -> None:
        for store, snapshot in (
            (self.vector_store, stage.vector),
            (self.graph_store, stage.graph),
        ):
            if store is not None and snapshot is not None:
                store.finalize(snapshot)
        self.sync_report.phase = "published"
        distributed_sync = self.manifest.get("distributed_sync")
        if isinstance(distributed_sync, dict):
            distributed_sync["phase"] = "published"
        self._rewrite_manifest()

    def abort_distributed(self, stage: DistributedStage) -> None:
        for store, snapshot in (
            (self.vector_store, stage.vector),
            (self.graph_store, stage.graph),
        ):
            if store is None or snapshot is None:
                continue
            try:
                store.abort(snapshot)
            except Exception:
                if not self.fail_open:
                    raise

    def search(
        self,
        query: str,
        top_k: int = 36,
        graph_depth: int = 1,
        schema_version: str | None = None,
    ) -> list[SearchHit]:
        if not self.is_ready:
            raise RuntimeError("Knowledge index is not loaded")
        cache_key = self._cache_key(query, top_k, graph_depth, schema_version)
        if self.cache is not None:
            try:
                cached = self.cache.get_json(cache_key)
                if isinstance(cached, list):
                    return [SearchHit.model_validate(item) for item in cached]
            except Exception:
                if not self.fail_open:
                    raise

        hits = self._distributed_search(query, top_k, graph_depth, schema_version)
        if self.cache is not None:
            try:
                self.cache.set_json(
                    cache_key,
                    [hit.model_dump(mode="json") for hit in hits],
                    self.cache_ttl_seconds,
                )
            except Exception:
                if not self.fail_open:
                    raise
        return hits

    def _distributed_search(
        self,
        query: str,
        top_k: int,
        graph_depth: int,
        schema_version: str | None,
    ) -> list[SearchHit]:
        query_vector = np.asarray(self.embedder.encode_query(query), dtype=np.float32)
        if query_vector.shape[0] != self.embeddings.shape[1]:
            raise RuntimeError("Query embedding dimensions do not match the knowledge index")
        eligible = self._eligible_indices(schema_version)
        if not eligible:
            return []
        positions = {artifact.id: index for index, artifact in enumerate(self.artifacts)}
        dense_scores = self.embeddings @ query_vector
        dense_rank: list[int] = []
        external_dense_scores: dict[int, float] = {}
        if self.vector_store is not None:
            try:
                matches = self.vector_store.search(
                    query_vector,
                    min(len(eligible), max(top_k * 3, 60)),
                    schema_version,
                    str(self.manifest.get("content_hash", "")),
                )
                for match in matches:
                    index = positions.get(match.artifact_id)
                    if index is None or index not in eligible:
                        continue
                    dense_rank.append(index)
                    external_dense_scores[index] = match.score
            except Exception:
                if not self.fail_open:
                    raise
        if not dense_rank:
            dense_rank = self._rank_subset(
                dense_scores,
                eligible,
                min(len(eligible), max(top_k * 3, 60)),
            )

        keyword_scores = self.bm25.scores(query)
        keyword_rank = self._rank_subset(
            keyword_scores,
            eligible,
            min(len(eligible), max(top_k * 3, 60)),
        )
        fused: dict[int, float] = defaultdict(float)
        channels: dict[int, set[str]] = defaultdict(set)
        for rank, index in enumerate(dense_rank, start=1):
            fused[index] += 0.56 / (60 + rank)
            channels[index].add("qdrant" if external_dense_scores else "dense_local")
        for rank, index in enumerate(keyword_rank, start=1):
            fused[index] += 0.36 / (60 + rank)
            channels[index].add("bm25")

        normalized_query = normalize_persian(query).lower()
        for index in eligible:
            artifact = self.artifacts[index]
            if any(table.lower() in normalized_query for table in artifact.tables):
                fused[index] += 0.035
                channels[index].add("identifier")

        initial = sorted(fused, key=fused.get, reverse=True)[:top_k]
        seeds = {table for index in initial[:12] for table in self.artifacts[index].tables}
        expanded = self._graph_for_version(schema_version).expand(
            seeds,
            depth=graph_depth,
        )
        if self.graph_store is not None:
            try:
                external_expanded = self.graph_store.related_tables(
                    seeds,
                    tokenize(normalized_query),
                    graph_depth,
                    schema_version,
                    str(self.manifest.get("content_hash", "")),
                )
                for table, distance in external_expanded.items():
                    expanded[table] = min(expanded.get(table, distance), distance)
            except Exception:
                if not self.fail_open:
                    raise

        best_score = max((fused[index] for index in initial), default=0.01)
        graph_indices: set[int] = set()
        artifacts_by_table: dict[str, list[int]] = defaultdict(list)
        for index in eligible:
            for table in self.artifacts[index].tables:
                artifacts_by_table[table.lower()].append(index)
        for table, distance in expanded.items():
            for index in artifacts_by_table.get(table.lower(), []):
                graph_indices.add(index)
                fused[index] = max(fused.get(index, 0.0), best_score * (0.70 / max(distance, 1)))
                channels[index].add("neo4j_graph" if self.graph_store else "fk_graph")

        final_indices = sorted(
            set(initial) | graph_indices,
            key=lambda index: fused[index],
            reverse=True,
        )
        hits = [
            SearchHit(
                artifact=self.artifacts[index],
                score=float(max(fused[index], 0.0)),
                dense_score=float(external_dense_scores.get(index, dense_scores[index])),
                keyword_score=float(keyword_scores[index]),
                graph_expanded=index in graph_indices and index not in initial,
                retrieval_channels=sorted(channels[index]),
                graph_distance=self._graph_distance(self.artifacts[index].tables, expanded),
            )
            for index in final_indices
        ]
        return hits

    def acquire_ingestion_lock(self) -> object | None:
        if self.cache is None:
            return True
        try:
            return self.cache.acquire_lock("knowledge-ingestion", self.ingest_lock_seconds)
        except Exception:
            if self.fail_open:
                return True
            raise

    def release_ingestion_lock(self, lock: object | None) -> None:
        if self.cache is None or lock is None or lock is True:
            return
        self.cache.release_lock(lock)

    def renew_ingestion_lock(self, lock: object | None) -> bool:
        if self.cache is None or lock is None or lock is True:
            return True
        return self.cache.renew_lock(lock, self.ingest_lock_seconds)

    def ingestion_fencing_token(self, lock: object | None) -> int:
        if self.cache is None or lock is None or lock is True:
            return 0
        return self.cache.fencing_token(lock)

    def publish_commit(self, fencing_token: int) -> bool:
        if self.cache is None:
            return True
        return self.cache.publish_snapshot(
            {
                "snapshot_id": str(self.manifest.get("content_hash", "")),
                "generation": int(self.manifest.get("generation", 0)),
                "snapshot_version": str(self.manifest.get("snapshot_version", "")),
            },
            fencing_token,
        )

    def consistency_status(self) -> dict[str, object]:
        expected = str(self.manifest.get("content_hash", ""))
        statuses = self.store_statuses()
        issues: list[str] = []
        for name in ("qdrant", "neo4j"):
            status = statuses[name]
            if status.get("connected") and status.get("snapshot_id") != expected:
                issues.append(f"{name}_snapshot_mismatch")
        published = None
        if self.cache is not None:
            try:
                published = self.cache.active_snapshot()
                if published and published.get("snapshot_id") != expected:
                    issues.append("redis_commit_mismatch")
                if not published and expected:
                    issues.append("redis_commit_missing")
            except Exception:
                if not self.fail_open:
                    issues.append("redis_commit_unavailable")
        return {
            "consistent": not issues,
            "expected_snapshot_id": expected,
            "published": published,
            "issues": issues,
            "stores": statuses,
        }

    def publication_matches_active(self) -> bool:
        if self.cache is None:
            return True
        try:
            published = self.cache.active_snapshot()
        except Exception:
            return self.fail_open
        return bool(
            published
            and published.get("snapshot_id") == self.manifest.get("content_hash")
            and int(published.get("generation", -1)) == int(self.manifest.get("generation", 0))
        )

    def store_statuses(self) -> dict[str, dict[str, object]]:
        stores = {
            "redis": self.cache,
            "qdrant": self.vector_store,
            "neo4j": self.graph_store,
        }
        output: dict[str, dict[str, object]] = {}
        for name, store in stores.items():
            if store is None:
                output[name] = {"enabled": False, "connected": False, "backend": name}
                continue
            output[name] = store.status()
        return output

    def close_distributed(self) -> None:
        seen: set[int] = set()
        for store in (self.cache, self.vector_store, self.graph_store):
            if store is None or id(store) in seen:
                continue
            seen.add(id(store))
            store.close()

    def _handle_store_error(
        self,
        report: KnowledgeSyncReport,
        store_name: str,
        error: Exception,
    ) -> None:
        message = f"{store_name}:{type(error).__name__}"
        report.errors.append(message)
        if not self.fail_open:
            raise RuntimeError(f"Distributed knowledge synchronization failed: {message}") from error

    def _rewrite_manifest(self) -> None:
        target_dir = self.storage_dir
        path = target_dir / "manifest.json"
        temporary = target_dir / "manifest.json.distributed.tmp"
        temporary.write_text(
            json.dumps(self.manifest, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        os.replace(temporary, path)

    def _cache_key(
        self,
        query: str,
        top_k: int,
        graph_depth: int,
        schema_version: str | None,
    ) -> str:
        digest = hashlib.sha256(normalize_persian(query).encode()).hexdigest()
        generation = int(self.manifest.get("generation", 0))
        version = schema_version or str(self.manifest.get("snapshot_version", "current"))
        return f"search:g{generation}:{version}:{top_k}:{graph_depth}:{digest}"

    @staticmethod
    def _graph_distance(tables: list[str], expanded: dict[str, int]) -> int | None:
        distances = [
            distance
            for table, distance in expanded.items()
            if table.lower() in {value.lower() for value in tables}
        ]
        return min(distances) if distances else None
