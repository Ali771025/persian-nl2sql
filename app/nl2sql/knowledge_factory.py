from __future__ import annotations

from .config import Settings
from .distributed_index import DistributedKnowledgeBase
from .embeddings import Embedder
from .index import HybridKnowledgeBase
from .neo4j_store import Neo4jKnowledgeGraph
from .qdrant_store import QdrantKnowledgeStore
from .redis_store import RedisKnowledgeCache


def build_knowledge_base(settings: Settings, embedder: Embedder) -> HybridKnowledgeBase:
    if not settings.distributed_knowledge_enabled:
        return HybridKnowledgeBase(settings.index_dir, embedder)
    try:
        cache = RedisKnowledgeCache(settings.redis_url)
        vector_store = QdrantKnowledgeStore(
            settings.qdrant_url,
            settings.qdrant_collection,
            api_key=settings.qdrant_api_key,
            timeout_seconds=settings.qdrant_timeout_seconds,
            batch_size=settings.qdrant_batch_size,
            retained_snapshots=settings.qdrant_retained_snapshots,
        )
        graph_store = Neo4jKnowledgeGraph(
            settings.neo4j_uri,
            settings.neo4j_user,
            settings.neo4j_password,
            database=settings.neo4j_database,
            timeout_seconds=settings.neo4j_timeout_seconds,
        )
    except Exception:
        if not settings.distributed_fail_open:
            raise
        return HybridKnowledgeBase(settings.index_dir, embedder)
    return DistributedKnowledgeBase(
        settings.index_dir,
        embedder,
        vector_store=vector_store,
        graph_store=graph_store,
        cache=cache,
        cache_ttl_seconds=settings.redis_cache_ttl_seconds,
        ingest_lock_seconds=settings.redis_ingest_lock_seconds,
        fail_open=settings.distributed_fail_open,
    )
