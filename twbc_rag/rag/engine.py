"""BM25 + dense + article-number lookup, fused with RRF.

Dense vectors use intfloat/multilingual-e5-small, not BGE-M3. This VM has
4 CPUs and about 5 GB free. BGE-M3's weights alone are ~2.3 GB and the
optional reranker is another large model; together they do not leave room
for Postgres, Qdrant, and the API. E5-small is multilingual, 384-d, and
needs the ``query:`` / ``passage:`` prefixes.

The optional reranker is BAAI/bge-reranker-v2-m3 and loads only when a
request asks for it.
"""

from __future__ import annotations

import logging
import os
import threading
import time
from dataclasses import dataclass, field

from qdrant_client import QdrantClient
from qdrant_client.http.models import Distance, PointStruct, VectorParams
from rank_bm25 import BM25Okapi

from twbc_rag.db.session import session_scope
from twbc_rag.rag.chunks import Chunk, load_chunks
from twbc_rag.rag.fusion import reciprocal_rank_fusion
from twbc_rag.rag.lookup import lookup_chunk_ids
from twbc_rag.rag.tokenize import tokenize

logger = logging.getLogger(__name__)

EMBED_MODEL = os.environ.get("TWBC_EMBED_MODEL", "intfloat/multilingual-e5-small")
RERANK_MODEL = os.environ.get("TWBC_RERANK_MODEL", "BAAI/bge-reranker-v2-m3")
COLLECTION = "twbc_e5_small_clauses"
_PASSAGE_CHARS = 700


@dataclass
class _ClauseRow:
    clause_id: int
    chunk_id: str
    clause_path: str
    text: str


@dataclass
class Hit:
    chunk: Chunk
    score: float
    channels: list[str]
    expansion: bool = False
    matched_clause_paths: list[str] = field(default_factory=list)


@dataclass
class SearchResult:
    hits: list[Hit]
    expansions: list[Hit]
    timings_ms: dict[str, float]
    rerank_warning: str | None = None


