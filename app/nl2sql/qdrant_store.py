from __future__ import annotations

import uuid
from collections.abc import Iterable

import numpy as np

from .knowledge_protocols import StagedStoreSnapshot, VectorMatch
from .models import Artifact, Catalog


class QdrantKnowledgeStore:
    """Versioned Qdrant collections with an atomic alias for the active snapshot."""

    name = "qdrant"

    def __init__(
        self,
        url: str,
        collection: str,
        *,
        api_key: str = "",
        timeout_seconds: float = 10.0,
        batch_size: int = 128,
        retained_snapshots: int = 3,
        client=None,
    ):
        self.url = url
        self.collection_alias = collection
        self.batch_size = max(1, batch_size)
        self.retained_snapshots = max(1, retained_snapshots)
        if client is None:
            try:
                from qdrant_client import QdrantClient
            except ImportError as exc:
                raise RuntimeError(
                    "qdrant-client package is required for distributed knowledge mode"
                ) from exc
            client = QdrantClient(
                url=url,
                api_key=api_key or None,
                timeout=timeout_seconds,
                prefer_grpc=False,
                check_compatibility=False,
            )
        self.client = client
        self.active_collection: str | None = None

    @staticmethod
    def _models():
        from qdrant_client import models

        return models

    @staticmethod
    def _point_id(artifact_id: str) -> str:
        return str(uuid.uuid5(uuid.NAMESPACE_URL, f"nl2sql-artifact:{artifact_id}"))

    def sync(
        self,
        catalog: Catalog,
        embeddings: np.ndarray,
        embedding_model: str,
        generation: int,
    ) -> int:
        staged = self.stage(catalog, embeddings, embedding_model, generation)
        self.activate(staged)
        self.finalize(staged)
        return staged.item_count

    def stage(
        self,
        catalog: Catalog,
        embeddings: np.ndarray,
        embedding_model: str,
        generation: int,
    ) -> StagedStoreSnapshot:
        """Create and verify an immutable collection without changing the read alias."""
        if len(catalog.artifacts) != embeddings.shape[0]:
            raise ValueError("Artifact/vector count mismatch before Qdrant synchronization")
        if embeddings.ndim != 2 or not embeddings.shape[1]:
            raise ValueError("Qdrant synchronization requires two-dimensional embeddings")

        models = self._models()
        collection_name = f"{self.collection_alias}__g{generation:08d}_{catalog.content_hash[:12]}"
        if not self.client.collection_exists(collection_name):
            self.client.create_collection(
                collection_name=collection_name,
                vectors_config=models.VectorParams(
                    size=int(embeddings.shape[1]),
                    distance=models.Distance.COSINE,
                ),
                optimizers_config=models.OptimizersConfigDiff(indexing_threshold=10_000),
            )
            self.client.create_payload_index(
                collection_name,
                field_name="kind",
                field_schema=models.PayloadSchemaType.KEYWORD,
            )
            self.client.create_payload_index(
                collection_name,
                field_name="document_version",
                field_schema=models.PayloadSchemaType.KEYWORD,
            )
            self.client.create_payload_index(
                collection_name,
                field_name="snapshot_version",
                field_schema=models.PayloadSchemaType.KEYWORD,
            )
            self.client.create_payload_index(
                collection_name,
                field_name="snapshot_id",
                field_schema=models.PayloadSchemaType.KEYWORD,
            )

        for positions in self._batches(range(len(catalog.artifacts)), self.batch_size):
            points = [
                models.PointStruct(
                    id=self._point_id(catalog.artifacts[position].id),
                    vector=embeddings[position].astype(float).tolist(),
                    payload=self._payload(
                        catalog.artifacts[position],
                        catalog,
                        embedding_model,
                        generation,
                    ),
                )
                for position in positions
            ]
            self.client.upsert(collection_name=collection_name, points=points, wait=True)

        info = self.client.get_collection(collection_name)
        actual = int(info.points_count or 0)
        if actual != len(catalog.artifacts):
            raise RuntimeError(
                f"Qdrant staged point count mismatch: expected={len(catalog.artifacts)} actual={actual}"
            )
        return StagedStoreSnapshot(
            snapshot_id=catalog.content_hash,
            reference=collection_name,
            item_count=actual,
        )

    def activate(self, snapshot: StagedStoreSnapshot) -> None:
        self._activate_alias(snapshot.reference)
        self.active_collection = snapshot.reference

    def finalize(self, snapshot: StagedStoreSnapshot) -> None:
        self._prune_old_snapshots(snapshot.reference)

    def active_reference(self) -> str | None:
        aliases = self.client.get_aliases().aliases
        return next(
            (alias.collection_name for alias in aliases if alias.alias_name == self.collection_alias),
            None,
        )

    def restore(self, reference: str | None) -> None:
        if reference:
            self._activate_alias(reference)
            self.active_collection = reference
            return
        models = self._models()
        aliases = self.client.get_aliases().aliases
        if any(alias.alias_name == self.collection_alias for alias in aliases):
            self.client.update_collection_aliases(
                change_aliases_operations=[
                    models.DeleteAliasOperation(
                        delete_alias=models.DeleteAlias(alias_name=self.collection_alias)
                    )
                ]
            )
        self.active_collection = None

    def abort(self, snapshot: StagedStoreSnapshot) -> None:
        if snapshot.reference != self.active_reference() and self.client.collection_exists(
            snapshot.reference
        ):
            self.client.delete_collection(snapshot.reference)

    def _activate_alias(self, collection_name: str) -> None:
        models = self._models()
        aliases = self.client.get_aliases().aliases
        operations = []
        if any(alias.alias_name == self.collection_alias for alias in aliases):
            operations.append(
                models.DeleteAliasOperation(delete_alias=models.DeleteAlias(alias_name=self.collection_alias))
            )
        operations.append(
            models.CreateAliasOperation(
                create_alias=models.CreateAlias(
                    collection_name=collection_name,
                    alias_name=self.collection_alias,
                )
            )
        )
        self.client.update_collection_aliases(change_aliases_operations=operations)

    def _prune_old_snapshots(self, active_collection: str) -> None:
        prefix = f"{self.collection_alias}__g"
        collections = sorted(
            (item.name for item in self.client.get_collections().collections if item.name.startswith(prefix)),
            reverse=True,
        )
        retained = set(collections[: self.retained_snapshots]) | {active_collection}
        for collection in collections:
            if collection not in retained:
                self.client.delete_collection(collection)

    def search(
        self,
        vector: np.ndarray,
        limit: int,
        schema_version: str | None = None,
        snapshot_id: str | None = None,
    ) -> list[VectorMatch]:
        models = self._models()
        must = []
        should = []
        if snapshot_id:
            must.append(
                models.FieldCondition(
                    key="snapshot_id",
                    match=models.MatchValue(value=snapshot_id),
                )
            )
        if schema_version:
            should.extend(
                [
                    models.FieldCondition(
                        key="document_version",
                        match=models.MatchValue(value=schema_version),
                    ),
                    models.FieldCondition(
                        key="snapshot_version",
                        match=models.MatchValue(value=schema_version),
                    ),
                ]
            )
        query_filter = models.Filter(must=must or None, should=should or None) if must or should else None
        response = self.client.query_points(
            collection_name=self.collection_alias,
            query=np.asarray(vector, dtype=np.float32).astype(float).tolist(),
            query_filter=query_filter,
            limit=limit,
            with_payload=["artifact_id"],
            with_vectors=False,
        )
        return [
            VectorMatch(
                artifact_id=str(point.payload.get("artifact_id", "")),
                score=float(point.score),
            )
            for point in response.points
            if point.payload and point.payload.get("artifact_id")
        ]

    def status(self) -> dict[str, object]:
        try:
            aliases = self.client.get_aliases().aliases
            active = next(
                (alias.collection_name for alias in aliases if alias.alias_name == self.collection_alias),
                None,
            )
            points = 0
            if active:
                info = self.client.get_collection(active)
                points = int(info.points_count or 0)
            snapshot_id = self._snapshot_id_for_collection(active) if active else None
            self.active_collection = active
            return {
                "enabled": True,
                "connected": True,
                "backend": "qdrant",
                "alias": self.collection_alias,
                "active_collection": active,
                "snapshot_id": snapshot_id,
                "points": points,
            }
        except Exception as exc:
            return {
                "enabled": True,
                "connected": False,
                "backend": "qdrant",
                "error": type(exc).__name__,
            }

    def close(self) -> None:
        self.client.close()

    def _snapshot_id_for_collection(self, collection: str) -> str | None:
        try:
            points, _ = self.client.scroll(
                collection_name=collection,
                limit=1,
                with_payload=["snapshot_id"],
                with_vectors=False,
            )
            if points and points[0].payload:
                return str(points[0].payload.get("snapshot_id") or "") or None
        except Exception:
            return None
        return None

    @staticmethod
    def _payload(
        artifact: Artifact,
        catalog: Catalog,
        embedding_model: str,
        generation: int,
    ) -> dict[str, object]:
        return {
            "artifact_id": artifact.id,
            "kind": artifact.kind.value,
            "title": artifact.title,
            "source_path": artifact.source_path,
            "tables": artifact.tables,
            "doc_id": artifact.doc_id,
            "document_version": artifact.document_version,
            "content_hash": artifact.content_hash,
            "snapshot_id": catalog.content_hash,
            "snapshot_version": catalog.snapshot_version,
            "generation": generation,
            "embedding_model": embedding_model,
        }

    @staticmethod
    def _batches(values: Iterable[int], size: int) -> Iterable[list[int]]:
        batch: list[int] = []
        for value in values:
            batch.append(value)
            if len(batch) >= size:
                yield batch
                batch = []
        if batch:
            yield batch
