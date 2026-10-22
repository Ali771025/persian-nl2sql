from __future__ import annotations

import math
import threading
from pathlib import Path
from typing import Protocol

from .models import SearchHit
from .normalization import tokenize


class Reranker(Protocol):
    model_name: str

    def warmup(self) -> None: ...

    def rerank(self, query: str, hits: list[SearchHit], top_k: int) -> list[SearchHit]: ...


class CrossEncoderReranker:
    def __init__(
        self,
        model_name: str,
        device: str = "cpu",
        revision: str = "",
        batch_size: int = 2,
        max_length: int = 512,
        onnx_model_name: str = "",
        onnx_revision: str = "",
        onnx_file: str = "onnx/model_quantized.onnx",
    ):
        self.base_model_name = model_name
        self.revision = revision
        pinned_name = f"{model_name}@{revision}" if revision else model_name
        self.model_name = f"{pinned_name} (ONNX INT8)"
        self.device = device
        self.batch_size = batch_size
        self.max_length = max_length
        self.onnx_model_name = onnx_model_name or model_name
        self.onnx_revision = onnx_revision or revision
        self.onnx_file = onnx_file
        self._tokenizer = None
        self._session = None
        # CrossEncoder.predict can release the GIL. Serializing inference prevents
        # concurrent activation tensors from pushing the CPU container over its
        # deliberately small memory budget.
        self._inference_lock = threading.Lock()

    def _load(self):
        if self._session is None:
            if self.device != "cpu":
                raise RuntimeError("The memory-bounded ONNX reranker supports device=cpu only")
            try:
                import onnxruntime as ort
                from huggingface_hub import snapshot_download
                from transformers import AutoTokenizer
            except ImportError as exc:
                raise RuntimeError(
                    "onnxruntime, transformers and huggingface-hub are required "
                    "for cross-encoder reranking"
                ) from exc
            local_model_dir = Path(self.onnx_model_name)
            if local_model_dir.is_dir():
                model_dir = str(local_model_dir)
            else:
                model_dir = snapshot_download(
                    self.onnx_model_name,
                    revision=self.onnx_revision or None,
                    local_files_only=True,
                    allow_patterns=["*.json", "*.model", self.onnx_file],
                )
            self._tokenizer = AutoTokenizer.from_pretrained(
                model_dir,
                trust_remote_code=False,
                local_files_only=True,
            )
            options = ort.SessionOptions()
            options.intra_op_num_threads = 2
            options.inter_op_num_threads = 1
            options.execution_mode = ort.ExecutionMode.ORT_SEQUENTIAL
            options.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
            self._session = ort.InferenceSession(
                str(Path(model_dir) / self.onnx_file),
                sess_options=options,
                providers=["CPUExecutionProvider"],
            )
        return self._tokenizer, self._session

    def warmup(self) -> None:
        self._load()

    def rerank(self, query: str, hits: list[SearchHit], top_k: int) -> list[SearchHit]:
        if not hits:
            return []
        pairs = [(query, hit.artifact.text[:12_000]) for hit in hits]
        raw_scores = self._model_scores(pairs)
        reranked = []
        for hit, raw in zip(hits, raw_scores, strict=True):
            normalized = 1.0 / (1.0 + math.exp(-max(-30.0, min(30.0, float(raw)))))
            reranked.append(hit.model_copy(update={"rerank_score": normalized, "score": normalized}))
        reranked.sort(key=lambda item: item.score, reverse=True)
        return reranked[:top_k]

    def _model_scores(self, pairs: list[tuple[str, str]]):
        import numpy as np

        with self._inference_lock:
            tokenizer, session = self._load()
            input_names = {item.name for item in session.get_inputs()}
            batches = []
            for start in range(0, len(pairs), self.batch_size):
                batch = pairs[start : start + self.batch_size]
                encoded = tokenizer(
                    [query for query, _ in batch],
                    [document for _, document in batch],
                    padding=True,
                    truncation=True,
                    max_length=self.max_length,
                    return_tensors="np",
                )
                inputs = {name: value for name, value in encoded.items() if name in input_names}
                logits = session.run(None, inputs)[0]
                batches.append(np.asarray(logits).reshape(-1))
            scores = np.concatenate(batches)
        if getattr(scores, "ndim", 1) > 1:
            scores = scores[:, -1]
        return scores


class LexicalReranker:
    def __init__(self):
        self.model_name = "lexical-overlap"

    def warmup(self) -> None:
        return None

    def rerank(self, query: str, hits: list[SearchHit], top_k: int) -> list[SearchHit]:
        query_tokens = set(tokenize(query))
        output = []
        for hit in hits:
            document_tokens = set(tokenize(hit.artifact.text))
            overlap = len(query_tokens & document_tokens) / max(1, len(query_tokens))
            exact_table = any(table.lower() in query.lower() for table in hit.artifact.tables)
            retrieval_signal = hit.score / (hit.score + 0.05)
            score = (
                0.65 * overlap
                + 0.30 * retrieval_signal
                + (0.05 if exact_table else 0.0)
            )
            output.append(hit.model_copy(update={"rerank_score": score, "score": score}))
        output.sort(key=lambda item: item.score, reverse=True)
        return output[:top_k]


def build_reranker(
    provider: str,
    model: str,
    device: str,
    revision: str = "",
    batch_size: int = 2,
    max_length: int = 512,
    onnx_model_name: str = "",
    onnx_revision: str = "",
    onnx_file: str = "onnx/model_quantized.onnx",
) -> Reranker:
    if provider == "cross_encoder":
        return CrossEncoderReranker(
            model_name=model,
            device=device,
            revision=revision,
            batch_size=batch_size,
            max_length=max_length,
            onnx_model_name=onnx_model_name,
            onnx_revision=onnx_revision,
            onnx_file=onnx_file,
        )
    if provider == "lexical":
        return LexicalReranker()
    raise ValueError(f"Unsupported reranker provider: {provider}")