class Engine:
    def __init__(self) -> None:
        self.ready = False
        self.chunks: dict[str, Chunk] = {}
        self._rows: list[_ClauseRow] = []
        self._bm25: BM25Okapi | None = None
        self._qdrant: QdrantClient | None = None
        self._embedder: object | None = None
        self._reranker: object | None = None
        self._lock = threading.Lock()

    def ensure_ready(self) -> None:
        with self._lock:
            if self.ready:
                return
            started = time.perf_counter()
            with session_scope() as session:
                loaded = load_chunks(session)
            self.chunks = {chunk.chunk_id: chunk for chunk in loaded}
            self._rows = [
                _ClauseRow(child.clause_id, chunk.chunk_id, child.clause_path, child.text)
                for chunk in loaded
                for child in chunk.clauses
                if child.text.strip()
            ]
            logger.info("Indexed %s articles / %s clauses in memory", len(loaded), len(self._rows))
            self._build_bm25()
            self._ensure_qdrant()
            self.ready = True
            logger.info("Retrieval index ready in %.1fs", time.perf_counter() - started)

    def search(self, question: str, *, mode: str = "hybrid", top_k: int = 5) -> SearchResult:
        self.ensure_ready()
        top_k = max(1, min(top_k, 10))
        timings: dict[str, float] = {}
        rerank_warning = None
        use_lookup = mode in {"hybrid", "hybrid_rerank"}
        use_bm25 = mode in {"bm25", "hybrid", "hybrid_rerank"}
        use_dense = mode in {"dense", "hybrid", "hybrid_rerank"}

        lookup_ids: list[str] = []
        if use_lookup:
            started = time.perf_counter()
            lookup_ids = lookup_chunk_ids(question, list(self.chunks.values()))
            timings["lookup"] = _ms(started)

        bm25_ids: list[str] = []
        bm25_paths: dict[str, list[str]] = {}
        if use_bm25:
            started = time.perf_counter()
            bm25_ids, bm25_paths = self._bm25_rank(question)
            timings["bm25"] = _ms(started)

        dense_ids: list[str] = []
        dense_paths: dict[str, list[str]] = {}
        if use_dense:
            started = time.perf_counter()
            dense_ids, dense_paths = self._dense_rank(question)
            timings["dense"] = _ms(started)

        started = time.perf_counter()
        rankings = [ids for ids in (lookup_ids, bm25_ids, dense_ids) if ids]
        if not rankings:
            fused: list[tuple[str, float]] = []
        elif len(rankings) == 1 and not use_lookup:
            fused = [(doc_id, 1.0 / rank) for rank, doc_id in enumerate(rankings[0], start=1)]
        else:
            fused = reciprocal_rank_fusion(rankings)
        timings["fusion"] = _ms(started)

        channels = _channels(lookup_ids, bm25_ids, dense_ids)
        paths = _merge_paths(bm25_paths, dense_paths)
        ordered = [doc_id for doc_id, _score in fused]
        scores = dict(fused)

        if mode == "hybrid_rerank" and ordered:
            started = time.perf_counter()
            try:
                ordered = self._rerank(question, ordered[:20])
                scores = {
                    doc_id: float(len(ordered) - index) for index, doc_id in enumerate(ordered)
                }
            except Exception as exc:
                rerank_warning = f"Reranker skipped: {exc}"
                logger.warning(rerank_warning)
            timings["rerank"] = _ms(started)

        hits = [
            Hit(
                chunk=self.chunks[doc_id],
                score=scores.get(doc_id, 0.0),
                channels=channels.get(doc_id, []),
                matched_clause_paths=paths.get(doc_id, []),
            )
            for doc_id in ordered[:top_k]
            if doc_id in self.chunks
        ]
        expansions = self._expand(hits)
        return SearchResult(
            hits=hits, expansions=expansions, timings_ms=timings, rerank_warning=rerank_warning
        )

    def _build_bm25(self) -> None:
        corpus = [tokenize(row.text) or ["空"] for row in self._rows]
        self._bm25 = BM25Okapi(corpus) if corpus else None

    def _bm25_rank(self, question: str) -> tuple[list[str], dict[str, list[str]]]:
        if self._bm25 is None or not self._rows:
            return [], {}
        tokens = tokenize(question)
        if not tokens:
            return [], {}
        scores = self._bm25.get_scores(tokens)
        best: dict[str, float] = {}
        paths: dict[str, list[tuple[float, str]]] = {}
        for score, row in zip(scores, self._rows, strict=True):
            if score <= 0:
                continue
            best[row.chunk_id] = max(best.get(row.chunk_id, 0.0), float(score))
            paths.setdefault(row.chunk_id, []).append((float(score), row.clause_path))
        ordered = sorted(best, key=best.get, reverse=True)[:40]
        return ordered, {key: _top_paths(value) for key, value in paths.items()}

    def _ensure_qdrant(self) -> None:
        url = os.environ.get("QDRANT_URL", "http://127.0.0.1:6333")
        self._qdrant = QdrantClient(url=url, timeout=60, check_compatibility=False)
        embedder = self._embedder_model()
        if hasattr(embedder, "get_embedding_dimension"):
            size = embedder.get_embedding_dimension()
        else:
            size = embedder.get_sentence_embedding_dimension()
        existing = {item.name for item in self._qdrant.get_collections().collections}
        count = 0
        if COLLECTION in existing:
            count = self._qdrant.count(COLLECTION, exact=True).count
        if count != len(self._rows):
            if COLLECTION in existing:
                self._qdrant.delete_collection(COLLECTION)
            self._qdrant.create_collection(
                COLLECTION,
                vectors_config=VectorParams(size=size, distance=Distance.COSINE),
            )
            self._upsert_vectors(embedder)
        logger.info("Qdrant collection %s has %s vectors", COLLECTION, len(self._rows))

    def _embedder_model(self):
        if self._embedder is None:
            os.environ.setdefault("CUDA_VISIBLE_DEVICES", "")
            from sentence_transformers import SentenceTransformer

            logger.info("Loading embedding model %s", EMBED_MODEL)
            self._embedder = SentenceTransformer(EMBED_MODEL, device="cpu")
        return self._embedder

    def _upsert_vectors(self, embedder) -> None:
        assert self._qdrant is not None
        batch = 64
        for start in range(0, len(self._rows), batch):
            rows = self._rows[start : start + batch]
            texts = [
                "passage: "
                + f"{self.chunks[row.chunk_id].law_name} {self.chunks[row.chunk_id].article_no} "
                + row.text[:_PASSAGE_CHARS]
                for row in rows
            ]
            vectors = embedder.encode(texts, normalize_embeddings=True, show_progress_bar=False)
            points = [
                PointStruct(
                    id=row.clause_id,
                    vector=vector.tolist(),
                    payload={"chunk_id": row.chunk_id, "clause_path": row.clause_path},
                )
                for row, vector in zip(rows, vectors, strict=True)
            ]
            self._qdrant.upsert(COLLECTION, points=points)
            logger.info(
                "Upserted embeddings %s/%s", min(start + batch, len(self._rows)), len(self._rows)
            )

    def _dense_rank(self, question: str) -> tuple[list[str], dict[str, list[str]]]:
        if self._qdrant is None or not self._rows:
            return [], {}
        embedder = self._embedder_model()
        vector = embedder.encode(f"query: {question}", normalize_embeddings=True)
        response = self._qdrant.query_points(
            COLLECTION,
            query=vector.tolist(),
            limit=40,
            with_payload=True,
        )
        best: dict[str, float] = {}
        paths: dict[str, list[tuple[float, str]]] = {}
        for point in response.points:
            payload = point.payload or {}
            chunk_id = str(payload.get("chunk_id") or "")
            if not chunk_id:
                continue
            score = float(point.score or 0)
            best[chunk_id] = max(best.get(chunk_id, 0.0), score)
            paths.setdefault(chunk_id, []).append((score, str(payload.get("clause_path") or "")))
        ordered = sorted(best, key=best.get, reverse=True)
        return ordered, {key: _top_paths(value) for key, value in paths.items()}

    def _rerank(self, question: str, chunk_ids: list[str]) -> list[str]:
        if self._reranker is None:
            from sentence_transformers import CrossEncoder

            logger.info("Loading reranker %s", RERANK_MODEL)
            self._reranker = CrossEncoder(RERANK_MODEL, device="cpu")
        pairs = [(question, self.chunks[chunk_id].full_text[:1200]) for chunk_id in chunk_ids]
        scores = self._reranker.predict(pairs)
        ranked = sorted(
            zip(chunk_ids, scores, strict=True), key=lambda item: float(item[1]), reverse=True
        )
        return [chunk_id for chunk_id, _score in ranked]

    def _expand(self, hits: list[Hit]) -> list[Hit]:
        seen = {hit.chunk.chunk_id for hit in hits}
        expansions: list[Hit] = []
        for hit in hits[:3]:
            for target in hit.chunk.out_chunk_ids:
                if target in seen or target not in self.chunks:
                    continue
                seen.add(target)
                expansions.append(
                    Hit(
                        chunk=self.chunks[target],
                        score=0.0,
                        channels=["cross_ref"],
                        expansion=True,
                    )
                )
                if len(expansions) >= 4:
                    return expansions
        return expansions


