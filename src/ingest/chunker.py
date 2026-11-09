"""Split page text into chunks. Tables are kept whole (or split by rows
with the header repeated), so a number never gets separated from its label."""
import re
from dataclasses import asdict, dataclass

MAX_CHARS = 1800      # ~400 tokens
OVERLAP_CHARS = 200


@dataclass
class Chunk:
    chunk_id: str      # e.g. "acme_2025_p112_c0"
    company: str
    year: str          # the report label, e.g. FY26
    page: int          # 1-based page number, as in the PDF viewer
    section_type: str  # income_statement | highlights | other
    chunk_type: str    # "text" or "table"
    text: str

    def to_dict(self):
        return asdict(self)


def split_text(text: str, max_chars: int = MAX_CHARS, overlap: int = OVERLAP_CHARS):
    """Paragraph-aware splitting with a small overlap."""
    paras = [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]
    chunks, current = [], ""
    for p in paras:
        if len(current) + len(p) + 2 <= max_chars:
            current = f"{current}\n\n{p}" if current else p
            continue
        if current:
            chunks.append(current)
        if len(p) > max_chars:  # a single huge paragraph: hard split
            for i in range(0, len(p), max_chars - overlap):
                chunks.append(p[i:i + max_chars])
            current = ""
        else:
            tail = current[-overlap:] if current else ""
            current = f"{tail}\n\n{p}".strip() if tail else p
    if current:
        chunks.append(current)
    return chunks


def split_table(table_text: str, max_chars: int = MAX_CHARS):
    """Split a long table by rows, repeating the header row in each part."""
    if len(table_text) <= max_chars:
        return [table_text]
    rows = table_text.split("\n")
    header, body = rows[0], rows[1:]
    parts, current = [], header
    for row in body:
        if len(current) + len(row) + 1 > max_chars and current != header:
            parts.append(current)
            current = header
        current += "\n" + row
    if current != header:
        parts.append(current)
    return parts


def build_chunks(company, year, page_no, section, text, tables):
    base = f"{company}_{year}_p{page_no}".lower().replace(" ", "")
    out, n = [], 0
    for t in split_text(text):
        out.append(Chunk(f"{base}_c{n}", company, year, page_no, section, "text", t))
        n += 1
    for tbl in tables:
        for part in split_table(tbl):
            out.append(Chunk(f"{base}_c{n}", company, year, page_no, section, "table", part))
            n += 1
    return out
