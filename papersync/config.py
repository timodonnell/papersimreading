"""Configuration loading.

All machine-specific and secret values (the Dropbox folder path, the shared-link
URL, the optional Anthropic API key) live in a JSON file OUTSIDE the repo so they
never get committed:

    ~/.config/papersimreading/config.json

Environment variables override individual keys, which is handy for cron:
    PAPERS_DIR, ANTHROPIC_API_KEY, CROSSREF_MAILTO
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path

CONFIG_PATH = Path(
    os.environ.get(
        "PAPERSIMREADING_CONFIG",
        os.path.expanduser("~/.config/papersimreading/config.json"),
    )
)


@dataclass
class Config:
    papers_dir: Path
    crossref_mailto: str = "anonymous@example.com"
    anthropic_api_key: str = ""
    anthropic_model: str = "claude-fable-5"
    generate_dropbox_links: bool = True
    # share_folder_url is loaded but never written to the repo.
    share_folder_url: str = ""

    @property
    def llm_enabled(self) -> bool:
        return bool(self.anthropic_api_key)


def load_config() -> Config:
    data: dict = {}
    if CONFIG_PATH.exists():
        data = json.loads(CONFIG_PATH.read_text())

    papers_dir = os.environ.get("PAPERS_DIR") or data.get("papers_dir")
    if not papers_dir:
        raise SystemExit(
            f"No papers_dir configured. Create {CONFIG_PATH} with a "
            '{"papers_dir": "/path/to/Dropbox/folder"} entry, or set PAPERS_DIR.'
        )

    return Config(
        papers_dir=Path(os.path.expanduser(papers_dir)),
        crossref_mailto=os.environ.get("CROSSREF_MAILTO")
        or data.get("crossref_mailto", "anonymous@example.com"),
        anthropic_api_key=os.environ.get("ANTHROPIC_API_KEY")
        or data.get("anthropic_api_key", ""),
        anthropic_model=data.get("anthropic_model", "claude-fable-5"),
        generate_dropbox_links=data.get("generate_dropbox_links", True),
        share_folder_url=data.get("share_folder_url", ""),
    )
