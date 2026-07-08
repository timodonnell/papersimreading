"""Backfill html_url for existing records (arXiv native HTML / PubMed Central).

Independent of pdf_url resolution, so it does not redo Dropbox share links.
Resumable: records already at the current HTML-schema version are skipped.

    python -m papersync.refresh_html
"""

from __future__ import annotations

from . import links, store


def main() -> int:
    records = store.load()
    todo = [r for r in records if r.get("html_v") != links.HTML_VERSION]
    print(f"{len(todo)} of {len(records)} records need HTML resolution", flush=True)

    found = 0
    for i, r in enumerate(todo, 1):
        links.resolve_html(r)
        if r.get("html_url"):
            found += 1
        if i % 25 == 0:
            store.save(records)
            print(f"  {i}/{len(todo)} | html links so far: {found}", flush=True)

    store.save(records)
    print(f"done: {sum(1 for r in records if r.get('html_url'))} records have an HTML link")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
