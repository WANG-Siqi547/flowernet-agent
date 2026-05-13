"""
Optional agent-engineering stack for FlowerNet.

This module is intentionally dependency-light. It exposes production-facing
interfaces for Vector DB/RAG, reranking, tools, checkpoints, evaluation, and a
LangGraph-style workflow description while falling back to local memory/file
storage when optional services are unavailable.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional, Tuple
import hashlib
import json
import math
import os
import queue
import re
import threading
import time
import uuid


PROJECT_ROOT = Path(__file__).resolve().parent
DEFAULT_STATE_DIR = Path(os.getenv("FLOWERNET_STATE_DIR", str(PROJECT_ROOT / ".flowernet_state")))


def _ensure_state_dir() -> Path:
    DEFAULT_STATE_DIR.mkdir(parents=True, exist_ok=True)
    return DEFAULT_STATE_DIR


def _tokenize(text: str) -> List[str]:
    return re.findall(r"[\u4e00-\u9fff]{2,}|[A-Za-z][A-Za-z0-9_-]{2,}", str(text or "").lower())


def _embedding(text: str, dim: int = 256) -> List[float]:
    vec = [0.0] * dim
    tokens = _tokenize(text)
    if not tokens:
        return vec
    for tok in tokens:
        digest = hashlib.blake2b(tok.encode("utf-8"), digest_size=8).digest()
        idx = int.from_bytes(digest[:4], "big") % dim
        sign = 1.0 if digest[4] % 2 == 0 else -1.0
        vec[idx] += sign
    norm = math.sqrt(sum(x * x for x in vec)) or 1.0
    return [x / norm for x in vec]


def _cosine(left: List[float], right: List[float]) -> float:
    if not left or not right:
        return 0.0
    return max(0.0, min(1.0, sum(a * b for a, b in zip(left, right))))


def _safe_jsonl_append(path: Path, obj: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(obj, ensure_ascii=False) + "\n")


@dataclass
class VectorRecord:
    id: str
    text: str
    metadata: Dict[str, Any] = field(default_factory=dict)
    embedding: Optional[List[float]] = None

    @classmethod
    def from_rag_result(cls, query: str, result: Dict[str, Any], namespace: str) -> "VectorRecord":
        title = str(result.get("title") or result.get("source_name") or "").strip()
        body = str(result.get("body") or result.get("snippet") or result.get("abstract") or "").strip()
        url = str(result.get("href") or result.get("url") or result.get("link") or "").strip()
        text = "\n".join(part for part in [title, body, url] if part)
        stable = hashlib.sha1(f"{namespace}|{query}|{url}|{title}".encode("utf-8")).hexdigest()
        meta = {
            "namespace": namespace,
            "query": query,
            "title": title,
            "url": url,
            "source": result.get("source") or result.get("source_type") or "",
            "quality_score": float(result.get("quality_score", 0.0) or 0.0),
            "domain_score": float(result.get("domain_score", 0.0) or 0.0),
            "semantic_score": float(result.get("semantic_score", 0.0) or 0.0),
            "created_at": time.time(),
        }
        return cls(id=stable, text=text, metadata=meta)


class RAGReranker:
    """Lightweight reranker used before/after Vector DB retrieval."""

    def __init__(self) -> None:
        self.authority_domains = {
            "doi.org", "arxiv.org", "ieee.org", "acm.org", "springer.com",
            "sciencedirect.com", "nature.com", "science.org", "nih.gov",
            "ncbi.nlm.nih.gov", "edu.cn", "tsinghua.edu.cn", "pku.edu.cn",
            "cambridge.org", "oxfordacademic.com", "jstor.org", "sagepub.com",
        }

    def score(self, query: str, text: str, metadata: Optional[Dict[str, Any]] = None) -> float:
        metadata = metadata or {}
        q_tokens = set(_tokenize(query))
        d_tokens = set(_tokenize(text))
        lexical = len(q_tokens & d_tokens) / max(1, len(q_tokens))
        semantic = _cosine(_embedding(query), _embedding(text))
        url = str(metadata.get("url") or "")
        authority = 0.0
        for domain in self.authority_domains:
            if domain in url:
                authority = 1.0
                break
        prior = max(
            float(metadata.get("quality_score", 0.0) or 0.0),
            float(metadata.get("domain_score", 0.0) or 0.0),
            float(metadata.get("semantic_score", 0.0) or 0.0),
        )
        return round(0.42 * lexical + 0.34 * semantic + 0.16 * authority + 0.08 * prior, 4)

    def rerank(self, query: str, items: List[Dict[str, Any]], top_k: int = 8) -> List[Dict[str, Any]]:
        ranked: List[Dict[str, Any]] = []
        for item in items or []:
            text = "\n".join(
                str(item.get(key) or "")
                for key in ("title", "body", "snippet", "abstract", "url", "href", "link")
            )
            meta = dict(item)
            score = self.score(query, text, meta)
            enriched = dict(item)
            enriched["rerank_score"] = score
            ranked.append(enriched)
        ranked.sort(key=lambda x: float(x.get("rerank_score", 0.0) or 0.0), reverse=True)
        return ranked[:top_k]


class VectorStore:
    """Vector DB adapter with Qdrant/Chroma optional backends and file fallback."""

    def __init__(self, backend: Optional[str] = None, collection: str = "flowernet_rag", dim: int = 256):
        self.backend = (backend or os.getenv("FLOWERNET_VECTOR_BACKEND", "auto")).lower()
        self.collection_name = collection
        self.dim = dim
        self.reranker = RAGReranker()
        self._records: Dict[str, VectorRecord] = {}
        self._lock = threading.Lock()
        self._client = None
        self._collection = None
        self._path = _ensure_state_dir() / f"{collection}.jsonl"
        self.active_backend = "memory"
        self._load_file_records()
        self._init_optional_backend()

    def _load_file_records(self) -> None:
        if not self._path.exists():
            return
        try:
            with self._path.open("r", encoding="utf-8") as f:
                for line in f:
                    if not line.strip():
                        continue
                    raw = json.loads(line)
                    rec = VectorRecord(
                        id=str(raw.get("id") or ""),
                        text=str(raw.get("text") or ""),
                        metadata=dict(raw.get("metadata") or {}),
                        embedding=raw.get("embedding"),
                    )
                    if rec.id and rec.text:
                        self._records[rec.id] = rec
        except Exception:
            self._records = {}

    def _init_optional_backend(self) -> None:
        if self.backend in {"qdrant", "auto"} and os.getenv("QDRANT_URL"):
            try:
                from qdrant_client import QdrantClient
                from qdrant_client.http import models

                self._client = QdrantClient(
                    url=os.getenv("QDRANT_URL"),
                    api_key=os.getenv("QDRANT_API_KEY") or None,
                    timeout=float(os.getenv("QDRANT_TIMEOUT", "10")),
                )
                collections = self._client.get_collections().collections
                names = {c.name for c in collections}
                if self.collection_name not in names:
                    self._client.create_collection(
                        collection_name=self.collection_name,
                        vectors_config=models.VectorParams(size=self.dim, distance=models.Distance.COSINE),
                    )
                self.active_backend = "qdrant"
                return
            except Exception:
                self._client = None
        if self.backend in {"chroma", "auto"}:
            try:
                import chromadb

                persist_dir = os.getenv("CHROMA_PERSIST_DIR", str(_ensure_state_dir() / "chroma"))
                self._client = chromadb.PersistentClient(path=persist_dir)
                self._collection = self._client.get_or_create_collection(self.collection_name)
                self.active_backend = "chroma"
            except Exception:
                self._client = None
                self._collection = None

    def capabilities(self) -> Dict[str, Any]:
        return {
            "active_backend": self.active_backend,
            "configured_backend": self.backend,
            "collection": self.collection_name,
            "records": len(self._records),
            "qdrant_configured": bool(os.getenv("QDRANT_URL")),
            "chroma_persist_dir": os.getenv("CHROMA_PERSIST_DIR", str(_ensure_state_dir() / "chroma")),
        }

    def upsert(self, records: Iterable[VectorRecord]) -> int:
        prepared: List[VectorRecord] = []
        for rec in records:
            if not rec.id or not rec.text:
                continue
            if rec.embedding is None:
                rec.embedding = _embedding(rec.text, self.dim)
            prepared.append(rec)
        if not prepared:
            return 0
        with self._lock:
            for rec in prepared:
                self._records[rec.id] = rec
                _safe_jsonl_append(
                    self._path,
                    {
                        "id": rec.id,
                        "text": rec.text,
                        "metadata": rec.metadata,
                        "embedding": rec.embedding,
                    },
                )
        if self.active_backend == "chroma" and self._collection is not None:
            try:
                self._collection.upsert(
                    ids=[rec.id for rec in prepared],
                    embeddings=[rec.embedding for rec in prepared],
                    documents=[rec.text for rec in prepared],
                    metadatas=[rec.metadata for rec in prepared],
                )
            except Exception:
                pass
        elif self.active_backend == "qdrant" and self._client is not None:
            try:
                from qdrant_client.http import models

                self._client.upsert(
                    collection_name=self.collection_name,
                    points=[
                        models.PointStruct(
                            id=rec.id,
                            vector=rec.embedding,
                            payload={"text": rec.text, **rec.metadata},
                        )
                        for rec in prepared
                    ],
                )
            except Exception:
                pass
        return len(prepared)

    def index_rag_results(self, query: str, results: List[Dict[str, Any]], namespace: str = "global") -> int:
        records = [VectorRecord.from_rag_result(query, item, namespace) for item in results or []]
        return self.upsert(records)

    def query(self, query: str, top_k: int = 8, namespace: Optional[str] = None) -> List[Dict[str, Any]]:
        top_k = max(1, min(int(top_k or 8), 50))
        query_embedding = _embedding(query, self.dim)
        candidates: List[Dict[str, Any]] = []
        if self.active_backend == "chroma" and self._collection is not None:
            try:
                raw = self._collection.query(query_embeddings=[query_embedding], n_results=top_k * 2)
                for idx, doc in enumerate((raw.get("documents") or [[]])[0]):
                    meta = ((raw.get("metadatas") or [[]])[0] or [{}])[idx] or {}
                    if namespace and meta.get("namespace") != namespace:
                        continue
                    candidates.append({"text": doc, "metadata": meta, "vector_score": 1.0 - float(((raw.get("distances") or [[]])[0] or [1])[idx] or 1)})
            except Exception:
                candidates = []
        elif self.active_backend == "qdrant" and self._client is not None:
            try:
                hits = self._client.search(
                    collection_name=self.collection_name,
                    query_vector=query_embedding,
                    limit=top_k * 2,
                    with_payload=True,
                )
                for hit in hits:
                    payload = dict(hit.payload or {})
                    if namespace and payload.get("namespace") != namespace:
                        continue
                    text = str(payload.pop("text", "") or "")
                    candidates.append({"text": text, "metadata": payload, "vector_score": float(hit.score or 0.0)})
            except Exception:
                candidates = []

        if not candidates:
            with self._lock:
                records = list(self._records.values())
            for rec in records:
                if namespace and rec.metadata.get("namespace") != namespace:
                    continue
                candidates.append(
                    {
                        "id": rec.id,
                        "text": rec.text,
                        "metadata": rec.metadata,
                        "vector_score": _cosine(query_embedding, rec.embedding or _embedding(rec.text, self.dim)),
                    }
                )

        ranked: List[Dict[str, Any]] = []
        for item in candidates:
            meta = item.get("metadata", {}) or {}
            text = str(item.get("text") or "")
            combined = 0.58 * float(item.get("vector_score", 0.0) or 0.0) + 0.42 * self.reranker.score(query, text, meta)
            ranked.append(
                {
                    "id": item.get("id") or hashlib.sha1(text.encode("utf-8")).hexdigest(),
                    "text": text,
                    "metadata": meta,
                    "vector_score": round(float(item.get("vector_score", 0.0) or 0.0), 4),
                    "rerank_score": round(combined, 4),
                }
            )
        ranked.sort(key=lambda x: float(x.get("rerank_score", 0.0) or 0.0), reverse=True)
        return ranked[:top_k]


class CheckpointStore:
    """Redis checkpoint store with local JSON fallback."""

    def __init__(self) -> None:
        self._redis = None
        self._file = _ensure_state_dir() / "checkpoints.json"
        self._lock = threading.Lock()
        self.active_backend = "file"
        redis_url = os.getenv("REDIS_URL", "").strip()
        if redis_url:
            try:
                import redis

                self._redis = redis.Redis.from_url(redis_url, decode_responses=True)
                self._redis.ping()
                self.active_backend = "redis"
            except Exception:
                self._redis = None

    def _read_file(self) -> Dict[str, Any]:
        if not self._file.exists():
            return {}
        try:
            return json.loads(self._file.read_text(encoding="utf-8"))
        except Exception:
            return {}

    def set(self, key: str, value: Dict[str, Any], ttl_seconds: Optional[int] = None) -> None:
        payload = json.dumps(value, ensure_ascii=False, default=str)
        if self._redis is not None:
            try:
                self._redis.set(key, payload, ex=ttl_seconds)
                return
            except Exception:
                pass
        with self._lock:
            data = self._read_file()
            data[key] = {"value": value, "updated_at": time.time(), "ttl_seconds": ttl_seconds}
            self._file.write_text(json.dumps(data, ensure_ascii=False, indent=2, default=str), encoding="utf-8")

    def get(self, key: str) -> Optional[Dict[str, Any]]:
        if self._redis is not None:
            try:
                raw = self._redis.get(key)
                return json.loads(raw) if raw else None
            except Exception:
                pass
        data = self._read_file()
        item = data.get(key)
        if not item:
            return None
        ttl = item.get("ttl_seconds")
        if ttl and time.time() - float(item.get("updated_at", 0.0) or 0.0) > float(ttl):
            return None
        return item.get("value") if isinstance(item, dict) else item

    def capabilities(self) -> Dict[str, Any]:
        return {"active_backend": self.active_backend, "redis_configured": bool(os.getenv("REDIS_URL"))}


class TaskQueue:
    """Redis queue facade with in-process fallback."""

    def __init__(self, name: str = "flowernet:tasks") -> None:
        self.name = name
        self._queue: "queue.Queue[Dict[str, Any]]" = queue.Queue()
        self._redis = None
        self.active_backend = "memory"
        redis_url = os.getenv("REDIS_URL", "").strip()
        if redis_url:
            try:
                import redis

                self._redis = redis.Redis.from_url(redis_url, decode_responses=True)
                self._redis.ping()
                self.active_backend = "redis"
            except Exception:
                self._redis = None

    def put(self, item: Dict[str, Any]) -> None:
        if self._redis is not None:
            try:
                self._redis.rpush(self.name, json.dumps(item, ensure_ascii=False, default=str))
                return
            except Exception:
                pass
        self._queue.put(item)

    def get(self, timeout: float = 1.0) -> Optional[Dict[str, Any]]:
        if self._redis is not None:
            try:
                item = self._redis.blpop(self.name, timeout=max(1, int(timeout or 1)))
                if item:
                    return json.loads(item[1])
            except Exception:
                pass
        try:
            return self._queue.get(timeout=timeout)
        except queue.Empty:
            return None

    def size(self) -> int:
        if self._redis is not None:
            try:
                return int(self._redis.llen(self.name))
            except Exception:
                pass
        return self._queue.qsize()

    def capabilities(self) -> Dict[str, Any]:
        return {"active_backend": self.active_backend, "name": self.name, "size": self.size()}


class EvaluationStore:
    """Append-only LLM evaluation store for automatic regression tracking."""

    def __init__(self) -> None:
        self._path = _ensure_state_dir() / "llm_evaluations.jsonl"
        self._lock = threading.Lock()

    def record(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        item = dict(payload or {})
        item.setdefault("id", str(uuid.uuid4()))
        item.setdefault("created_at", time.time())
        with self._lock:
            _safe_jsonl_append(self._path, item)
        return item

    def recent(self, limit: int = 50) -> List[Dict[str, Any]]:
        if not self._path.exists():
            return []
        rows: List[Dict[str, Any]] = []
        try:
            with self._path.open("r", encoding="utf-8") as f:
                for line in f:
                    if line.strip():
                        rows.append(json.loads(line))
        except Exception:
            return []
        return rows[-max(1, min(limit, 500)):]

    def summary(self) -> Dict[str, Any]:
        rows = self.recent(500)
        if not rows:
            return {"count": 0, "quality_score_avg": 0.0, "pass_rate": 0.0, "recent": []}
        scores = [float(row.get("quality_score_avg", row.get("quality_score", 0.0)) or 0.0) for row in rows]
        passed = [bool(row.get("success", row.get("passed", False))) for row in rows]
        controller = [float(row.get("controller_trigger_rate", row.get("controller_triggered_subsections", 0.0)) or 0.0) for row in rows]
        return {
            "count": len(rows),
            "quality_score_avg": round(sum(scores) / max(1, len(scores)), 4),
            "pass_rate": round(sum(1 for x in passed if x) / max(1, len(passed)), 4),
            "controller_signal_avg": round(sum(controller) / max(1, len(controller)), 4),
            "recent": rows[-10:],
        }


class ToolRegistry:
    """Small tool-use registry used by HTTP endpoints and the MCP adapter."""

    def __init__(self, vector_store: VectorStore, eval_store: EvaluationStore, checkpoint_store: CheckpointStore):
        self.vector_store = vector_store
        self.eval_store = eval_store
        self.checkpoint_store = checkpoint_store
        self._tools: Dict[str, Callable[[Dict[str, Any]], Dict[str, Any]]] = {}
        self._descriptions: Dict[str, str] = {}
        self.register("rag_query", self._tool_rag_query, "Query FlowerNet Vector DB/RAG memory.")
        self.register("rag_index", self._tool_rag_index, "Index RAG snippets into Vector DB.")
        self.register("eval_record", self._tool_eval_record, "Record an LLM evaluation run.")
        self.register("eval_summary", self._tool_eval_summary, "Return LLM evaluation dashboard summary.")
        self.register("checkpoint_get", self._tool_checkpoint_get, "Get task/document checkpoint.")
        self.register("checkpoint_set", self._tool_checkpoint_set, "Set task/document checkpoint.")

    def register(self, name: str, fn: Callable[[Dict[str, Any]], Dict[str, Any]], description: str = "") -> None:
        self._tools[name] = fn
        self._descriptions[name] = description

    def list_tools(self) -> List[Dict[str, str]]:
        return [{"name": name, "description": self._descriptions.get(name, "")} for name in sorted(self._tools)]

    def call(self, name: str, arguments: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        if name not in self._tools:
            return {"success": False, "error": f"unknown_tool:{name}"}
        try:
            return self._tools[name](arguments or {})
        except Exception as exc:
            return {"success": False, "error": f"{type(exc).__name__}: {str(exc)[:300]}"}

    def _tool_rag_query(self, args: Dict[str, Any]) -> Dict[str, Any]:
        query = str(args.get("query") or "")
        namespace = args.get("namespace")
        results = self.vector_store.query(query, top_k=int(args.get("top_k", 8) or 8), namespace=namespace)
        return {"success": True, "results": results, "backend": self.vector_store.active_backend}

    def _tool_rag_index(self, args: Dict[str, Any]) -> Dict[str, Any]:
        query = str(args.get("query") or "")
        namespace = str(args.get("namespace") or "global")
        results = args.get("results") if isinstance(args.get("results"), list) else []
        count = self.vector_store.index_rag_results(query, results, namespace=namespace)
        return {"success": True, "indexed": count, "backend": self.vector_store.active_backend}

    def _tool_eval_record(self, args: Dict[str, Any]) -> Dict[str, Any]:
        return {"success": True, "record": self.eval_store.record(args)}

    def _tool_eval_summary(self, args: Dict[str, Any]) -> Dict[str, Any]:
        return {"success": True, "summary": self.eval_store.summary()}

    def _tool_checkpoint_get(self, args: Dict[str, Any]) -> Dict[str, Any]:
        key = str(args.get("key") or "")
        return {"success": True, "key": key, "value": self.checkpoint_store.get(key)}

    def _tool_checkpoint_set(self, args: Dict[str, Any]) -> Dict[str, Any]:
        key = str(args.get("key") or "")
        value = args.get("value") if isinstance(args.get("value"), dict) else {"value": args.get("value")}
        self.checkpoint_store.set(key, value, ttl_seconds=args.get("ttl_seconds"))
        return {"success": True, "key": key}


class LangGraphAdapter:
    """Optional LangGraph builder; returns a graph spec when package is absent."""

    def __init__(self) -> None:
        self.langgraph_available = False
        try:
            import langgraph  # noqa: F401

            self.langgraph_available = True
        except Exception:
            self.langgraph_available = False

    def graph_spec(self) -> Dict[str, Any]:
        return {
            "available": self.langgraph_available,
            "mode": "langgraph" if self.langgraph_available else "spec_fallback",
            "nodes": [
                {"id": "outliner", "role": "structure_planning"},
                {"id": "generator", "role": "subsection_drafting"},
                {"id": "verifier", "role": "quality_evaluation"},
                {"id": "controller", "role": "targeted_repair"},
                {"id": "rag_tools", "role": "evidence_retrieval"},
                {"id": "exporter", "role": "docx_pdf_export"},
            ],
            "edges": [
                ["outliner", "generator"],
                ["generator", "verifier"],
                ["verifier", "controller", "if_failed"],
                ["controller", "generator"],
                ["verifier", "exporter", "if_passed_all"],
                ["rag_tools", "generator", "tool_context"],
            ],
            "state_keys": [
                "document_id", "topic", "structure", "content_prompts", "history",
                "rag_context", "verifier_snapshots", "controller_events", "exports",
            ],
        }


_VECTOR_STORE: Optional[VectorStore] = None
_CHECKPOINT_STORE: Optional[CheckpointStore] = None
_TASK_QUEUE: Optional[TaskQueue] = None
_EVAL_STORE: Optional[EvaluationStore] = None
_TOOL_REGISTRY: Optional[ToolRegistry] = None
_LANGGRAPH_ADAPTER: Optional[LangGraphAdapter] = None


def get_vector_store() -> VectorStore:
    global _VECTOR_STORE
    if _VECTOR_STORE is None:
        _VECTOR_STORE = VectorStore()
    return _VECTOR_STORE


def get_checkpoint_store() -> CheckpointStore:
    global _CHECKPOINT_STORE
    if _CHECKPOINT_STORE is None:
        _CHECKPOINT_STORE = CheckpointStore()
    return _CHECKPOINT_STORE


def get_task_queue(name: str = "flowernet:tasks") -> TaskQueue:
    global _TASK_QUEUE
    if _TASK_QUEUE is None:
        _TASK_QUEUE = TaskQueue(name=name)
    return _TASK_QUEUE


def get_eval_store() -> EvaluationStore:
    global _EVAL_STORE
    if _EVAL_STORE is None:
        _EVAL_STORE = EvaluationStore()
    return _EVAL_STORE


def get_tool_registry() -> ToolRegistry:
    global _TOOL_REGISTRY
    if _TOOL_REGISTRY is None:
        _TOOL_REGISTRY = ToolRegistry(get_vector_store(), get_eval_store(), get_checkpoint_store())
    return _TOOL_REGISTRY


def get_langgraph_adapter() -> LangGraphAdapter:
    global _LANGGRAPH_ADAPTER
    if _LANGGRAPH_ADAPTER is None:
        _LANGGRAPH_ADAPTER = LangGraphAdapter()
    return _LANGGRAPH_ADAPTER


def agent_stack_capabilities() -> Dict[str, Any]:
    return {
        "vector_store": get_vector_store().capabilities(),
        "checkpoint_store": get_checkpoint_store().capabilities(),
        "task_queue": get_task_queue().capabilities(),
        "langgraph": get_langgraph_adapter().graph_spec(),
        "tools": get_tool_registry().list_tools(),
        "evaluation": get_eval_store().summary(),
    }
