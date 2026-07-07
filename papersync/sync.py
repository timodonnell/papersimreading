"""Main pipeline: find new PDFs in the Dropbox folder and record their metadata.

Run:
    python -m papersync.sync                 # process everything not yet seen
    python -m papersync.sync --limit 20      # stop after 20 new papers
    python -m papersync.sync --since 90      # only PDFs modified in last 90 days
    python -m papersync.sync --dry-run       # scan + report, write nothing

Safe to interrupt and re-run: records are written to disk every few papers, and
already-processed files are skipped on the next run.
"""

from __future__ import annotations

import argparse
import datetime as dt
import sys
import time
from pathlib import Path

from . import extract, metadata, store
from .config import Config, load_config
from .sharelink import dropbox_sharelink


def _now_iso() -> str:
    return dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def build_record(pdf: Path, cfg: Config, rel: str, sha: str, stat) -> dict:
    """Run the full extraction pipeline for one PDF and return a record dict."""
    text = extract.text_first_pages(pdf, pages=2)
    meta = extract.embedded_metadata(pdf)
    ids = extract.find_identifiers(text, meta)

    record: dict = {
        "title": "",
        "authors": [],
        "journal": "",
        "published": "",
        "abstract": "",
        "doi": ids.get("doi", ""),
        "arxiv_id": ids.get("arxiv_id", ""),
        "extraction": "failed",
    }

    resolved: dict | None = None
    if ids.get("doi"):
        resolved = metadata.crossref_by_doi(ids["doi"], cfg.crossref_mailto)
        metadata.polite_sleep(0.3)
    if resolved is None and ids.get("arxiv_id"):
        resolved = metadata.arxiv_by_id(ids["arxiv_id"])
        metadata.polite_sleep(3.0)  # arXiv asks for 1 request / 3s
    if resolved is None:
        # Filenames in this folder are often the identifier itself.
        for cand_doi in extract.filename_doi_candidates(pdf.stem):
            resolved = metadata.crossref_by_doi(cand_doi, cfg.crossref_mailto)
            metadata.polite_sleep(0.3)
            if resolved:
                record["doi"] = cand_doi
                break
    if resolved is None:
        title = extract.guess_title(text, meta)
        if title:
            resolved = metadata.crossref_by_title(title, cfg.crossref_mailto)
            metadata.polite_sleep(0.3)
    if resolved is None and cfg.llm_enabled:
        resolved = metadata.llm_extract(text, cfg.anthropic_api_key, cfg.anthropic_model)

    if resolved:
        for k, v in resolved.items():
            if v:
                record[k] = v

    # If we still have no title, fall back to the guessed title / filename so the
    # entry is at least identifiable on the page.
    if not record["title"]:
        record["title"] = extract.guess_title(text, meta) or pdf.stem

    # Abstract fallback: pull it straight from the PDF text.
    if not record["abstract"]:
        record["abstract"] = extract.extract_abstract_from_text(text)

    record.update(
        {
            "id": sha[:16],
            "file": rel,
            "file_sha256": sha,
            "file_size": stat.st_size,
            "file_mtime": int(stat.st_mtime),
            "added_at": _now_iso(),
        }
    )
    _resolve_pdf_url(record, pdf, cfg)
    return record


def _resolve_pdf_url(record: dict, pdf: Path, cfg: Config) -> None:
    """Public link first (arXiv/DOI), Dropbox per-file link as fallback."""
    if record.get("arxiv_id"):
        record["pdf_url"] = f"https://arxiv.org/abs/{record['arxiv_id']}"
        record["pdf_url_kind"] = "arxiv"
        return
    if record.get("doi"):
        record["pdf_url"] = f"https://doi.org/{record['doi']}"
        record["pdf_url_kind"] = "doi"
        return
    if cfg.generate_dropbox_links:
        link = dropbox_sharelink(pdf)
        if link:
            record["pdf_url"] = link
            record["pdf_url_kind"] = "dropbox"
            return
    record["pdf_url"] = ""
    record["pdf_url_kind"] = "none"


def iter_pdfs(root: Path):
    for p in sorted(root.rglob("*.pdf")):
        if p.is_file() and not p.name.startswith("."):
            yield p


def run(cfg: Config, limit: int | None, since_days: int | None, dry_run: bool) -> int:
    records = store.load()
    by_path = store.index_by_path(records)
    by_hash = store.index_by_hash(records)

    cutoff = None
    if since_days is not None:
        cutoff = time.time() - since_days * 86400

    new_count = 0
    changed = False
    processed_since_save = 0

    for pdf in iter_pdfs(cfg.papers_dir):
        rel = str(pdf.relative_to(cfg.papers_dir))
        try:
            stat = pdf.stat()
        except OSError:
            continue
        if cutoff is not None and stat.st_mtime < cutoff:
            continue

        existing = by_path.get(rel)
        if (
            existing
            and existing.get("file_size") == stat.st_size
            and existing.get("file_mtime") == int(stat.st_mtime)
        ):
            continue  # unchanged, already recorded

        sha = store.sha256_file(pdf)

        # Same content under a (possibly) new path: update location, keep metadata.
        twin = by_hash.get(sha)
        if twin is not None and twin is not existing:
            twin["file"] = rel
            twin["file_size"] = stat.st_size
            twin["file_mtime"] = int(stat.st_mtime)
            changed = True
            by_path[rel] = twin
            continue

        if dry_run:
            print(f"NEW  {rel}")
            new_count += 1
            if limit and new_count >= limit:
                break
            continue

        print(f"[{new_count + 1}] processing {rel} ...", flush=True)
        record = build_record(pdf, cfg, rel, sha, stat)
        print(
            f"      -> {record['extraction']:14s} | {record['title'][:70]}",
            flush=True,
        )

        if existing is not None:
            records.remove(existing)
        records.append(record)
        by_path[rel] = record
        by_hash[sha] = record
        new_count += 1
        changed = True
        processed_since_save += 1

        if processed_since_save >= 5:
            store.save(records)
            processed_since_save = 0

        if limit and new_count >= limit:
            break

    if changed and not dry_run:
        store.save(records)

    verb = "would add" if dry_run else "added"
    print(f"\nDone: {verb} {new_count} record(s); {len(records)} total.")
    return new_count


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Sync Dropbox PDFs into references.json")
    ap.add_argument("--limit", type=int, default=None, help="max new papers to process")
    ap.add_argument("--since", type=int, default=None, help="only PDFs modified in last N days")
    ap.add_argument("--dry-run", action="store_true", help="report new PDFs, write nothing")
    args = ap.parse_args(argv)

    cfg = load_config()
    if not cfg.papers_dir.exists():
        print(f"papers_dir does not exist: {cfg.papers_dir}", file=sys.stderr)
        return 2
    run(cfg, args.limit, args.since, args.dry_run)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
