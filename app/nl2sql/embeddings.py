from __future__ import annotations

import hashlib
from collections.abc import Sequence
from typing import Protocol

import numpy as np

from .normalization import tokenize


class Embedder(Protocol):
    model_name: str

    def encode_documents(self, texts: Sequence[str]) -> np.ndarray: ...

    def encode_query(self, text: str) -> np.ndarray: ...


def _normalize(matrix: np.ndarray) -> np.ndarray:
    values = np.asarray(matrix, dtype=np.float32)
    if values.ndim == 1:
        values = values.reshape(1, -1)
    norms = np.linalg.norm(values, axis=1, keepdims=True)
    norms[norms == 0] = 1.0
    return values / norms


class SentenceTransformerEmbedder:
    """Lazy local Transformer embedder; no silent random-vector fallback."""

    def __init__(
        self,
        model_name: str,
        device: str = "cpu",
        batch_size: int = 32,
        revision: str = "",
    ):
        self.base_model_name = model_name
        self.revision = revision
        self.model_name = f"{model_name}@{revision}" if revision else model_name
        self.device = device
        self.batch_size = batch_size
        self._model = None

    def _load(self):
        if self._model is None:
            try:
                from sentence_transformers import SentenceTransformer
            except ImportError as exc:
                raise RuntimeError(
                    "sentence-transformers is required for the configured embedding provider"
                ) from exc
            self._model = SentenceTransformer(
                self.base_model_name,
                device=self.device,
                revision=self.revision or None,
                trust_remote_code=False,
            )
        return self._model

    def encode_documents(self, texts: Sequence[str]) -> np.ndarray:
        values = [self._prefix(value, query=False) for value in texts]
        return np.asarray(
            self._load().encode(
                values,
                batch_size=self.batch_size,
                normalize_embeddings=True,
                show_progress_bar=len(values) > self.batch_size,
            ),
            dtype=np.float32,
        )

    def encode_query(self, text: str) -> np.ndarray:
        value = self._load().encode(
            [self._prefix(text, query=True)],
            normalize_embeddings=True,
            show_progress_bar=False,
        )
        return np.asarray(value[0], dtype=np.float32)

    def _prefix(self, text: str, *, query: bool) -> str:
        if "e5" in self.model_name.lower():
            return f"{'query' if query else 'passage'}: {text}"
        return text


class FeatureHashEmbedder:
    """Deterministic lexical feature hashing for tests; not a semantic production substitute."""

    def __init__(self, dimensions: int = 512):
        self.dimensions = dimensions
        self.model_name = f"feature-hash-{dimensions}"

    def encode_documents(self, texts: Sequence[str]) -> np.ndarray:
        return _normalize(np.vstack([self._encode(value) for value in texts]))

    def encode_query(self, text: str) -> np.ndarray:
        return _normalize(self._encode(text))[0]

    def _encode(self, text: str) -> np.ndarray:
        vector = np.zeros(self.dimensions, dtype=np.float32)
        tokens = tokenize(text)
        features = tokens + [f"{tokens[i]}::{tokens[i + 1]}" for i in range(len(tokens) - 1)]
        for feature in features:
            digest = hashlib.blake2b(feature.encode("utf-8"), digest_size=8).digest()
            number = int.from_bytes(digest, "little")
            index = number % self.dimensions
            sign = 1.0 if (number >> 8) & 1 else -1.0
            vector[index] += sign
        return vector


def build_embedder(
    provider: str,
    model: str,
    device: str,
    batch_size: int,
    revision: str = "",
) -> Embedder:
    if provider == "sentence_transformer":
        return SentenceTransformerEmbedder(
            model_name=model,
            device=device,
            batch_size=batch_size,
            revision=revision,
        )
    if provider == "feature_hash":
        return FeatureHashEmbedder()
    raise ValueError(f"Unsupported embedding provider: {provider}")
