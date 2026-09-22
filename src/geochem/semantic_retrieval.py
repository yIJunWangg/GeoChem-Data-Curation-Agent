"""Structure-aware evidence chunking and optional dense vector retrieval."""

from __future__ import annotations

from abc import ABC, abstractmethod
import hashlib
import json
import os
from pathlib import Path
import re
from threading import Lock
from typing import Any, Iterable
from uuid import NAMESPACE_URL, uuid5

from .agent_models import EvidenceChunk
from .core.logging_config import get_logger


logger = get_logger("semantic_retrieval")


class EmbeddingProvider(ABC):
    dimension: int
    model_version: str

    @abstractmethod
    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        raise NotImplementedError

    @abstractmethod
    def embed_query(self, text: str) -> list[float]:
        raise NotImplementedError


class BgeM3EmbeddingProvider(EmbeddingProvider):
    """Lazy, process-local dense BGE-M3 provider."""

    dimension = 1024
    _models: dict[tuple[str, str], Any] = {}
    _model_lock = Lock()

    def __init__(
        self,
        model_name: str = "BAAI/bge-m3",
        *,
        batch_size: int = 8,
        max_tokens: int = 512,
        cache_dir: Path | None = None,
    ) -> None:
        self.model_name = model_name
        self.batch_size = batch_size
        self.max_tokens = max_tokens
        self.cache_dir = cache_dir
        self.model_version = model_name

    def _model(self):
        cache_key = str(self.cache_dir or "")
        key = (self.model_name, cache_key)
        if key in self._models:
            return self._models[key]
        with self._model_lock:
            if key in self._models:
                return self._models[key]
            if self.cache_dir:
                self.cache_dir.mkdir(parents=True, exist_ok=True)
                os.environ.setdefault("HF_HOME", str(self.cache_dir))
            try:
                from FlagEmbedding import BGEM3FlagModel
            except ImportError as exc:
                raise RuntimeError(
                    "Vector retrieval requires the 'vector' optional dependencies: "
                    "pip install -e '.[vector]'"
                ) from exc

            devices: list[str] = ["cpu"]
            use_fp16 = False
            try:
                import torch

                if torch.cuda.is_available():
                    devices = ["cuda:0"]
                    use_fp16 = True
                elif getattr(torch.backends, "mps", None) and torch.backends.mps.is_available():
                    devices = ["mps"]
                    use_fp16 = True
            except Exception:
                pass
            try:
                model = BGEM3FlagModel(
                    self.model_name,
                    use_fp16=use_fp16,
                    devices=devices,
                )
            except Exception:
                if devices == ["cpu"]:
                    raise
                logger.warning("BGE-M3 accelerator initialization failed; falling back to CPU")
                model = BGEM3FlagModel(self.model_name, use_fp16=False, devices=["cpu"])
            self._models[key] = model
            return model

    @staticmethod
    def _as_vectors(value: Any) -> list[list[float]]:
        if hasattr(value, "tolist"):
            value = value.tolist()
        return [[float(component) for component in vector] for vector in value]

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        result = self._model().encode(
            texts,
            batch_size=self.batch_size,
            max_length=self.max_tokens,
            return_dense=True,
            return_sparse=False,
            return_colbert_vecs=False,
        )
        return self._as_vectors(result["dense_vecs"])

    def embed_query(self, text: str) -> list[float]:
        vectors = self.embed_documents([text])
        return vectors[0] if vectors else []


