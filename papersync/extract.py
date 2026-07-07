"""Pull raw text and identifiers out of a PDF.

Two jobs:
  1. text_first_pages() - readable text from the opening pages, used both for
     identifier detection and (as a last resort) abstract extraction.
  2. find_identifiers() - locate a DOI or arXiv id in the text or the PDF's own
     embedded metadata, so we can query an authoritative source.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

# DOIs: 10.<registrant>/<suffix>. Suffix runs until whitespace or a few
# characters that are almost never part of a real DOI in running text.
DOI_RE = re.compile(r"\b10\.\d{4,9}/[-._;()/:A-Za-z0-9]+", re.IGNORECASE)

# arXiv new style (1501.00001) and old style (math.GT/0309136).
ARXIV_NEW_RE = re.compile(r"arXiv:\s*(\d{4}\.\d{4,5})(v\d+)?", re.IGNORECASE)
ARXIV_OLD_RE = re.compile(
    r"arXiv:\s*([a-z-]+(?:\.[A-Z]{2})?/\d{7})(v\d+)?", re.IGNORECASE
)


def text_first_pages(pdf_path: Path, pages: int = 2) -> str:
    """Extract text from the first `pages` pages using poppler's pdftotext."""
    try:
        out = subprocess.run(
            ["pdftotext", "-f", "1", "-l", str(pages), "-q", str(pdf_path), "-"],
            capture_output=True,
            timeout=60,
        )
        return out.stdout.decode("utf-8", errors="replace")
    except (subprocess.SubprocessError, FileNotFoundError):
        return ""


def embedded_metadata(pdf_path: Path) -> dict:
    """Return the PDF's own /Title, /Author and any DOI in the XMP metadata."""
    meta: dict = {}
    try:
        from pypdf import PdfReader

        reader = PdfReader(str(pdf_path))
        info = reader.metadata or {}
        if info.get("/Title"):
            meta["title"] = str(info["/Title"]).strip()
        if info.get("/Author"):
            meta["author"] = str(info["/Author"]).strip()
        # XMP sometimes carries the DOI as dc:identifier / prism:doi.
        xmp = getattr(reader, "xmp_metadata", None)
        if xmp is not None:
            raw = getattr(xmp, "rdf_root", None)
            if raw is not None:
                m = DOI_RE.search(raw.toxml())
                if m:
                    meta["doi"] = _clean_doi(m.group(0))
    except Exception:
        pass
    return meta


def _clean_doi(doi: str) -> str:
    # Strip trailing punctuation that regularly clings to a DOI in body text.
    return doi.rstrip(".,;)]}>\"'").rstrip(".")


def find_identifiers(text: str, meta: dict) -> dict:
    """Return {'doi': .., 'arxiv_id': ..} using text first, then embedded meta."""
    found: dict = {}

    for pat, key, group in (
        (ARXIV_NEW_RE, "arxiv_id", 1),
        (ARXIV_OLD_RE, "arxiv_id", 1),
    ):
        m = pat.search(text)
        if m:
            found[key] = m.group(group)
            break

    m = DOI_RE.search(text)
    if m:
        found["doi"] = _clean_doi(m.group(0))
    elif meta.get("doi"):
        found["doi"] = meta["doi"]

    return found


_TITLE_JUNK_LINE = re.compile(
    r"^(biorxiv|medrxiv|arxiv|preprint|research article|article|www\.|http|"
    r"vol\.|volume|doi:|\d+\s*$|figure|table)",
    re.IGNORECASE,
)


def guess_title(text: str, meta: dict) -> str:
    """Best-effort title for a bibliographic query when no DOI/arXiv id exists.

    Prefer the PDF's embedded /Title when it looks like a real title (embedded
    titles are often junk like the filename or "Microsoft Word - ..."). Otherwise
    stitch together the leading title-like lines of the text, since titles often
    wrap across two or three lines.
    """
    t = (meta.get("title") or "").strip()
    if _looks_like_title(t):
        return t

    lines = [ln.strip() for ln in text.splitlines()]
    parts: list[str] = []
    started = False
    for line in lines:
        if not started:
            if _looks_like_title(line) and not _TITLE_JUNK_LINE.match(line):
                parts.append(line)
                started = True
            continue
        # Once started, keep appending continuation lines until a blank line or
        # a line that looks like an author list / affiliation / abstract heading.
        if not line:
            break
        if _TITLE_JUNK_LINE.match(line) or _looks_like_author_line(line):
            break
        if re.match(r"^\s*abstract\b", line, re.IGNORECASE):
            break
        parts.append(line)
        if len(" ".join(parts)) > 250:
            break

    joined = _normalize_ws(" ".join(parts))
    return joined or t


