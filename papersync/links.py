"""Decide the links shown for a paper.

Policy:
  - arXiv id            -> public arXiv abstract page (open).
  - DOI + open access   -> the free OA copy (public).
  - DOI + paywalled     -> a Dropbox share link to the user's local PDF.
  - (no DOI/arXiv)      -> not a paper; never linked (and excluded upstream).

`doi_url` is always set when a DOI is known, so the page can show a canonical
reference link alongside whatever `pdf_url` points to.
"""

from __future__ import annotations

from pathlib import Path

from . import metadata
from .config import Config
from .sharelink import dropbox_sharelink

# Bump when the link policy changes, so refresh_links re-resolves old records.
LINKS_VERSION = 2


def resolve_links(record: dict, pdf_path: Path | None, cfg: Config) -> None:
    record["links_v"] = LINKS_VERSION
    doi = record.get("doi", "")
    record["doi_url"] = f"https://doi.org/{doi}" if doi else ""

    if record.get("arxiv_id"):
        record["pdf_url"] = f"https://arxiv.org/abs/{record['arxiv_id']}"
        record["pdf_url_kind"] = "arxiv"
        return

    if doi:
        oa = metadata.unpaywall_oa(doi, cfg.crossref_mailto)
        metadata.polite_sleep(0.2)
        if oa:
            record["pdf_url"] = oa
            record["pdf_url_kind"] = "oa"
            return
        # Paywalled: link to the user's own copy in Dropbox.
        if cfg.generate_dropbox_links and pdf_path is not None:
            link = dropbox_sharelink(pdf_path)
            if link:
                record["pdf_url"] = link
                record["pdf_url_kind"] = "dropbox"
                return
        # No OA copy and no Dropbox link available: fall back to the DOI page.
        record["pdf_url"] = record["doi_url"]
        record["pdf_url_kind"] = "doi"
        return

    record["pdf_url"] = ""
    record["pdf_url_kind"] = "none"
