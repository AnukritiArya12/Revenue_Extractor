"""Persistent semantic retrieval over ingested report chunks using ChromaDB."""
import hashlib
import json
import logging
import os
from pathlib import Path

from sentence_transformers import SentenceTransformer
import chromadb

from .bm25_search import _name_key, _report_key

logger = logging.getLogger("extractor")
PROJECT_ROOT = Path(__file__).resolve().parents[2]
CHUNKS_PATH = PROJECT_ROOT / "data" / "processed" / "chunks.jsonl"
CHROMA_PATH = PROJECT_ROOT / "data" / "processed" / "chroma_db"
MODEL_NAME = "all-MiniLM-L6-v2"
COLLECTION_NAME = "annual_report_chunks_minilm_l6_v2"
EMBED_BATCH_SIZE = 128
EXISTING_PAGE_SIZE = 5000


class SemanticSearch:
    """Persist chunk embeddings and use Chroma cosine similarity for retrieval.

    Stable chunk IDs and content hashes let repeated ingestion reuse unchanged
    vectors. Dependency, model, and storage errors are surfaced to the caller.
    """

    def __init__(self, chunks_path=str(CHUNKS_PATH), persist_path=str(CHROMA_PATH),
                 model_name=MODEL_NAME):
        self.chunks_path = str(chunks_path)
        self.persist_path = str(persist_path)
        self.model_name = model_name
        try:
            self.model = SentenceTransformer(model_name)
            self.client = chromadb.PersistentClient(path=self.persist_path)
            self.collection = self.client.get_or_create_collection(
                name=COLLECTION_NAME,
                metadata={"hnsw:space": "cosine", "embedding_model": model_name},
                embedding_function=None,
            )
        except Exception as exc:
            raise RuntimeError(
                "Could not initialize persistent semantic search. Check ChromaDB, "
                f"sentence-transformers, and model availability ({model_name})."
            ) from exc
        self.chunks = []
        self._chunks_mtime = None
        self.reload()

    @staticmethod
    def _hash(text):
        return hashlib.sha256(text.encode("utf-8")).hexdigest()

    @staticmethod
    def _metadata(chunk, digest, model_name):
        return {
            "chunk_id": str(chunk["chunk_id"]),
            "company": str(chunk.get("company") or ""),
            "year": str(chunk.get("year") or ""),
            "company_key": _name_key(chunk.get("company")),
            "report_key": _report_key(chunk.get("year")),
            "page": int(chunk.get("page") or 0),
            "section_type": str(chunk.get("section_type") or "other"),
            "chunk_type": str(chunk.get("chunk_type") or "text"),
            "content_sha256": digest,
            "embedding_model": model_name,
            "source_file": str(chunk.get("source_file") or ""),
            "source_sha256": str(chunk.get("source_sha256") or ""),
            "company_source": str(chunk.get("company_source") or ""),
            "report_year_source": str(chunk.get("report_year_source") or ""),
        }

    def _load_chunks(self):
        if not os.path.exists(self.chunks_path):
            return []
        chunks = []
        with open(self.chunks_path, encoding="utf-8") as source:
            for line in source:
                if line.strip():
                    chunks.append(json.loads(line))
        return chunks

    def reload(self, prune=False):
        """Load JSONL and index missing/changed chunks without duplicating IDs."""
        self.chunks = self._load_chunks()
        self._chunks_mtime = (
            os.path.getmtime(self.chunks_path) if os.path.exists(self.chunks_path) else None
        )
        self.index_chunks(self.chunks, prune=prune)

    def index_chunks(self, chunks, prune=False):
        unique = {}
        for chunk in chunks:
            text = str(chunk.get("text") or "").strip()
            chunk_id = str(chunk.get("chunk_id") or "").strip()
            if text and chunk_id:
                item = dict(chunk)
                item["text"] = text
                unique[chunk_id] = item

        existing_ids = []
        existing_hashes = {}
        offset = 0
        while True:
            existing = self.collection.get(
                limit=EXISTING_PAGE_SIZE,
                offset=offset,
                include=["metadatas"],
            )
            ids = existing["ids"]
            if not ids:
                break
            existing_ids.extend(ids)
            existing_hashes.update({
                chunk_id: metadata.get("content_sha256")
                for chunk_id, metadata in zip(ids, existing["metadatas"])
            })
            offset += len(ids)
            if len(ids) < EXISTING_PAGE_SIZE:
                break
        changed = []
        for chunk_id, chunk in unique.items():
            digest = self._hash(chunk["text"])
            if existing_hashes.get(chunk_id) != digest:
                changed.append((chunk_id, chunk, digest))

        embedded = 0
        for start in range(0, len(changed), EMBED_BATCH_SIZE):
            batch = changed[start:start + EMBED_BATCH_SIZE]
            texts = [entry[1]["text"] for entry in batch]
            vectors = self.model.encode(
                texts,
                normalize_embeddings=True,
                show_progress_bar=False,
                batch_size=EMBED_BATCH_SIZE,
            )
            self.collection.upsert(
                ids=[entry[0] for entry in batch],
                embeddings=vectors.tolist(),
                documents=texts,
                metadatas=[
                    self._metadata(entry[1], entry[2], self.model_name) for entry in batch
                ],
            )
            embedded += len(batch)

        if prune:
            current_ids = set(unique)
            stale = [chunk_id for chunk_id in existing_ids if chunk_id not in current_ids]
            for start in range(0, len(stale), EMBED_BATCH_SIZE):
                self.collection.delete(ids=stale[start:start + EMBED_BATCH_SIZE])

        logger.info(
            "Chroma index synced: %d chunks, %d newly embedded, %d stored",
            len(unique), embedded, self.collection.count(),
        )
        return embedded

    def search(self, query, top_k=5, company=None, year=None):
        """Embed the query and retrieve nearest persisted report chunks."""
        if os.path.exists(self.chunks_path):
            mtime = os.path.getmtime(self.chunks_path)
            if mtime != self._chunks_mtime:
                self.reload()
        if not query.strip() or top_k <= 0:
            return []
        count = self.collection.count()
        if not count:
            raise RuntimeError("Semantic index is empty; run ingestion to index existing chunks.")

        filters = []
        if company:
            filters.append({"company_key": _name_key(company)})
        if year:
            filters.append({"report_key": _report_key(year)})
        where = {"$and": filters} if len(filters) > 1 else filters[0] if filters else None

        vector = self.model.encode([query], normalize_embeddings=True, show_progress_bar=False)[0]
        result = self.collection.query(
            query_embeddings=[vector.tolist()],
            n_results=min(top_k, count),
            where=where,
            include=["documents", "metadatas", "distances"],
        )
        hits = []
        for document, metadata, distance in zip(
            result["documents"][0], result["metadatas"][0], result["distances"][0]
        ):
            hits.append({
                "chunk_id": metadata["chunk_id"],
                "company": metadata["company"],
                "year": metadata["year"],
                "page": metadata["page"],
                "section_type": metadata["section_type"],
                "chunk_type": metadata["chunk_type"],
                "text": document,
                "semantic_score": 1.0 - float(distance),
            })
        if not hits:
            raise RuntimeError(
                f"Semantic search returned no matches for company={company!r}, year={year!r}."
            )
        return hits
