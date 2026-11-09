"""BM25 keyword search over the chunks of ONE report.

search(company, report, query) returns the best-matching chunks. boost=True multiplies the score
of chunks from income-statement / highlights pages (measured by `python -m eval.evaluate --search`).
"""
import json
import math
import re
from collections import Counter

import os
from collections import Counter, defaultdict

CHUNKS_PATH = "data/processed/chunks.jsonl"
BOOST = {"income_statement": 2.0, "highlights": 1.3}
PHRASE_BONUS = 1.5     # a chunk that contains one of the metric's exact names ("total income") scores higher

_CACHE = {}
_CACHE_PATH = None


def _name_key(value):
    """Normalize display names and common report-name aliases for matching."""
    value = str(value or "").strip().lower()
    value = re.sub(r"\.(?:pdf|docx?)$", "", value)
    words = re.findall(r"[a-z0-9]+", value)
    while words and words[-1] in {
        "limited", "ltd", "incorporated", "inc", "corporation", "corp",
        "plc", "company", "co",
    }:
        words.pop()
    key = "".join(words)
    aliases = {
        "jpmorgan": "jpmorgon",
        "jpmorganchase": "jpmorgon",
        "bankofamerica": "bankamerica",
    }
    return aliases.get(key, key)


def _report_key(value):
    """Normalize report labels such as FY26.pdf and FY26 to the same key."""
    value = re.sub(r"\.(?:pdf|docx?)$", "", str(value or "").strip().lower())
    return "".join(re.findall(r"[a-z0-9]+", value))


def _get_cache(path=CHUNKS_PATH):
    global _CACHE, _CACHE_PATH
    try:
        mtime = os.path.getmtime(path)
    except OSError:
        return {}
    if _CACHE_PATH == (path, mtime):
        return _CACHE
    cache = defaultdict(list)
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            for line in f:
                if line.strip():
                    c = json.loads(line)
                    cache[(_name_key(c.get("company")), _report_key(c.get("year")))].append(c)
    _CACHE = cache
    _CACHE_PATH = (path, mtime)
    return _CACHE


class ReportNotFound(ValueError):
    """The requested company/report has not been ingested."""

_TOKEN = re.compile(r"[a-z]+")


def tokenize(text: str):
    return _TOKEN.findall(text.lower())


class BM25:
    def __init__(self, docs_tokens, k1=1.5, b=0.75):
        self.k1, self.b = k1, b
        self.docs = docs_tokens
        self.N = len(docs_tokens)
        self.avgdl = sum(len(d) for d in docs_tokens) / max(self.N, 1)
        df = Counter()
        for d in docs_tokens:
            df.update(set(d))
        # idf with +1 inside the log so it never goes negative
        self.idf = {t: math.log(1 + (self.N - n + 0.5) / (n + 0.5)) for t, n in df.items()}
        self.tf = [Counter(d) for d in docs_tokens]

    def score(self, query_tokens, i):
        tf, dl, s = self.tf[i], len(self.docs[i]), 0.0
        for t in set(query_tokens):
            if t not in tf:
                continue
            f = tf[t]
            s += self.idf[t] * f * (self.k1 + 1) / (f + self.k1 * (1 - self.b + self.b * dl / self.avgdl))
        return s


def load_chunks(company, year, path=CHUNKS_PATH):
    cache = _get_cache(path)
    return cache.get((_name_key(company), _report_key(year)), [])


def search(company, year, query, k=5, boost=False, phrases=None, path=CHUNKS_PATH):
    """Best chunks of one report. boost = page-type boost; phrases = exact names that earn a bonus."""
    chunks = load_chunks(company, year, path)
    if not chunks:
        raise ReportNotFound(f"No report ingested for {company} {year}. See: python -m src.tools reports")
    bm25 = BM25([tokenize(c["text"]) for c in chunks])
    q = tokenize(query)
    scored = []
    for i, c in enumerate(chunks):
        s = bm25.score(q, i)
        if boost:
            s *= BOOST.get(c["section_type"], 1.0)
        if phrases and any(p in c["text"].lower() for p in phrases):
            s *= PHRASE_BONUS
        scored.append((s, c))
    scored.sort(key=lambda x: x[0], reverse=True)
    return scored[:k]