class EvidenceChunker:
    """Create deterministic, provenance-preserving chunks from RAG documents."""

    _TOKEN_RE = re.compile(r"[A-Za-z0-9_.%/‰δΔ+-]+|[\u3400-\u9fff]|[^\s]", re.UNICODE)

    def __init__(self, max_tokens: int = 512, overlap_tokens: int = 64) -> None:
        self.max_tokens = max_tokens
        self.overlap_tokens = min(overlap_tokens, max_tokens // 2)

    def build(self, documents: Iterable[dict[str, Any]]) -> list[EvidenceChunk]:
        chunks: list[EvidenceChunk] = []
        for document in documents:
            chunks.extend(self._document_chunks(document))
        return chunks

    def _document_chunks(self, document: dict[str, Any]) -> list[EvidenceChunk]:
        metadata = dict(document.get("metadata") or {})
        document_type = str(document.get("document_type") or "document")
        element_type = str(metadata.get("element_type") or document_type)
        section = str(metadata.get("section_path") or "")
        caption = str(metadata.get("caption") or metadata.get("source_caption") or "")
        content = str(document.get("content") or "").strip()

        if element_type == "table" and isinstance(metadata.get("raw_table"), dict):
            parts = self._table_parts(metadata["raw_table"], section, caption)
        elif element_type == "figure":
            parts = self._split_text(self._prefix(section, caption, content))
        elif document_type in {"candidate_cell", "standardized_cell", "rule", "statistic"}:
            parts = [self._prefix(section, caption, content)]
        else:
            parts = self._split_text(self._prefix(section, caption, content))

        return [
            self._chunk(document, metadata, element_type, index, part)
            for index, part in enumerate(parts)
            if part.strip()
        ]

    @staticmethod
    def _prefix(section: str, caption: str, content: str) -> str:
        values: list[str] = []
        if section:
            values.append(f"Section: {section}")
        if caption and caption not in content:
            values.append(f"Caption: {caption}")
        values.append(content)
        return "\n".join(value for value in values if value).strip()

    def _table_parts(self, table: dict[str, Any], section: str, caption: str) -> list[str]:
        headers = [str(value) for value in table.get("headers") or []]
        rows = [[str(value) for value in row] for row in table.get("rows") or []]
        prefix_values = [value for value in (f"Section: {section}" if section else "", f"Caption: {caption}" if caption else "") if value]
        header_line = " | ".join(headers)
        base = "\n".join([*prefix_values, f"Headers: {header_line}" if header_line else ""]).strip()
        if not rows:
            body = str(table.get("body_text") or "")
            return self._split_text("\n".join(value for value in (base, body) if value))

        parts: list[str] = []
        current: list[str] = []
        for row in rows:
            line = " | ".join(row)
            candidate = "\n".join(value for value in (base, *current, line) if value)
            if current and self._token_count(candidate) > self.max_tokens:
                parts.append("\n".join(value for value in (base, *current) if value))
                current = []
            current.append(line)
            if self._token_count("\n".join(value for value in (base, *current) if value)) > self.max_tokens:
                oversized = "\n".join(value for value in (base, current.pop()) if value)
                parts.extend(self._split_text(oversized))
        if current:
            parts.append("\n".join(value for value in (base, *current) if value))
        return parts

    def _token_count(self, text: str) -> int:
        return len(self._TOKEN_RE.findall(text))

    def _split_text(self, text: str) -> list[str]:
        matches = list(self._TOKEN_RE.finditer(text))
        if len(matches) <= self.max_tokens:
            return [text.strip()] if text.strip() else []
        parts: list[str] = []
        start_token = 0
        while start_token < len(matches):
            end_token = min(len(matches), start_token + self.max_tokens)
            start_char = matches[start_token].start()
            end_char = matches[end_token - 1].end()
            parts.append(text[start_char:end_char].strip())
            if end_token == len(matches):
                break
            start_token = end_token - self.overlap_tokens
        return parts

    def _chunk(
        self,
        document: dict[str, Any],
        metadata: dict[str, Any],
        element_type: str,
        index: int,
        content: str,
    ) -> EvidenceChunk:
        content_hash = hashlib.sha256(content.encode("utf-8")).hexdigest()
        parent_id = str(document["document_id"])
        identity = hashlib.sha256(f"{parent_id}:{index}:{content_hash}".encode()).hexdigest()[:24].upper()
        return EvidenceChunk(
            chunk_id=f"CHK_{identity}",
            parent_document_id=parent_id,
            project_id=str(document["project_id"]),
            article_id=str(document.get("article_id") or ""),
            resource_id=str(document.get("resource_id") or ""),
            element_id=str(document.get("element_id") or ""),
            record_id=str(document.get("record_id") or ""),
            cell_id=str(document.get("cell_id") or ""),
            content=content,
            content_hash=content_hash,
            element_type=element_type,
            page_number=self._int_or_none(metadata.get("page_number")),
            page_spans=self._list_value(metadata.get("page_spans")),
            section_path=str(metadata.get("section_path") or ""),
            bbox=self._list_value(metadata.get("bbox")),
            reading_order=int(metadata.get("reading_order") or 0),
            metadata={
                **metadata,
                "chunk_index": index,
                "parent_document_type": str(document.get("document_type") or element_type),
            },
        )

    @staticmethod
    def _list_value(value: Any) -> list[Any]:
        if isinstance(value, list):
            return value
        if isinstance(value, str):
            try:
                decoded = json.loads(value)
                return decoded if isinstance(decoded, list) else []
            except json.JSONDecodeError:
                return []
        return []

    @staticmethod
    def _int_or_none(value: Any) -> int | None:
        try:
            return int(value) if value is not None else None
        except (TypeError, ValueError):
            return None


class QdrantEvidenceStore:
    """Thin optional adapter around Qdrant's official Python client."""

    def __init__(self, url: str, collection: str, *, api_key: str = "", dimension: int = 1024) -> None:
        try:
            from qdrant_client import QdrantClient
        except ImportError as exc:
            raise RuntimeError(
                "Vector retrieval requires the 'vector' optional dependencies: "
                "pip install -e '.[vector]'"
            ) from exc
        self.client = QdrantClient(url=url, api_key=api_key or None, timeout=3)
        self.collection = collection
        self.dimension = dimension

    def ensure_collection(self) -> None:
        from qdrant_client.models import Distance, VectorParams

        if not self.client.collection_exists(self.collection):
            self.client.create_collection(
                collection_name=self.collection,
                vectors_config=VectorParams(size=self.dimension, distance=Distance.COSINE),
            )

    def ready(self) -> bool:
        try:
            self.client.get_collections()
            return True
        except Exception:
            return False

    @staticmethod
    def point_id(chunk_id: str) -> str:
        return str(uuid5(NAMESPACE_URL, f"geochem:{chunk_id}"))

    def upsert(self, chunks: list[EvidenceChunk], vectors: list[list[float]]) -> None:
        from qdrant_client.models import PointStruct

        self.ensure_collection()
        points = [
            PointStruct(
                id=self.point_id(chunk.chunk_id),
                vector=vector,
                payload={
                    **chunk.model_dump(exclude={"metadata"}),
                    "metadata": chunk.metadata,
                },
            )
            for chunk, vector in zip(chunks, vectors, strict=True)
        ]
        if points:
            self.client.upsert(collection_name=self.collection, points=points, wait=True)

    def delete(self, chunk_ids: list[str]) -> None:
        if not chunk_ids or not self.client.collection_exists(self.collection):
            return
        from qdrant_client.models import PointIdsList

        self.client.delete(
            collection_name=self.collection,
            points_selector=PointIdsList(points=[self.point_id(chunk_id) for chunk_id in chunk_ids]),
            wait=True,
        )

    def list_chunk_ids(self, *, project_id: str, article_id: str) -> list[str]:
        if not self.client.collection_exists(self.collection):
            return []
        from qdrant_client.models import FieldCondition, Filter, MatchValue

        query_filter = Filter(
            must=[
                FieldCondition(key="project_id", match=MatchValue(value=project_id)),
                FieldCondition(key="article_id", match=MatchValue(value=article_id)),
            ]
        )
        result: list[str] = []
        offset = None
        while True:
            points, offset = self.client.scroll(
                collection_name=self.collection,
                scroll_filter=query_filter,
                limit=256,
                offset=offset,
                with_payload=["chunk_id"],
                with_vectors=False,
            )
            result.extend(
                str((point.payload or {}).get("chunk_id") or "")
                for point in points
                if (point.payload or {}).get("chunk_id")
            )
            if offset is None:
                break
        return result

    def search(
        self,
        vector: list[float],
        *,
        project_id: str,
        article_id: str,
        limit: int = 30,
    ) -> list[dict[str, Any]]:
        from qdrant_client.models import FieldCondition, Filter, MatchValue

        self.ensure_collection()
        query_filter = Filter(
            must=[
                FieldCondition(key="project_id", match=MatchValue(value=project_id)),
                FieldCondition(key="article_id", match=MatchValue(value=article_id)),
            ]
        )
        if hasattr(self.client, "query_points"):
            response = self.client.query_points(
                collection_name=self.collection,
                query=vector,
                query_filter=query_filter,
                limit=limit,
                with_payload=True,
            )
            points = response.points
        else:  # pragma: no cover - compatibility with older qdrant-client
            points = self.client.search(
                collection_name=self.collection,
                query_vector=vector,
                query_filter=query_filter,
                limit=limit,
                with_payload=True,
            )
        return [
            {"score": float(point.score), "payload": dict(point.payload or {})}
            for point in points
        ]