def _ms(started: float) -> float:
    return round((time.perf_counter() - started) * 1000, 1)


def _top_paths(scored: list[tuple[float, str]]) -> list[str]:
    scored.sort(key=lambda item: item[0], reverse=True)
    paths: list[str] = []
    for _score, path in scored:
        if path and path not in paths:
            paths.append(path)
        if len(paths) >= 3:
            break
    return paths


def _channels(
    lookup_ids: list[str], bm25_ids: list[str], dense_ids: list[str]
) -> dict[str, list[str]]:
    found: dict[str, list[str]] = {}
    for doc_id in lookup_ids:
        found.setdefault(doc_id, []).append("lookup")
    for doc_id in bm25_ids:
        found.setdefault(doc_id, []).append("bm25")
    for doc_id in dense_ids:
        found.setdefault(doc_id, []).append("dense")
    return found


def _merge_paths(*maps: dict[str, list[str]]) -> dict[str, list[str]]:
    merged: dict[str, list[str]] = {}
    for item in maps:
        for key, paths in item.items():
            bucket = merged.setdefault(key, [])
            for path in paths:
                if path not in bucket:
                    bucket.append(path)
    return merged


_ENGINE: Engine | None = None


def get_engine() -> Engine:
    global _ENGINE
    if _ENGINE is None:
        _ENGINE = Engine()
    return _ENGINE
