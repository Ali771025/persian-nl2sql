from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol

import numpy as np

from .models import Catalog


@dataclass(frozen=True)
class VectorMatch:
    artifact_id: str
    score: float


@dataclass
class KnowledgeSyncReport:
    qdrant_points: int = 0
    graph_nodes: int = 0
    errors: list[str] = field(default_factory=list)
    snapshot_id: str = ""
    generation: int = 0
    phase: str = "idle"


@dataclass(frozen=True)
class StagedStoreSnapshot:
    """Opaque store-specific snapshot prepared but not yet visible to readers."""

    snapshot_id: str
    reference: str
    item_count: int


class VectorKnowledgeStore(Protocol):
    name: str

    def sync(
        self,
        catalog: Catalog,
        embeddings: np.ndarray,
        embedding_model: str,
        generation: int,
    ) -> int: ...

    def stage(
        self,
        catalog: Catalog,
        embeddings: np.ndarray,
        embedding_model: str,
        generation: int,
    ) -> StagedStoreSnapshot: ...

    def activate(self, snapshot: StagedStoreSnapshot) -> None: ...

    def active_reference(self) -> str | None: ...

    def restore(self, reference: str | None) -> None: ...

    def finalize(self, snapshot: StagedStoreSnapshot) -> None: ...

    def abort(self, snapshot: StagedStoreSnapshot) -> None: ...

    def search(
        self,
        vector: np.ndarray,
        limit: int,
        schema_version: str | None = None,
        snapshot_id: str | None = None,
    ) -> list[VectorMatch]: ...

    def status(self) -> dict[str, object]: ...

    def close(self) -> None: ...


class GraphKnowledgeStore(Protocol):
    name: str

    def sync(self, catalog: Catalog, generation: int) -> int: ...

    def stage(self, catalog: Catalog, generation: int) -> StagedStoreSnapshot: ...

    def activate(self, snapshot: StagedStoreSnapshot) -> None: ...

    def active_reference(self) -> str | None: ...

    def restore(self, reference: str | None) -> None: ...

    def finalize(self, snapshot: StagedStoreSnapshot) -> None: ...

    def abort(self, snapshot: StagedStoreSnapshot) -> None: ...

    def related_tables(
        self,
        seeds: set[str],
        terms: list[str],
        depth: int,
        schema_version: str | None = None,
        snapshot_id: str | None = None,
    ) -> dict[str, int]: ...

    def status(self) -> dict[str, object]: ...

    def close(self) -> None: ...


class KnowledgeCache(Protocol):
    name: str

    def get_json(self, key: str) -> object | None: ...

    def set_json(self, key: str, value: object, ttl_seconds: int) -> None: ...

    def acquire_lock(self, name: str, timeout_seconds: int) -> object | None: ...

    def release_lock(self, lock: object) -> None: ...

    def renew_lock(self, lock: object, timeout_seconds: int) -> bool: ...

    def fencing_token(self, lock: object) -> int: ...

    def publish_snapshot(self, payload: dict[str, object], fencing_token: int) -> bool: ...

    def active_snapshot(self) -> dict[str, object] | None: ...

    def status(self) -> dict[str, object]: ...

    def close(self) -> None: ...
