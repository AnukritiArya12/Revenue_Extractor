"""Usage:  python -m src.ingest.run_ingest [--rebuild]

Put reports (.pdf or .docx) in data/reports/ named  <Company>_<Year>.<ext>
(e.g. Infosys_FY26.pdf, Microsoft_FY25.docx).
Output: data/processed/chunks.jsonl  (one chunk per line).
"""
import hashlib
import json
import logging
import sys
import os
import re
from pathlib import Path
import fitz
import pytesseract
from PIL import Image

from .section_tagger import tag_page
from .chunker import build_chunks

REPORTS = Path("data/reports")
OUT = Path("data/processed/chunks.jsonl")


def parse_name(path: Path):
    """Parse the required ``<company>_<fiscal-year>`` filename metadata."""
    stem = path.stem.strip()
    while Path(stem).suffix.lower() in {".pdf", ".doc", ".docx"}:
        stem = Path(stem).stem.strip()
    match = re.match(
        r"^(.*?)[_\s-]*(FY\s*\d{2,4}(?:[-_]\d{2,4})?|\d{4})$",
        stem,
        re.IGNORECASE,
    )
    if not match:
        raise ValueError(
            "filename must include company and fiscal year, e.g. Company_FY26.pdf"
        )
    company = match.group(1).rstrip(" _-").strip()
    year = re.sub(r"\s+", "", match.group(2)).replace("_", "-").upper()
    year = re.sub(r"(FY\d{4})-(\d{4})$", lambda m: m.group(1) + "-" + m.group(2)[-2:], year)
    if not company:
        raise ValueError("company name is missing from the filename")
    return company, year


def file_sha256(path: Path):
    """Return a stable fingerprint used to recognize renamed duplicate uploads."""
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def metadata_warnings(company, year, pages):
    """Cross-check filename metadata against the report's opening pages."""
    front_pages = pages[:12]
    front_text = "\n".join(text or "" for _, text, _ in front_pages)
    warnings = []
    from src.extract.query import label_year
    from src.retrieve.bm25_search import _name_key

    company_key = _name_key(company)
    company_found = any(company_key in _name_key(line) for line in front_text.splitlines())
    if not company_found:
        warnings.append(
            f"filename company {company!r} was not found in the opening report pages"
        )

    expected_year = label_year(year)
    content_years = {int(value) for value in re.findall(r"\b(?:19|20)\d{2}\b", front_text)}
    if expected_year and not ({expected_year, expected_year - 1} & content_years):
        warnings.append(
            f"filename report year {year} does not appear (or its prior-year comparative) "
            "in the opening report pages"
        )
    return warnings, sorted(content_years, reverse=True)

def ocr_pdf_page(page):
    """Use Tesseract OCR to extract text from a scanned PDF page."""
    import fitz
    import pytesseract
    from PIL import Image

    tesseract_path = Path(
        r"C:\Program Files\Tesseract-OCR\tesseract.exe"
    )

    if tesseract_path.exists():
        pytesseract.pytesseract.tesseract_cmd = str(tesseract_path)

    scale = 200 / 72
    pix = page.get_pixmap(
        matrix=fitz.Matrix(scale, scale),
        colorspace=fitz.csRGB,
        alpha=False,
    )

    image = Image.frombytes(
        "RGB",
        (pix.width, pix.height),
        pix.samples,
    )

    return pytesseract.image_to_string(
        image,
        lang="eng",
        config="--psm 3",
        timeout=45,
    ).strip()

def read_report(path: Path, company: str, year: str, source_sha256: str):
    """Read a report, OCR scanned PDF pages, and build unique chunks."""

    is_pdf = path.suffix.lower() == ".pdf"

    if is_pdf:
        from .pdf_loader import iter_pages

        # First, use the existing PDF loader.
        try:
            pages = list(iter_pages(path))
        except Exception as exc:
            logging.warning(
                "PDF loader failed for %s: %s. Trying OCR fallback.",
                path.name,
                exc,
            )
            pages = None

        # If the PDF loader fails, read the PDF directly.
        if pages is None:
            import fitz

            pages = []
            with fitz.open(str(path)) as pdf:
                for page_no, page in enumerate(pdf, start=1):
                    try:
                        text = page.get_text("text", sort=True) or ""
                    except Exception:
                        text = ""

                    pages.append((page_no, text, []))

        # OCR pages with little or no extractable text.
        import fitz

        with fitz.open(str(path)) as pdf:
            processed_pages = []

            for page_no, text, tables in pages:
                text = text or ""
                tables = tables or []

                if len(text.strip()) < 30:
                    try:
                        if 1 <= page_no <= len(pdf):
                            ocr_text = ocr_pdf_page(pdf[page_no - 1])

                            if len(ocr_text) > len(text.strip()):
                                text = ocr_text

                            logging.info(
                                "OCR checked %s page %s: %d characters",
                                path.name,
                                page_no,
                                len(ocr_text),
                            )
                    except Exception as exc:
                        logging.warning(
                            "OCR failed for %s page %s: %s",
                            path.name,
                            page_no,
                            exc,
                        )

                processed_pages.append((page_no, text.strip(), tables))

        pages = processed_pages

    else:
        from .docx_loader import iter_pages
        pages = list(iter_pages(path))

    metadata_notes, content_years = metadata_warnings(company, year, pages)

    # Preserve the existing section tagging and chunking pipeline.
    chunks, counts = [], {}

    for page_no, text, tables in pages:
        text = text or ""
        tables = tables or []

        if not text.strip() and not tables:
            continue

        section = tag_page(text)
        counts[section] = counts.get(section, 0) + 1

        page_chunks = build_chunks(
            company, year, page_no, section, text, tables
        )

        for idx, ch in enumerate(page_chunks):
            ch_dict = ch.to_dict() if hasattr(ch, "to_dict") else dict(ch)
            ch_dict["chunk_id"] = (
                f"{company}_{year}_p{page_no}_c{idx + 1}"
            )
            ch_dict["source_file"] = path.name
            ch_dict["source_sha256"] = source_sha256
            ch_dict["company_source"] = "filename"
            ch_dict["report_year_source"] = "filename"
            ch_dict["metadata_warnings"] = metadata_notes
            chunks.append(ch_dict)

    return chunks, counts, metadata_notes, content_years



