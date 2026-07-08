"""One-off maintenance over the existing dataset.

  1. Reprocess every crossref-title match through the current pipeline. With the
     expanded filename->DOI heuristics and the stricter title matcher, correct
     papers get authoritative metadata (e.g. Science/Annual-Review/PNAS PDFs whose
     filename is the DOI) and genuine non-papers resolve to nothing and are
     excluded. This repairs earlier false-positive title matches.
  2. Backfill abstracts for records that have a DOI but no abstract.

Resumable and safe to re-run.

    python -m papersync.maintain
"""

from __future__ import annotations

from . import metadata, store
from .config import load_config
from .sync import build_record


def main() -> int:
    cfg = load_config()
    records = store.load()
    excluded = store.load_excluded()
    excl_paths = {e["file"] for e in excluded}

    # --- 1. Reprocess title matches through the improved pipeline --------------
    title_recs = [r for r in records if r.get("extraction") == "crossref-title"]
    print(f"reprocessing {len(title_recs)} crossref-title records ...", flush=True)

    result: list[dict] = []
    replaced = dropped = 0
    for r in records:
        if r.get("extraction") != "crossref-title":
            result.append(r)
            continue
        pdf = cfg.papers_dir / r["file"]
        if not pdf.exists():
            result.append(r)
            continue
        try:
            st = pdf.stat()
        except OSError:
            result.append(r)
            continue
        sha = r.get("file_sha256") or store.sha256_file(pdf)
        new = build_record(pdf, cfg, r["file"], sha, st)
        if new is None:
            # No public identifier once mis-matches are removed: not a paper.
            if r["file"] not in excl_paths:
                excluded.append({"file": r["file"], "file_sha256": sha,
                                 "file_size": st.st_size, "file_mtime": int(st.st_mtime)})
                excl_paths.add(r["file"])
            dropped += 1
            print(f"  EXCLUDE {r['file']}", flush=True)
        else:
            new["added_at"] = r.get("added_at", new["added_at"])  # keep original
            if new.get("extraction") != "crossref-title" or new.get("title") != r.get("title"):
                print(f"  FIX     {r['file']}\n          now: {new['title'][:70]}", flush=True)
            result.append(new)
            replaced += 1

    store.save_excluded(excluded)
    records = result
    store.save(records)
    print(f"reprocessed: {replaced} kept/fixed, {dropped} excluded; "
          f"{len(records)} papers remain", flush=True)

    # --- 2. Backfill abstracts -------------------------------------------------
    need = [r for r in records if r.get("doi") and not r.get("abstract")]
    print(f"backfilling abstracts for {len(need)} records ...", flush=True)
    filled = 0
    for i, r in enumerate(need, 1):
        abs_text = metadata.abstract_from_apis(r["doi"])
        metadata.polite_sleep(0.2)
        if abs_text:
            r["abstract"] = abs_text
            filled += 1
        if i % 25 == 0:
            store.save(records)
            print(f"  {i}/{len(need)} | filled {filled}", flush=True)
    store.save(records)
    print(f"filled {filled} abstracts; "
          f"{sum(1 for r in records if r.get('abstract'))}/{len(records)} now have one")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
