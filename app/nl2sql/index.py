from __future__ import annotations

import json
import math
import os
from collections import Counter, defaultdict, deque
from pathlib import Path

import numpy as np

from .embeddings import Embedder
from .models import Artifact, ArtifactKind, Catalog, ForeignKey, IndexBuildStats, SearchHit
from .normalization import normalize_persian, tokenize


class BM25Index:
    def __init__(self, texts: list[str], k1: float = 1.5, b: float = 0.75):
        self.k1 = k1
        self.b = b
        self.documents = [tokenize(text) for text in texts]
        self.lengths = np.asarray([len(doc) for doc in self.documents], dtype=np.float32)
        self.avg_length = float(np.mean(self.lengths)) if len(self.lengths) else 1.0
        self.term_frequencies = [Counter(doc) for doc in self.documents]
        doc_frequency: Counter[str] = Counter()
        for doc in self.documents:
            doc_frequency.update(set(doc))
        count = max(1, len(self.documents))
        self.idf = {
            term: math.log(1 + (count - frequency + 0.5) / (frequency + 0.5))
            for term, frequency in doc_frequency.items()
        }

    def scores(self, query: str) -> np.ndarray:
        output = np.zeros(len(self.documents), dtype=np.float32)
        if not self.documents:
            return output
        for term in set(tokenize(query)):
            idf = self.idf.get(term)
            if idf is None:
                continue
            for index, frequencies in enumerate(self.term_frequencies):
                frequency = frequencies.get(term, 0)
                if not frequency:
                    continue
                denominator = frequency + self.k1 * (
                    1 - self.b + self.b * self.lengths[index] / max(self.avg_length, 1.0)
                )
                output[index] += idf * (frequency * (self.k1 + 1)) / denominator
        return output


class ForeignKeyGraph:
    def __init__(self, edges: list[ForeignKey]):
        self.edges = edges
        self._adjacency: dict[str, set[str]] = defaultdict(set)
        self._canonical: dict[str, str] = {}
        for edge in edges:
            child = edge.child_table.lower()
            parent = edge.parent_table.lower()
            self._canonical[child] = edge.child_table
            self._canonical[parent] = edge.parent_table
            self._adjacency[child].add(parent)
            self._adjacency[parent].add(child)

    def expand(self, seeds: set[str], depth: int = 1) -> dict[str, int]:
        distances: dict[str, int] = {}
        queue: deque[tuple[str, int]] = deque()
        for seed in seeds:
            key = seed.lower()
            distances[key] = 0
            queue.append((key, 0))
        while queue:
            node, current_depth = queue.popleft()
            if current_depth >= depth:
                continue
            for neighbor in self._adjacency.get(node, set()):
                if neighbor in distances:
                    continue
                distances[neighbor] = current_depth + 1
                queue.append((neighbor, current_depth + 1))
        return {
            self._canonical.get(key, key): distance for key, distance in distances.items() if distance > 0
        }