def _looks_like_author_line(s: str) -> bool:
    # Author lines: many commas, initials with periods, or superscript markers.
    if s.count(",") >= 2 and re.search(r"[A-Z]\.\s*[A-Z]", s):
        return True
    if re.search(r"\b[A-Z]\.\s*[A-Z][a-z]+", s) and s.count(",") >= 1:
        return True
    return False


# Filenames in a curated Dropbox folder are often the identifier itself. Trying
# these as DOIs (cheap Crossref lookups) recovers metadata for old preprints and
# publisher PDFs whose DOI is not printed on the first page.
_FN_DIGITS = re.compile(r"^(\d{6,})(?:\.full)?(?:-\d+)?$")
_FN_BIORXIV_DATED = re.compile(r"^(\d{4}\.\d{2}\.\d{2}\.\d{6,})(?:v\d+)?(?:\.full)?$")
_FN_NATURE = re.compile(r"^(s\d{5}-\d{3}-\d{4,6}-[a-z0-9]+)$", re.IGNORECASE)


def filename_doi_candidates(stem: str) -> list[str]:
    """DOIs worth trying based on the filename alone (folder-specific heuristics)."""
    stem = stem.strip()
    cands: list[str] = []
    m = _FN_BIORXIV_DATED.match(stem)
    if m:
        cands.append(f"10.1101/{m.group(1)}")
    m = _FN_DIGITS.match(stem)
    if m:
        cands.append(f"10.1101/{m.group(1)}")  # old bioRxiv numeric ids
    m = _FN_NATURE.match(stem)
    if m:
        cands.append(f"10.1038/{m.group(1)}")
    return cands


def _looks_like_title(s: str) -> bool:
    if not s or len(s) < 12 or len(s) > 300:
        return False
    low = s.lower()
    junk = ("microsoft word", "untitled", ".pdf", ".doc", "http://", "https://")
    if any(j in low for j in junk):
        return False
    # A title has letters and some spaces; reject all-caps codes / URLs / numbers.
    if sum(c.isalpha() for c in s) < 8:
        return False
    if " " not in s:
        return False
    return True


def extract_abstract_from_text(text: str) -> str:
    """Pull an Abstract section out of raw first-page text as a fallback."""
    # Find a line that is (or starts with) "Abstract", grab following paragraph(s)
    # until an Introduction/Keywords heading or a blank-line gap after enough text.
    lines = text.splitlines()
    start = None
    for i, line in enumerate(lines):
        if re.match(r"^\s*abstract\b\s*:?\s*$", line, re.IGNORECASE) or re.match(
            r"^\s*abstract[:.\-\s]", line, re.IGNORECASE
        ):
            start = i
            break
    if start is None:
        return ""

    collected: list[str] = []
    # If "Abstract" had trailing text on the same line, keep it.
    first = re.sub(r"^\s*abstract[:.\-\s]*", "", lines[start], flags=re.IGNORECASE)
    if first.strip():
        collected.append(first.strip())

    for line in lines[start + 1 :]:
        s = line.strip()
        if re.match(
            r"^\s*(introduction|keywords?|1\.?\s+introduction|index terms)\b",
            s,
            re.IGNORECASE,
        ):
            break
        if not s:
            if len(" ".join(collected)) > 250:
                break
            continue
        collected.append(s)
        if len(" ".join(collected)) > 2500:
            break

    return _normalize_ws(" ".join(collected))


def _normalize_ws(s: str) -> str:
    return re.sub(r"\s+", " ", s).strip()
