"""Word (.docx) loader. Returns pages in the same format as pdf_loader.iter_pages:
    yield (page_no, text, [table_texts])

Word files do not store pages, so page numbers are APPROXIMATE:
  - if the file contains page-break markers (Word writes them when saving), we use them
  - otherwise we start a new pseudo-page every ~6000 characters
"""
from docx import Document
from docx.table import Table
from docx.text.paragraph import Paragraph

PSEUDO_PAGE_CHARS = 6000


def _blocks(doc):
    """Paragraphs and tables in the order they appear in the document."""
    for child in doc.element.body.iterchildren():
        tag = child.tag.rsplit("}", 1)[-1]
        if tag == "p":
            yield Paragraph(child, doc)
        elif tag == "tbl":
            yield Table(child, doc)


def _breaks_in(paragraph):
    """Return (explicit, rendered) page-break counts for a paragraph.
    An explicit break ends the page, so the paragraph text belongs BEFORE it.
    A rendered break marks where a new page starts, so the text belongs AFTER it."""
    el = paragraph._p
    explicit = len(el.xpath('.//w:br[@w:type="page"]'))
    rendered = len(el.xpath(".//w:lastRenderedPageBreak"))
    return explicit, rendered


def _table_to_text(table) -> str:
    lines = []
    for row in table.rows:
        cells, seen = [], set()
        for cell in row.cells:          # merged cells repeat; keep each once
            if id(cell._tc) in seen:
                continue
            seen.add(id(cell._tc))
            cells.append(cell.text.replace("\n", " ").strip())
        if any(cells):
            lines.append(" | ".join(cells))
    return "\n".join(lines)


def iter_pages(path):
    doc = Document(path)
    blocks = list(_blocks(doc))
    has_breaks = any(isinstance(b, Paragraph) and any(_breaks_in(b)) for b in blocks)

    page_no, parts, tables, size = 1, [], [], 0

    def flush():
        nonlocal parts, tables, size
        text = "\n\n".join(parts).strip()
        out = (page_no, text, tables) if (text or tables) else None
        parts, tables, size = [], [], 0
        return out

    for b in blocks:
        if isinstance(b, Paragraph):
            explicit, rendered = _breaks_in(b)
            if rendered and not explicit:   # new page starts here: close old page first
                res = flush()
                if res:
                    yield res
                page_no += rendered
            if b.text.strip():
                parts.append(b.text.strip())
                size += len(b.text)
            if explicit:                    # page ends here: text stays on this page
                res = flush()
                if res:
                    yield res
                page_no += explicit
        else:
            t = _table_to_text(b)
            if t:
                parts.append(t)           # table text is also part of the page text
                if len(t) > 40:
                    tables.append(t)
                size += len(t)
        if not has_breaks and size >= PSEUDO_PAGE_CHARS:
            res = flush()
            if res:
                yield res
            page_no += 1
    res = flush()
    if res:
        yield res
