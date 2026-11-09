"""Hybrid BM25 + semantic retrieval."""

from .bm25_search import search as bm25_search, _name_key, _report_key
from .semantic_search import SemanticSearch
from .reranker import CrossEncoderReranker


def _debug_component_hits(bm25_results, semantic_results, candidates, results, company, year, k):
    bm25_debug = []
    for score, original in bm25_results[:k]:
        item = original.copy()
        item["bm25_score"] = float(score)
        item["semantic_score"] = candidates.get(item["chunk_id"], {}).get("semantic_score", 0.0)
        bm25_debug.append(item)

    hybrid_scores = {item["chunk_id"]: item["hybrid_score"] for item in results}
    semantic_debug = []
    for original in semantic_results:
        if (
            _name_key(original.get("company")) != _name_key(company)
            or _report_key(original.get("year")) != _report_key(year)
        ):
            continue
        item = original.copy()
        matched = candidates.get(item["chunk_id"], {})
        item["bm25_score"] = matched.get("bm25_score", 0.0)
        item["hybrid_score"] = hybrid_scores.get(item["chunk_id"], 0.0)
        semantic_debug.append(item)
        if len(semantic_debug) >= k:
            break
    return bm25_debug, semantic_debug


class HybridSearch:

    def __init__(
        self,
        semantic_search=None,
        reranker=None,
        bm25_weight=0.6,
        semantic_weight=0.4,
        enable_rerank=True,
    ):
        self.semantic_search = semantic_search or SemanticSearch()
        self.enable_rerank = enable_rerank
        self.reranker = reranker or (CrossEncoderReranker() if enable_rerank else None)
        self.bm25_weight = bm25_weight
        self.semantic_weight = semantic_weight

    def search(
        self,
        company,
        year,
        query,
        k=5,
        boost=True,
        phrases=None,
        rerank=True,
        debug=False,
    ):
        # Retrieve a broader pool of BM25 candidates for re-ranking
        bm25_k = max(k * 3, 15) if (rerank and self.reranker) else k
        bm25_results = bm25_search(
            company=company,
            year=year,
            query=query,
            k=bm25_k,
            boost=boost,
            phrases=phrases,
        )

        # Semantic search
        semantic_results = self.semantic_search.search(
            query,
            top_k=max(k * 20, 50),
            company=company,
            year=year,
        )
        if not semantic_results:
            raise RuntimeError(
                f"Semantic search returned no candidates for {company} {year}; "
                "refusing to continue with BM25-only retrieval."
            )

        candidates = {}

        # BM25 returns: (score, chunk)
        for score, chunk in bm25_results:
            chunk_id = chunk["chunk_id"]

            candidates[chunk_id] = {
                "chunk": chunk,
                "bm25_score": float(score),
                "semantic_score": 0.0,
            }

        # Semantic results
        for chunk in semantic_results:

            # Keep only requested company/year
            if (
                _name_key(chunk.get("company")) != _name_key(company)
                or _report_key(chunk.get("year")) != _report_key(year)
            ):
                continue

            chunk_id = chunk["chunk_id"]

            if chunk_id not in candidates:
                candidates[chunk_id] = {
                    "chunk": chunk,
                    "bm25_score": 0.0,
                    "semantic_score": 0.0,
                }

            candidates[chunk_id]["semantic_score"] = float(
                chunk["semantic_score"]
            )

        if not candidates:
            return []

        # Normalize scores
        bm25_values = [
            item["bm25_score"]
            for item in candidates.values()
        ]

        semantic_values = [
            item["semantic_score"]
            for item in candidates.values()
        ]

        max_bm25 = max(bm25_values) if bm25_values else 0
        max_semantic = max(semantic_values) if semantic_values else 0

        results = []

        for item in candidates.values():

            bm25_normalized = (
                item["bm25_score"] / max_bm25
                if max_bm25 > 0
                else 0.0
            )

            semantic_normalized = (
                item["semantic_score"] / max_semantic
                if max_semantic > 0
                else 0.0
            )

            hybrid_score = (
                self.bm25_weight * bm25_normalized
                + self.semantic_weight * semantic_normalized
                )

            # Financial statement/table evidence gets a ranking boost.
            if item["chunk"].get("section_type") == "income_statement":
                hybrid_score *= 1.25


            if item["chunk"].get("chunk_type") == "table":
                hybrid_score *= 1.10


            chunk = item["chunk"].copy()

            chunk["bm25_score"] = item["bm25_score"]
            chunk["semantic_score"] = item["semantic_score"]
            chunk["hybrid_score"] = hybrid_score

            results.append(chunk)

        results.sort(
            key=lambda x: x["hybrid_score"],
            reverse=True,
        )

        if rerank and self.reranker:
            candidate_pool = results[: max(k * 3, 10)]
            reranked = self.reranker.rerank(query, candidate_pool, top_k=k)
            if debug:
                bm25_debug, semantic_debug = _debug_component_hits(
                    bm25_results, semantic_results, candidates, results, company, year, k
                )

                return {
                    "bm25": bm25_debug,
                    "semantic": semantic_debug,
                    "hybrid": results[:k],
                    "reranker_input": candidate_pool,
                    "reranked": reranked,
                    "reranker_available": getattr(self.reranker, "model", None) is not None,
                    "rerank_order_changed": [x["chunk_id"] for x in reranked]
                    != [x["chunk_id"] for x in candidate_pool[:k]],
                }
            return reranked

        if debug:
            bm25_debug, semantic_debug = _debug_component_hits(
                bm25_results, semantic_results, candidates, results, company, year, k
            )

            return {
                "bm25": bm25_debug,
                "semantic": semantic_debug,
                "hybrid": results[:k],
                "reranker_input": [],
                "reranked": [],
                "reranker_available": False,
                "rerank_order_changed": False,
            }

        return results[:k]
