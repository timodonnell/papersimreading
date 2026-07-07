"""The reference database: a single JSON file committed to the repo.

data/references.json is a list of records. It is the source of truth for the
web page (which reads the same file client-side) and for detecting which PDFs
have already been processed.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
DATA_PATH = REPO_ROOT / "data" / "references.json"
# Files that were processed but are NOT papers (no DOI/arXiv) are remembered here
# so they are neither published nor re-processed every run. This file is
# git-ignored: non-paper filenames must never reach the public repo.
EXCLUDED_PATH = REPO_ROOT / ".papersync-excluded.json"


def sha256_file(path: Path, chunk: int = 1 << 20) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(chunk), b""):
            h.update(block)
    return h.hexdigest()


def load() -> list[dict]:
    if DATA_PATH.exists():
        return json.loads(DATA_PATH.read_text() or "[]")
    return []


def save(records: list[dict]) -> None:
    DATA_PATH.parent.mkdir(parents=True, exist_ok=True)
    # Sort newest-added first so the page and the diff are both readable.
    ordered = sorted(records, key=lambda r: r.get("added_at", ""), reverse=True)
    DATA_PATH.write_text(json.dumps(ordered, indent=2, ensure_ascii=False) + "\n")


def index_by_path(records: list[dict]) -> dict[str, dict]:
    return {r["file"]: r for r in records}


def index_by_hash(records: list[dict]) -> dict[str, dict]:
    return {r["file_sha256"]: r for r in records if r.get("file_sha256")}


def load_excluded() -> list[dict]:
    """Minimal stat records for processed-but-not-a-paper files (git-ignored)."""
    if EXCLUDED_PATH.exists():
        return json.loads(EXCLUDED_PATH.read_text() or "[]")
    return []


def save_excluded(entries: list[dict]) -> None:
    EXCLUDED_PATH.write_text(json.dumps(entries, indent=2, ensure_ascii=False) + "\n")
