"""Resolve bibliographic metadata for a paper from public sources.

Order of preference, most authoritative first:
  1. Crossref by DOI          (journal articles, most preprints have a DOI)
  2. arXiv API by arXiv id     (arXiv preprints)
  3. Crossref by title         (when only a title could be extracted)
  4. LLM over first-page text  (only if an API key is configured)

Each resolver returns a partial Record dict; the caller merges them and fills
gaps (e.g. abstract from the PDF text) afterwards.
"""

from __future__ import annotations

import html
import re
import time
import xml.etree.ElementTree as ET
from difflib import SequenceMatcher

import requests

_SESSION = requests.Session()


def _ua(mailto: str) -> dict:
    return {"User-Agent": f"papersimreading/0.1 (mailto:{mailto})"}


def _strip_jats(abstract: str) -> str:
    """Crossref abstracts are JATS XML fragments; reduce to plain text."""
    if not abstract:
        return ""
    text = re.sub(r"<[^>]+>", " ", abstract)
    text = html.unescape(text)
    text = re.sub(r"\s+", " ", text).strip()
    # Crossref often prefixes the literal word "Abstract".
    return re.sub(r"^abstract\s*", "", text, flags=re.IGNORECASE).strip()


def _strip_tags(s: str) -> str:
    if not s:
        return ""
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", "", s))).strip()


def _crossref_item_to_record(item: dict) -> dict:
    title = (item.get("title") or [""])[0].strip()
    authors = []
    for a in item.get("author", []) or []:
        name = " ".join(p for p in (a.get("given"), a.get("family")) if p).strip()
        if name:
            authors.append(name)
    journal = (item.get("container-title") or [""])
    journal = journal[0] if journal else ""
    # Publisher stands in for venue on preprint servers with no container title.
    if not journal:
        journal = item.get("publisher", "") or ""

    date_parts = (
        item.get("published", {}).get("date-parts")
        or item.get("published-online", {}).get("date-parts")
        or item.get("published-print", {}).get("date-parts")
        or item.get("issued", {}).get("date-parts")
        or [[]]
    )[0]
    published = "-".join(f"{p:02d}" if i else str(p) for i, p in enumerate(date_parts))

    return {
        "title": _strip_tags(title),
        "authors": authors,
        "journal": _strip_tags(journal),
        "published": published,
        "doi": item.get("DOI", ""),
        "abstract": _strip_jats(item.get("abstract", "")),
        "extraction": "crossref",
    }


def doi_variants(doi: str):
    """Yield the DOI and cleaned variants to try (fallbacks only).

    PDF-extracted DOIs sometimes carry a trailing article id (Oxford: ".../btae547/
    7758065") or supplement segment; stripping a trailing "/<digits>" recovers the
    canonical DOI.
    """
    seen = set()
    for cand in (doi, re.sub(r"/\d+$", "", doi)):
        if cand and cand not in seen:
            seen.add(cand)
            yield cand


def crossref_by_doi(doi: str, mailto: str) -> dict | None:
    try:
        r = _SESSION.get(
            f"https://api.crossref.org/works/{requests.utils.quote(doi)}",
            headers=_ua(mailto),
            timeout=30,
        )
        if r.status_code != 200:
            return None
        return _crossref_item_to_record(r.json()["message"])
    except (requests.RequestException, KeyError, ValueError):
        return None


def _similar(a: str, b: str) -> float:
    return SequenceMatcher(None, a.lower(), b.lower()).ratio()


_STOPWORDS = {
    "a", "an", "the", "of", "and", "or", "to", "in", "on", "for", "with", "by",
    "from", "as", "at", "is", "are", "using", "via", "based",
}


def _norm_title(s: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9]+", " ", s.lower())).strip()


def _content_tokens(s: str) -> list[str]:
    return [t for t in _norm_title(s).split() if t not in _STOPWORDS and len(t) >= 2]


def _title_matches(query: str, candidate: str) -> bool:
    """Accept a Crossref title hit only on strong evidence (precision-first).

    A loose token overlap wrongly matches unrelated papers when the guessed
    title is a few common words (e.g. "Series Pre-A funding" -> a paper with
    "...Time Series..."). So accept only when the normalized strings are
    near-identical, OR the query is a genuine prefix of the candidate (the case
    where a real title wrapped across lines and we grabbed the first line).
    """
    q, c = _norm_title(query), _norm_title(candidate)
    if not q or not c:
        return False
    if SequenceMatcher(None, q, c).ratio() >= 0.90:
        return True
    if len(_content_tokens(query)) >= 4 and (c == q or c.startswith(q + " ")):
        return True
    return False


def europepmc_abstract(doi: str) -> str:
    """Abstract text for a DOI from Europe PMC (good coverage for life sciences)."""
    try:
        r = _SESSION.get(
            "https://www.ebi.ac.uk/europepmc/webservices/rest/search",
            params={"query": f'DOI:"{doi}"', "format": "json",
                    "resultType": "core", "pageSize": 1},
            timeout=30,
        )
        res = r.json().get("resultList", {}).get("result", [])
    except (requests.RequestException, ValueError):
        return ""
    if res and res[0].get("abstractText"):
        return _strip_tags(res[0]["abstractText"])
    return ""


