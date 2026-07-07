"""Re-resolve pdf_url / doi_url for existing records under the current policy.

Open-access and arXiv papers link to the public copy; paywalled papers link to
the user's Dropbox copy. Resumable: records already at the current link-schema
version are skipped, so it is safe to interrupt and re-run.

    python -m papersync.refresh_links
"""

from __future__ import annotations

from collections import Counter

from . import links, store
from .config import load_config


def main() -> int:
    cfg = load_config()
    records = store.load()
    todo = [r for r in records if r.get("links_v") != links.LINKS_VERSION]
    print(f"{len(todo)} of {len(records)} records need a link refresh", flush=True)

    for i, r in enumerate(todo, 1):
        pdf = cfg.papers_dir / r["file"]
        links.resolve_links(r, pdf if pdf.exists() else None, cfg)
        if i % 20 == 0:
            store.save(records)
            print(f"  {i}/{len(todo)} | last kind={r['pdf_url_kind']}", flush=True)

    store.save(records)
    print("link kinds:", dict(Counter(r["pdf_url_kind"] for r in records)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