class HybridKnowledgeBase:
    """Small, persistent dense + BM25 index designed for roughly 5k source files."""

    def __init__(self, index_dir: Path, embedder: Embedder):
        self.index_dir = index_dir.resolve()
        self.embedder = embedder
        self.artifacts: list[Artifact] = []
        self.foreign_keys: list[ForeignKey] = []
        self.embeddings = np.empty((0, 0), dtype=np.float32)
        self.bm25 = BM25Index([])
        self.graph = ForeignKeyGraph([])
        self._table_artifacts: dict[str, list[int]] = defaultdict(list)
        self.manifest: dict[str, object] = {}

    @property
    def mode(self) -> str:
        return "local"

    @property
    def is_ready(self) -> bool:
        return bool(self.artifacts) and self.embeddings.size > 0

    @property
    def known_tables(self) -> list[str]:
        names = {
            self._artifact_table_name(artifact, table)
            for artifact in self.artifacts
            if artifact.kind == ArtifactKind.TABLE
            for table in artifact.tables
        }
        names.update(edge.child_table for edge in self.foreign_keys)
        names.update(edge.parent_table for edge in self.foreign_keys)
        return sorted(names, key=str.lower)

    @property
    def table_columns(self) -> dict[str, set[str]]:
        output: dict[str, set[str]] = defaultdict(set)
        for artifact in self.artifacts:
            if artifact.kind != ArtifactKind.TABLE:
                continue
            columns = artifact.metadata.get("columns", [])
            for table in artifact.tables:
                output[self._artifact_table_name(artifact, table).lower()].update(
                    str(item.get("name", "")).lower()
                    for item in columns
                    if isinstance(item, dict) and item.get("name")
                )
        return dict(output)

    def known_tables_for_version(self, schema_version: str | None) -> list[str]:
        indices = self._eligible_indices(schema_version)
        names = {
            self._artifact_table_name(self.artifacts[index], table)
            for index in indices
            if self.artifacts[index].kind == ArtifactKind.TABLE
            for table in self.artifacts[index].tables
        }
        return sorted(names, key=str.lower)

    def table_columns_for_version(self, schema_version: str | None) -> dict[str, set[str]]:
        if not schema_version or schema_version == self.manifest.get("snapshot_version"):
            return self.table_columns
        output: dict[str, set[str]] = defaultdict(set)
        for index in self._eligible_indices(schema_version):
            artifact = self.artifacts[index]
            if artifact.kind != ArtifactKind.TABLE:
                continue
            for table in artifact.tables:
                output[self._artifact_table_name(artifact, table).lower()].update(
                    str(item.get("name", "")).lower()
                    for item in artifact.metadata.get("columns", [])
                    if isinstance(item, dict) and item.get("name")
                )
        return dict(output)

    @staticmethod
    def _artifact_table_name(artifact: Artifact, table: str) -> str:
        schema = str(artifact.metadata.get("schema", "dbo"))
        return table if schema.lower() == "dbo" else f"{schema}.{table}"

    def empty_clone(self) -> HybridKnowledgeBase:
        return HybridKnowledgeBase(self.index_dir, self.embedder)

    def clone_at(self, index_dir: Path) -> HybridKnowledgeBase:
        return HybridKnowledgeBase(index_dir, self.embedder)

    @property
    def storage_dir(self) -> Path:
        """Resolve the atomically published snapshot while supporting legacy flat indexes."""
        pointer = self.index_dir / "CURRENT"
        if not pointer.exists():
            return self.index_dir
        snapshot_name = pointer.read_text(encoding="utf-8").strip()
        if not snapshot_name or Path(snapshot_name).name != snapshot_name:
            raise RuntimeError("Knowledge CURRENT pointer is invalid")
        candidate = (self.index_dir / "snapshots" / snapshot_name).resolve()
        snapshots_root = (self.index_dir / "snapshots").resolve()
        if snapshots_root not in candidate.parents:
            raise RuntimeError("Knowledge CURRENT pointer escapes the snapshot directory")
        return candidate

    def build(self, catalog: Catalog) -> IndexBuildStats:
        if not catalog.artifacts:
            raise ValueError("No semantic artifacts were produced from the documents")
        texts = [self._embedding_text(item) for item in catalog.artifacts]
        embeddings = self.embedder.encode_documents(texts)
        if embeddings.shape[0] != len(catalog.artifacts):
            raise RuntimeError("Embedding provider returned an unexpected number of vectors")
        stats = IndexBuildStats(added=len(catalog.artifacts))
        self._persist(catalog, embeddings, stats, generation=1)
        return stats

    def build_incremental(self, catalog: Catalog) -> IndexBuildStats:
        if not catalog.artifacts:
            raise ValueError("No semantic artifacts were produced from the documents")
        old_artifacts: list[Artifact] = []
        old_embeddings = np.empty((0, 0), dtype=np.float32)
        generation = 1
        manifest_path = self.storage_dir / "manifest.json"
        if manifest_path.exists():
            previous_manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            generation = int(previous_manifest.get("generation", 0)) + 1
            if previous_manifest.get("embedding_model") == self.embedder.model_name:
                self.load()
                old_artifacts = self.artifacts
                old_embeddings = self.embeddings

        old_positions = {artifact.id: index for index, artifact in enumerate(old_artifacts)}
        new_ids = {artifact.id for artifact in catalog.artifacts}
        stats = IndexBuildStats(deleted=sum(1 for artifact in old_artifacts if artifact.id not in new_ids))
        pending: list[tuple[int, Artifact]] = []
        rows: list[np.ndarray | None] = [None] * len(catalog.artifacts)
        for index, artifact in enumerate(catalog.artifacts):
            old_index = old_positions.get(artifact.id)
            if old_index is None:
                stats.added += 1
                pending.append((index, artifact))
                continue
            previous = old_artifacts[old_index]
            if self._fingerprint(previous) == self._fingerprint(artifact):
                stats.reused += 1
                rows[index] = old_embeddings[old_index]
            else:
                stats.updated += 1
                pending.append((index, artifact))

        if pending:
            changed_vectors = self.embedder.encode_documents(
                [self._embedding_text(artifact) for _, artifact in pending]
            )
            if changed_vectors.shape[0] != len(pending):
                raise RuntimeError("Embedding provider returned an unexpected number of vectors")
            for (index, _), vector in zip(pending, changed_vectors, strict=True):
                rows[index] = vector

        if any(row is None for row in rows):
            raise RuntimeError("Incremental vector assembly left an empty row")
        embeddings = np.vstack(rows).astype(np.float32, copy=False)
        self._persist(catalog, embeddings, stats, generation=generation)
        return stats

    def _persist(
        self,
        catalog: Catalog,
        embeddings: np.ndarray,
        stats: IndexBuildStats,
        *,
        generation: int,
    ) -> None:
        target_dir = self.storage_dir
        target_dir.mkdir(parents=True, exist_ok=True)
        artifact_temp = target_dir / "artifacts.jsonl.tmp"
        vector_temp = target_dir / "embeddings.tmp.npy"
        graph_temp = target_dir / "foreign_keys.json.tmp"
        manifest_temp = target_dir / "manifest.json.tmp"

        with artifact_temp.open("w", encoding="utf-8") as handle:
            for artifact in catalog.artifacts:
                handle.write(json.dumps(artifact.model_dump(mode="json"), ensure_ascii=False) + "\n")
        np.save(vector_temp, np.asarray(embeddings, dtype=np.float32), allow_pickle=False)
        graph_temp.write_text(
            json.dumps(
                [edge.model_dump(mode="json") for edge in catalog.foreign_keys],
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        manifest = {
            "version": 3,
            "generation": generation,
            "content_hash": catalog.content_hash,
            "embedding_model": self.embedder.model_name,
            "embedding_dimensions": int(embeddings.shape[1]),
            "artifact_count": len(catalog.artifacts),
            "foreign_key_count": len(catalog.foreign_keys),
            "artifacts_added": stats.added,
            "artifacts_updated": stats.updated,
            "artifacts_reused": stats.reused,
            "artifacts_deleted": stats.deleted,
            "vector_reuse_ratio": round(stats.reuse_ratio, 6),
            "source_file_count": catalog.source_file_count,
            "databases": catalog.databases,
            "schema_versions": catalog.schema_versions,
            "snapshot_version": catalog.snapshot_version,
            "active_schema_version": (catalog.active_schema_version or catalog.snapshot_version),
        }
        manifest_temp.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")

        os.replace(artifact_temp, target_dir / "artifacts.jsonl")
        os.replace(vector_temp, target_dir / "embeddings.npy")
        os.replace(graph_temp, target_dir / "foreign_keys.json")
        os.replace(manifest_temp, target_dir / "manifest.json")
        self.load()

    @classmethod
    def _fingerprint(cls, artifact: Artifact) -> str:
        if artifact.content_hash:
            return artifact.content_hash
        payload = json.dumps(
            artifact.model_dump(mode="json", exclude={"content_hash"}),
            ensure_ascii=False,
            sort_keys=True,
        )
        import hashlib

        return hashlib.sha256(payload.encode()).hexdigest()

    def load(self) -> None:
        required = ["artifacts.jsonl", "embeddings.npy", "foreign_keys.json", "manifest.json"]
        source_dir = self.storage_dir
        missing = [name for name in required if not (source_dir / name).exists()]
        if missing:
            raise FileNotFoundError(f"Knowledge index is incomplete; missing: {', '.join(missing)}")

        with (source_dir / "artifacts.jsonl").open(encoding="utf-8") as handle:
            self.artifacts = [Artifact.model_validate_json(line) for line in handle if line.strip()]
        self.embeddings = np.load(source_dir / "embeddings.npy", allow_pickle=False)
        raw_edges = json.loads((source_dir / "foreign_keys.json").read_text(encoding="utf-8"))
        self.foreign_keys = [ForeignKey.model_validate(item) for item in raw_edges]
        self.manifest = json.loads((source_dir / "manifest.json").read_text(encoding="utf-8"))

        if self.embeddings.shape[0] != len(self.artifacts):
            raise RuntimeError("Artifact and embedding counts do not match")
        if self.manifest.get("embedding_model") != self.embedder.model_name:
            raise RuntimeError(
                "Configured embedding model differs from the built index; rebuild the knowledge index"
            )

        self.bm25 = BM25Index([self._keyword_text(item) for item in self.artifacts])
        self.graph = ForeignKeyGraph(self.foreign_keys)
        self._table_artifacts = defaultdict(list)
        for index, artifact in enumerate(self.artifacts):
            if artifact.kind == ArtifactKind.TABLE:
                for table in artifact.tables:
                    self._table_artifacts[table.lower()].append(index)

    def search(
        self,
        query: str,
        top_k: int = 36,
        graph_depth: int = 1,
        schema_version: str | None = None,
    ) -> list[SearchHit]:
        if not self.is_ready:
            raise RuntimeError("Knowledge index is not loaded")
        query_vector = np.asarray(self.embedder.encode_query(query), dtype=np.float32)
        if query_vector.shape[0] != self.embeddings.shape[1]:
            raise RuntimeError("Query embedding dimensions do not match the knowledge index")

        dense_scores = self.embeddings @ query_vector
        keyword_scores = self.bm25.scores(query)
        eligible_indices = self._eligible_indices(schema_version)
        if not eligible_indices:
            return []
        candidate_size = min(len(eligible_indices), max(top_k * 3, 60))
        dense_rank = self._rank_subset(dense_scores, eligible_indices, candidate_size)
        keyword_rank = self._rank_subset(keyword_scores, eligible_indices, candidate_size)
        fused: dict[int, float] = defaultdict(float)
        for rank, index in enumerate(dense_rank, start=1):
            fused[index] += 0.55 / (60 + rank)
        for rank, index in enumerate(keyword_rank, start=1):
            fused[index] += 0.45 / (60 + rank)

        normalized_query = normalize_persian(query).lower()
        for index in eligible_indices:
            artifact = self.artifacts[index]
            if any(table.lower() in normalized_query for table in artifact.tables):
                fused[index] += 0.03

        initial_indices = sorted(fused, key=fused.get, reverse=True)[:top_k]
        seeds = {table for index in initial_indices[:12] for table in self.artifacts[index].tables}
        expanded = self._graph_for_version(schema_version).expand(seeds, depth=graph_depth)
        best_score = max((fused[index] for index in initial_indices), default=0.01)
        graph_indices: set[int] = set()
        for table, distance in expanded.items():
            for index in self._table_artifacts.get(table.lower(), []):
                if index not in eligible_indices:
                    continue
                graph_indices.add(index)
                fused[index] = max(fused.get(index, 0.0), best_score * (0.72 / distance))

        final_indices = sorted(set(initial_indices) | graph_indices, key=lambda idx: fused[idx], reverse=True)
        return [
            SearchHit(
                artifact=self.artifacts[index],
                score=float(max(fused[index], 0.0)),
                dense_score=float(dense_scores[index]),
                keyword_score=float(keyword_scores[index]),
                graph_expanded=index in graph_indices and index not in initial_indices,
                retrieval_channels=[
                    *(["dense"] if index in dense_rank else []),
                    *(["bm25"] if index in keyword_rank else []),
                    *(["graph"] if index in graph_indices else []),
                ],
            )
            for index in final_indices
        ]

    @staticmethod
    def _top_indices(scores: np.ndarray, count: int) -> list[int]:
        if count <= 0 or len(scores) == 0:
            return []
        count = min(count, len(scores))
        if count == len(scores):
            return np.argsort(scores)[::-1].tolist()
        partition = np.argpartition(scores, -count)[-count:]
        return partition[np.argsort(scores[partition])[::-1]].tolist()

    @staticmethod
    def _rank_subset(scores: np.ndarray, indices: set[int], count: int) -> list[int]:
        if not indices or count <= 0:
            return []
        ordered = np.asarray(sorted(indices), dtype=np.int64)
        subset_scores = scores[ordered]
        local_positions = HybridKnowledgeBase._top_indices(subset_scores, count)
        return [int(ordered[position]) for position in local_positions]

    def _eligible_indices(self, schema_version: str | None) -> set[int]:
        searchable = {
            index
            for index, artifact in enumerate(self.artifacts)
            if artifact.kind != ArtifactKind.TECHNICAL_METADATA
        }
        if not schema_version or schema_version == self.manifest.get("snapshot_version"):
            return searchable
        candidates = {
            index
            for index in searchable
            if self.artifacts[index].document_version in {"unversioned", schema_version}
        }
        overrides = {
            self._logical_key(self.artifacts[index])
            for index in candidates
            if self.artifacts[index].document_version == schema_version
        }
        return {
            index
            for index in candidates
            if self.artifacts[index].document_version == schema_version
            or self._logical_key(self.artifacts[index]) not in overrides
        }

    def _graph_for_version(self, schema_version: str | None) -> ForeignKeyGraph:
        if not schema_version or schema_version == self.manifest.get("snapshot_version"):
            return self.graph
        candidates = [
            edge for edge in self.foreign_keys if edge.document_version in {"unversioned", schema_version}
        ]
        by_name: dict[str, ForeignKey] = {}
        for edge in candidates:
            key = edge.name.lower()
            previous = by_name.get(key)
            if previous is None or edge.document_version == schema_version:
                by_name[key] = edge
        return ForeignKeyGraph(list(by_name.values()))

    @staticmethod
    def _logical_key(artifact: Artifact) -> tuple[str, str]:
        if artifact.kind == ArtifactKind.TABLE:
            schema = str(artifact.metadata.get("schema", "dbo")).lower()
            table = str(artifact.metadata.get("table", artifact.title)).lower()
            return artifact.kind.value, f"{schema}.{table}"
        return artifact.kind.value, artifact.title.lower()

    @staticmethod
    def _embedding_text(artifact: Artifact) -> str:
        tables = " ".join(artifact.tables)
        return (
            f"{artifact.kind.value} version:{artifact.document_version} "
            f"{artifact.title} {tables}\n{artifact.text}"
        )

    @staticmethod
    def _keyword_text(artifact: Artifact) -> str:
        # Repeating exact identifiers gives BM25 a useful schema-name prior.
        identifiers = " ".join(artifact.tables * 2)
        return f"{artifact.title} {identifiers} {artifact.text}"
