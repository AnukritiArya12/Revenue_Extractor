"""Cross-Encoder Re-ranker module.

Takes candidate chunks from HybridSearch (BM25 + Semantic) and applies
deep token-level cross-attention scoring using a pretrained Cross-Encoder model.
This dramatically boosts exact financial statement precision while running 100% locally.
"""
import logging
from typing import List, Dict, Any, Optional

log = logging.getLogger("extractor")

DEFAULT_MODEL = "cross-encoder/ms-marco-MiniLM-L-6-v2"


class CrossEncoderReranker:
    def __init__(self, model_name: str = DEFAULT_MODEL):
        self.model_name = model_name
        self.model = None
        self._init_model()

    def _init_model(self):
        """Attempts to load CrossEncoder with local cache / offline fallback."""
        try:
            from sentence_transformers import CrossEncoder
            # First try default loading (downloads once if not cached, then cached locally)
            self.model = CrossEncoder(self.model_name)
            log.info("CrossEncoder '%s' loaded successfully.", self.model_name)
        except Exception as err:
            try:
                from sentence_transformers import CrossEncoder
                self.model = CrossEncoder(self.model_name, local_files_only=True)
                log.info("CrossEncoder '%s' loaded from local cache.", self.model_name)
            except Exception as fallback_err:
                log.warning(
                    "Could not initialize CrossEncoder model '%s' (%s). "
                    "Operating in fallback mode (preserving hybrid scores).",
                    self.model_name,
                    err,
                )
                self.model = None

    def rerank(
        self,
        query: str,
        candidates: List[Dict[str, Any]],
        top_k: int = 5,
    ) -> List[Dict[str, Any]]:
        """Re-ranks a list of candidate chunk dictionaries against the search query.

        Args:
            query: The search query (or metric query).
            candidates: List of chunk dictionaries containing 'text' and other metadata.
            top_k: Number of re-ranked chunks to return.

        Returns:
            Re-ordered candidate list sorted by cross-encoder score.
        """
        if not candidates:
            return []

        if self.model is None or not query:
            # Safe fallback if model is absent or query is empty
            return candidates[:top_k]

        # Prepare (query, chunk_text) pairs.
        # Strip excess whitespace, truncate overly long chunks to max model length
        pairs = []
        for c in candidates:
            chunk_text = c.get("text", "")
            # If section_type or page is available, provide slight context prefix
            sec = c.get("section_type", "")
            prefix = f"Section: {sec}\n" if sec and sec != "other" else ""
            pairs.append([query, prefix + chunk_text[:2000]])

        try:
            scores = self.model.predict(pairs)

            reranked = []
            for chunk, score in zip(candidates, scores):
                item = chunk.copy()
                raw_score = float(score)

                # Maintain slight domain boost for income statement and table chunks
                adjusted_score = raw_score
                if item.get("section_type") == "income_statement":
                    adjusted_score += 0.5
                if item.get("chunk_type") == "table":
                    adjusted_score += 0.2

                item["rerank_score"] = adjusted_score
                item["raw_rerank_score"] = raw_score
                reranked.append(item)

            reranked.sort(key=lambda x: x["rerank_score"], reverse=True)
            return reranked[:top_k]
        except Exception as err:
            log.warning("Cross-encoder reranking failed (%s). Reverting to hybrid order.", err)
            return candidates[:top_k]
