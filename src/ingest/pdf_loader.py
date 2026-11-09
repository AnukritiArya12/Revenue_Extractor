"""PDF classification + page-wise text/table extraction (PyMuPDF)."""


def _open(path):
    try:
        import pymupdf as fitz          # new name, removes the "fitz deprecated" warning
    except ImportError:
        import fitz
    try:
        fitz.TOOLS.mupdf_display_errors(False)   # hide harmless MuPDF warnings
    except Exception:
        pass
    return fitz.open(path)


def classify_pdf(path, sample_pages: int = 15, min_chars_per_page: int = 150) -> str:
    """Return 'text' if the PDF has a real text layer, else 'scanned'.

    Scanned PDFs need OCR (out of scope for v1) so we fail loudly instead of
    silently producing empty chunks.
    """
    doc = _open(path)
    n = min(sample_pages, len(doc))
    if n == 0:
        return "scanned"
    # sample pages spread across the document, not just the cover pages
    idx = [int(i * (len(doc) - 1) / max(n - 1, 1)) for i in range(n)]
    total = sum(len(doc[i].get_text("text").strip()) for i in idx)
    doc.close()
    return "text" if total / n >= min_chars_per_page else "scanned"


def _table_to_text(table) -> str:
    """Convert a PyMuPDF table into pipe-separated rows (LLM friendly)."""
    rows = table.extract()
    lines = []
    for row in rows:
        cells = [(c or "").replace("\n", " ").strip() for c in row]
        if any(cells):
            lines.append(" | ".join(cells))
    return "\n".join(lines)


def iter_pages(path):
    """Yield (page_no, text, [table_texts]) for every page. page_no is 1-based."""
    doc = _open(path)
    for i, page in enumerate(doc):
        text = page.get_text("text")
        tables = []
        try:
            for t in page.find_tables().tables:
                tt = _table_to_text(t)
                if len(tt) > 40:  # skip tiny/noise tables
                    tables.append(tt)
        except Exception:
            pass  # table detection can fail on odd pages; text is still kept
        yield i + 1, text, tables
    doc.close()