def main():
    files = sorted(
        p for p in REPORTS.iterdir()
        if p.suffix.lower() in (".pdf", ".docx") and not p.name.startswith("~$")
    ) if REPORTS.exists() else []
    if not files:
        sys.exit(f"No .pdf or .docx files found in {REPORTS}/")
    OUT.parent.mkdir(parents=True, exist_ok=True)
    rebuild = "--rebuild" in sys.argv[1:]
    incremental = not rebuild
    existing_reports = set()
    existing_hashes = set()
    fingerprinted_reports = set()
    from src.retrieve.bm25_search import _name_key, _report_key

    logging.basicConfig(filename=OUT.parent / "ingest.log", level=logging.INFO,
                        filemode="a" if incremental else "w",
                        format="%(asctime)s %(levelname)s %(message)s")
    if incremental:
        if OUT.exists():
            with OUT.open(encoding="utf-8") as existing_file:
                for line_number, line in enumerate(existing_file, start=1):
                    if line.strip():
                        try:
                            chunk = json.loads(line)
                        except json.JSONDecodeError:
                            logging.error("invalid JSON in %s at line %d", OUT, line_number)
                            continue
                        existing_reports.add((
                            _name_key(chunk.get("company")),
                            _report_key(chunk.get("year")),
                        ))
                        if chunk.get("source_sha256"):
                            existing_hashes.add(chunk["source_sha256"])
                            fingerprinted_reports.add((
                                _name_key(chunk.get("company")),
                                _report_key(chunk.get("year")),
                            ))

        # Older chunks predate source fingerprints. Hash their matching source
        # files once so a renamed copy can still be recognized as a duplicate.
        for source_path in files:
            try:
                source_company, source_year = parse_name(source_path)
                identity = (_name_key(source_company), _report_key(source_year))
                if identity in existing_reports and identity not in fingerprinted_reports:
                    existing_hashes.add(file_sha256(source_path))
            except (OSError, ValueError) as exc:
                logging.warning("could not fingerprint existing source %s: %s", source_path.name, exc)

    total, failed, skipped, ingested = 0, [], 0, 0
    with OUT.open("a" if incremental else "w", encoding="utf-8") as f:
        for path in files:
            try:
                company, year = parse_name(path)
                identity = (_name_key(company), _report_key(year))
                if identity in existing_reports:
                    print(f"[SKIP] {path.name}: company/report already ingested as {company} {year}")
                    skipped += 1
                    continue

                source_hash = file_sha256(path)
                if source_hash in existing_hashes:
                    print(f"[SKIP] {path.name}: file contents match an already ingested report")
                    skipped += 1
                    continue

                got = read_report(path, company, year, source_hash)  # stage before writing any chunks
            except Exception as e:
                logging.exception("ingestion failed for %s", path.name)
                print(f"[ERROR] {path.name} | metadata/read stage | {type(e).__name__}: {e} "
                      f"(details in {OUT.parent / 'ingest.log'})")
                failed.append(path.name)
                continue
            if got is None:
                print(f"[ERROR] {path.name} | extraction stage | no readable pages")
                logging.error("no readable pages in %s", path.name)
                failed.append(path.name)
                continue

            chunks, counts, notes, content_years = got
            if not chunks:
                message = "no text or tables extracted; check PDF health with: python -m src.tools pdfs"
                print(f"[ERROR] {path.name} | chunking stage | {message}")
                logging.error("%s: %s", path.name, message)
                failed.append(path.name)
                continue

            for ch in chunks:
                f.write(json.dumps(ch, ensure_ascii=False) + "\n")
            total += len(chunks)
            ingested += 1
            existing_reports.add(identity)
            existing_hashes.add(source_hash)
            note = " (docx: page numbers are approximate)" if path.suffix.lower() == ".docx" else ""
            logging.info("ingested %s as company=%s report=%s sha256=%s chunks=%d %s",
                         path.name, company, year, source_hash, len(chunks), counts)
            print(f"[OK] {path.name}: company={company}, report={year}, {len(chunks)} chunks, "
                  f"pages by section: {counts}{note}")
            if content_years:
                print(f"[META] {path.name}: opening-page years found: {content_years[:8]}")
            for warning in notes:
                print(f"[META WARNING] {path.name}: {warning}")
                logging.warning("metadata check for %s: %s", path.name, warning)
    print(f"Done. reports ingested={ingested}, skipped duplicates={skipped}, failed={len(failed)}, "
          f"new chunks={total} -> {OUT}")
    if failed:
        print("Failed files: " + ", ".join(failed))

    # Reuse stored embeddings and index only new or changed chunk text.
    from src.retrieve.semantic_search import SemanticSearch
    try:
        semantic = SemanticSearch(chunks_path=str(OUT))
        if rebuild:
            semantic.reload(prune=True)
    except Exception as exc:
        logging.exception("semantic index update failed after report ingestion")
        sys.exit(f"[INDEX ERROR] Reports were written to {OUT}, but the semantic index could not "
                 f"be updated: {type(exc).__name__}: {exc}")
    print(f"Semantic index: {semantic.collection.count()} chunks in {semantic.persist_path}")


if __name__ == "__main__":
    main()
