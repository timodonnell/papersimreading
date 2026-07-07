# papersimreading

A tiny reference manager backed by a Dropbox folder of PDFs and published as a
static web page via GitHub Pages.

**Live page:** https://timodonnell.github.io/papersimreading/

A scheduled job scans a Dropbox folder for new PDFs, extracts each paper's
metadata (title, authors, journal, publication date, abstract, and a link to the
PDF) from public sources, and appends a record to [`data/references.json`](data/references.json).
The web page ([`index.html`](index.html)) renders that JSON — searchable and
sortable, no build step.

## How it works

```
Dropbox folder (synced locally)
        │  scan for *.pdf not yet recorded
        ▼
  papersync.sync ──► extract DOI / arXiv id / title from the PDF
        │            resolve metadata:
        │              1. Crossref by DOI
        │              2. arXiv API by arXiv id
        │              3. Crossref by filename-derived DOI  (bioRxiv/Nature ids)
        │              4. Crossref by title
        │              5. LLM over first-page text  (optional, needs API key)
        │            link: public arXiv/DOI first, else a Dropbox share link
        ▼
  data/references.json ──► git commit & push ──► GitHub Pages renders index.html
```

The Dropbox folder is synced to this machine by the Dropbox desktop client, so
the job reads the local copy directly — no scraping, no API auth, no bulk
downloads. **The shared-folder URL and folder path are never committed**; they
live in a local config file.

## Setup

1. Install dependencies (poppler provides `pdftotext`):
   ```bash
   pip install -r requirements.txt
   sudo apt-get install poppler-utils    # if pdftotext is missing
   ```
2. Create the local config (outside the repo):
   ```bash
   mkdir -p ~/.config/papersimreading
   cat > ~/.config/papersimreading/config.json <<'JSON'
   {
     "papers_dir": "/home/you/Dropbox/your-folder",
     "crossref_mailto": "you@example.com",
     "anthropic_api_key": "",
     "generate_dropbox_links": true
   }
   JSON
   chmod 600 ~/.config/papersimreading/config.json
   ```
   Set `anthropic_api_key` to enable the LLM fallback for PDFs that no public
   source can identify. Leave it empty to stay API-only (no token cost).

## Usage

```bash
python3 -m papersync.sync              # process every new PDF
python3 -m papersync.sync --limit 20   # stop after 20 new papers
python3 -m papersync.sync --since 90   # only PDFs modified in the last 90 days
python3 -m papersync.sync --dry-run    # list new PDFs, write nothing
```

The run is incremental and resumable: records are flushed to disk every few
papers, and already-processed files are skipped next time. Files are tracked by
content hash, so renaming or moving a PDF within the folder does not re-process
it.

## Scheduling

`scripts/run.sh` runs the sync and, if `references.json` changed, commits and
pushes. Install it as a daily cron job:

```bash
scripts/install-cron.sh                       # daily at 07:30
CRON_SCHEDULE="0 * * * *" scripts/install-cron.sh   # hourly
```

Because the PDFs live in this machine's local Dropbox sync, the job must run on
this machine (not a cloud agent). Logs go to `papersimreading.log` (git-ignored).

## Layout

| Path | Purpose |
|------|---------|
| `papersync/` | the sync pipeline (config, PDF extraction, metadata lookups, store) |
| `data/references.json` | the reference database (source of truth for the page) |
| `index.html` | the GitHub Pages site, renders `references.json` client-side |
| `scripts/run.sh` | cron entry point: sync + commit + push |
| `scripts/install-cron.sh` | install/refresh the cron entry |