def semanticscholar_abstract(doi: str) -> str:
    """Abstract text for a DOI from Semantic Scholar (broader field coverage)."""
    try:
        r = _SESSION.get(
            f"https://api.semanticscholar.org/graph/v1/paper/DOI:{requests.utils.quote(doi)}",
            params={"fields": "abstract"},
            timeout=30,
        )
        if r.status_code != 200:
            return ""
        return (r.json().get("abstract") or "").strip()
    except (requests.RequestException, ValueError):
        return ""


def abstract_from_apis(doi: str) -> str:
    """Best-effort abstract for a DOI when Crossref/arXiv didn't provide one."""
    return europepmc_abstract(doi) or semanticscholar_abstract(doi)


def crossref_by_title(title: str, mailto: str) -> dict | None:
    try:
        r = _SESSION.get(
            "https://api.crossref.org/works",
            params={"query.bibliographic": title, "rows": 3},
            headers=_ua(mailto),
            timeout=30,
        )
        if r.status_code != 200:
            return None
        items = r.json()["message"]["items"]
    except (requests.RequestException, KeyError, ValueError):
        return None

    for item in items:
        cand = (item.get("title") or [""])[0]
        if cand and _title_matches(title, cand):
            rec = _crossref_item_to_record(item)
            rec["extraction"] = "crossref-title"
            return rec
    return None


def arxiv_by_id(arxiv_id: str) -> dict | None:
    try:
        r = _SESSION.get(
            "http://export.arxiv.org/api/query",
            params={"id_list": arxiv_id, "max_results": 1},
            timeout=30,
        )
        if r.status_code != 200:
            return None
        root = ET.fromstring(r.text)
    except (requests.RequestException, ET.ParseError):
        return None

    ns = {"a": "http://www.w3.org/2005/Atom"}
    entry = root.find("a:entry", ns)
    if entry is None:
        return None

    def text(tag: str) -> str:
        el = entry.find(f"a:{tag}", ns)
        return (el.text or "").strip() if el is not None else ""

    authors = [
        (a.find("a:name", ns).text or "").strip()
        for a in entry.findall("a:author", ns)
        if a.find("a:name", ns) is not None
    ]
    published = text("published")[:10]  # YYYY-MM-DD
    # A DOI is present once the preprint is published in a journal.
    doi_el = entry.find("{http://arxiv.org/schemas/atom}doi")
    return {
        "title": re.sub(r"\s+", " ", text("title")).strip(),
        "authors": authors,
        "journal": "arXiv",
        "published": published,
        "doi": (doi_el.text or "").strip() if doi_el is not None else "",
        "arxiv_id": arxiv_id,
        "abstract": re.sub(r"\s+", " ", text("summary")).strip(),
        "extraction": "arxiv",
    }


def llm_extract(first_page_text: str, api_key: str, model: str) -> dict | None:
    """Ask an LLM to pull structured fields from messy first-page text.

    Used only when identifier and title lookups all fail. Calls the Anthropic
    API directly over HTTP so no SDK dependency is required.
    """
    if not first_page_text.strip():
        return None
    prompt = (
        "Extract bibliographic metadata from the first page of this academic "
        "paper. Return ONLY a JSON object with keys: title (string), authors "
        "(array of full-name strings), journal (string, the venue/journal or "
        '"" if unknown), published (string "YYYY-MM-DD" or "YYYY" or ""), '
        "abstract (string, the paper's abstract verbatim or \"\" if not "
        "present), doi (string or \"\"). Do not invent values; use \"\" when "
        "unsure.\n\n---FIRST PAGE---\n" + first_page_text[:6000]
    )
    try:
        r = _SESSION.post(
            "https://api.anthropic.com/v1/messages",
            headers={
                "x-api-key": api_key,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            },
            json={
                "model": model,
                "max_tokens": 1500,
                "messages": [{"role": "user", "content": prompt}],
            },
            timeout=60,
        )
        if r.status_code != 200:
            return None
        content = r.json()["content"][0]["text"]
    except (requests.RequestException, KeyError, ValueError, IndexError):
        return None

    import json

    m = re.search(r"\{.*\}", content, re.DOTALL)
    if not m:
        return None
    try:
        data = json.loads(m.group(0))
    except ValueError:
        return None
    data["extraction"] = "llm"
    data.setdefault("authors", [])
    if isinstance(data.get("authors"), str):
        data["authors"] = [data["authors"]]
    return data


def unpaywall_oa(doi: str, mailto: str) -> str | None:
    """Return a free open-access URL for this DOI, or None if it's paywalled.

    Uses Unpaywall. A non-None result means an openly licensed copy exists, so we
    can link to the public version instead of the user's Dropbox copy.
    """
    try:
        r = _SESSION.get(
            f"https://api.unpaywall.org/v2/{requests.utils.quote(doi)}",
            params={"email": mailto},
            timeout=30,
        )
        if r.status_code != 200:
            return None
        data = r.json()
    except (requests.RequestException, ValueError):
        return None
    if not data.get("is_oa"):
        return None
    loc = data.get("best_oa_location") or {}
    return loc.get("url_for_pdf") or loc.get("url") or None


def polite_sleep(seconds: float = 0.5) -> None:
    time.sleep(seconds)
