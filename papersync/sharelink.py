"""Generate a per-file Dropbox share link via the local `dropbox` CLI.

Used only as a fallback when a paper has no public arXiv/DOI URL. Requires the
Dropbox desktop client to be running and the file to be inside the synced
Dropbox folder.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

_URL_RE = re.compile(r"https://www\.dropbox\.com/\S+")


def dropbox_sharelink(path: Path, timeout: int = 120) -> str:
    try:
        out = subprocess.run(
            ["dropbox", "sharelink", str(path)],
            capture_output=True,
            timeout=timeout,
        )
    except (subprocess.SubprocessError, FileNotFoundError):
        return ""
    m = _URL_RE.search(out.stdout.decode("utf-8", errors="replace"))
    return m.group(0) if m else ""
