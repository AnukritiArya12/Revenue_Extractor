"""Small helpers for looking inside the data.

    python -m src.tools reports                       reports that have been ingested
    python -m src.tools pdfs                          health check of every PDF in data/reports
    python -m src.tools page TCS FY26 211             everything read from one page
    python -m src.tools find TCS FY26 "regex" [max]   where does this text appear?
"""
import json
import re
import sys
from collections import defaultdict
from pathlib import Path

CHUNKS = "data/processed/chunks.jsonl"


def _chunks():
    with open(CHUNKS, encoding="utf-8") as f:
        for line in f:
            if line.strip():
                yield json.loads(line)


def reports():
    pages = defaultdict(set)
    for c in _chunks():
        pages[(c["company"], c["year"])].add(c["page"])
    for (company, report), p in sorted(pages.items()):
        print(f"{company:<16}{report:<8} pages {min(p)}-{max(p)}  ({len(p)} with text)")


def pdfs():
    """Tells a healthy PDF from a scanned one (no text layer) and from a damaged one."""
    try:
        import pymupdf as fitz
    except ImportError:
        import fitz
    try:
        fitz.TOOLS.mupdf_display_errors(False)      # hide harmless MuPDF noise
    except Exception:
        pass
    print(f"{'file':<38}{'size':>9}{'pages':>7}{'avg chars':>11}   verdict")
    for p in sorted(Path("data/reports").glob("*.pdf")):
        size = f"{p.stat().st_size / 1e6:.1f} MB"
        try:
            doc = fitz.open(p)
            n = len(doc)
        except Exception as e:
            print(f"{p.name:<38}{size:>9}{'-':>7}{'-':>11}   CANNOT OPEN ({str(e)[:40]}) -> damaged file")
            continue
        idx = list(range(n)) if n <= 15 else [int(i * (n - 1) / 14) for i in range(15)]
        chars, bad = [], 0
        for i in idx:
            try:
                chars.append(len(doc[i].get_text("text").strip()))
            except Exception:
                bad += 1
        avg = sum(chars) / max(len(chars), 1)
        if bad >= max(2, len(idx) // 3):
            verdict = f"DAMAGED ({bad} of {len(idx)} sampled pages unreadable) -> re-download it"
        elif avg < 150:
            verdict = "NO TEXT LAYER (scanned or empty) -> needs OCR, or use another copy"
        else:
            verdict = "OK (text)"
        print(f"{p.name:<38}{size:>9}{n:>7}{avg:>11.0f}   {verdict}")


def page(company, report, number):
    for c in _chunks():
        if (c["company"], c["year"], c["page"]) == (company, report, int(number)):
            print(f"--- {c['chunk_id']} | {c['section_type']} | {c['chunk_type']} ---\n{c['text']}\n")


def find(company, report, pattern, limit=20):
    rx = re.compile(pattern, re.I)
    hits, seen = [], set()
    for c in _chunks():
        if (c["company"], c["year"], c["chunk_type"]) != (company, report, "text"):
            continue
        for m in rx.finditer(c["text"]):
            snippet = " ".join(c["text"][max(0, m.start() - 100): m.end() + 200].split())
            if (c["page"], snippet[:60]) not in seen:
                seen.add((c["page"], snippet[:60]))
                hits.append((c["page"], c["section_type"], snippet))
    hits.sort()
    print(f"{len(hits)} match(es) for /{pattern}/ in {company} {report}\n")
    for p, section, snippet in hits[:limit]:
        print(f"page {p} [{section}]: ...{snippet}...\n")


def main():
    a = sys.argv[1:]
    if a[:1] == ["reports"]:
        reports()
    elif a[:1] == ["pdfs"]:
        pdfs()
    elif a[:1] == ["page"] and len(a) == 4:
        page(a[1], a[2], a[3])
    elif a[:1] == ["find"] and len(a) >= 4:
        find(a[1], a[2], a[3], int(a[4]) if len(a) > 4 else 20)
    else:
        sys.exit(__doc__)


if __name__ == "__main__":
    main()
