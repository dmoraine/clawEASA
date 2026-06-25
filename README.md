# clawEASA

Query EASA Easy Access Rules — OpenClaw skill with hybrid retrieval.

## Product goal

Let users query EASA regulation and receive:
- exact references,
- short cited extracts,
- or a sourced English answer,

while preserving regulatory structure and minimizing hallucinations.

## Architecture

- **SQLite** for structured regulation data with full regulatory hierarchy
  (parts → subparts → sections → entries)
- **FTS5** with porter stemming for full-text search
- **FAISS** (IndexFlatIP, cosine similarity) for semantic vector search
- **sentence-transformers** (`BAAI/bge-small-en-v1.5`, 384 dims) for embeddings
- Hybrid retrieval merges exact lookup + FTS + vector search with configurable scoring
- EASA sources are downloaded as **ZIP archives** containing Office Open XML; extraction is automatic

## Corpus

| Source | Slug |
|--------|------|
| Easy Access Rules for Air Operations | `air-ops` |
| Easy Access Rules for Aircrew | `aircrew` |
| Easy Access Rules for Basic Regulation | `basic-regulation` |
| Occurrence Reporting Rule Book | `occurrence-reporting` |
| EASA FAQs (per domain) | `faq-*` |

## Quick start

```bash
# Install in development mode
pip install -e ".[dev]"

# Initialise SQLite database
claw-easa init

# Discover Easy Access Rules available on the EASA website
claw-easa ear-discover

# List built-in aliases for common sources
claw-easa ear-list

# Ingest a regulation source (downloads ZIP, extracts XML, parses)
# Note: the plain fetch is blocked by EASA's bot-challenge — see
# "Downloading sources" below for the --browser and --file workarounds.
claw-easa ingest fetch air-ops
claw-easa ingest parse air-ops

# Verify parser coverage against the source XML
claw-easa ingest diagnose air-ops

# Ingest all EASA FAQs (crawls every sub-domain)
claw-easa ingest faq-all

# Or ingest a single FAQ domain
claw-easa ingest faq air-operations

# Build search index
claw-easa index build

# List everything that has been ingested
claw-easa sources-list              # all sources
claw-easa sources-list --type ear   # only EARs
claw-easa sources-list --type faq   # only FAQs

# Query
claw-easa lookup ORO.FTL.110
claw-easa refs "split duty"
claw-easa ask "What are the FTL operator responsibilities?"

# Source-scoped search (restrict to a specific source)
claw-easa refs "crew fatigue" --slug occurrence-reporting
claw-easa snippets "crew fatigue" --slug occurrence-reporting
```

## Downloading sources (EASA bot-challenge)

The EASA website is fronted by a Fastly JavaScript bot-challenge (cookies
`_fs_ch_*`), so the plain HTTP `ingest fetch` **cannot** download files — a
`requests`-style client cannot execute the challenge script, whatever the
User-Agent. The fetcher detects the challenge page and fails with a clear
message rather than saving it. Three workarounds, in order of preference:

**1. Browser download + `parse --file` (recommended — works for agents and humans)**

A real browser solves the challenge. An agent driving a browser (or you, by
hand) opens the document-library page, clicks the **XML** download link,
saves the file, then ingests it locally — no network needed at parse time:

```bash
claw-easa ingest parse air-ops --file ~/Downloads/EAR-for-Air-Operations.zip
```

Find the page for a slug with `claw-easa ear-discover`, or browse
<https://www.easa.europa.eu/en/document-library/easy-access-rules>.

**2. Headless browser backend (`fetch --browser`, fully automated)**

An opt-in Playwright backend launches headless Chromium, clears the
challenge, and downloads the current file:

```bash
pip install 'claw-easa[browser]'
playwright install chromium
claw-easa ingest fetch air-ops --browser
claw-easa ingest parse air-ops
```

Always fetches the latest revision without a human in the loop. Caveat:
aggressive bot-management can occasionally fingerprint headless browsers, so
it is best-effort — fall back to option 1 if a run is challenged.

**3. EUR-Lex for the underlying regulation only (not the EAR)**

The raw legal act behind a rule (e.g. Air-OPS = Regulation (EU) No 965/2012,
CELEX `32012R0965`) is on EUR-Lex with no bot-challenge, but it is the
Implementing Rule **only** — it does not include EASA's consolidated AMC/GM
or the Easy Access Rules structure this parser expects. Use it as a
reference for the IR text, not as a drop-in EAR source.

## Configuration

Settings are resolved in order: `config.yaml` → environment variables → defaults.

Key environment variables:
- `CLAW_EASA_DATA_DIR` — data directory (default: `data/`)
- `CLAW_EASA_DB_FILE` — SQLite filename (default: `claw_easa.db`)
- `CLAW_EASA_EMBEDDING_MODEL` — embedding model (default: `BAAI/bge-small-en-v1.5`)

## Repository layout

```text
clawEASA/
├── src/claw_easa/
│   ├── cli/             # Click CLI commands
│   ├── db/              # SQLite wrapper, schema, migrations
│   ├── ingest/          # Download, parse, persist EASA sources
│   ├── retrieval/       # Search: exact, FTS, FAISS, hybrid
│   └── answering/       # Answer formatting
├── tests/
├── docs/
├── skill/
│   └── claw-easa/       # OpenClaw AgentSkill package
├── data/                # Runtime data (SQLite + FAISS), gitignored
├── manifest.json
└── pyproject.toml
```

## OpenClaw skill packaging

This repository is a normal Python project **and** contains an OpenClaw skill package.

- Development source of truth: repository root
- Installable AgentSkill package: `skill/claw-easa/`
- Local installation is guarded against symlink/destination paths that resolve back into the source repository
- Installing the skill package alone is **not enough**; the Python runtime and CLI must also be installed

### Recommended GitHub → OpenClaw install flow

The bootstrap installs a **CPU-only** torch build by default, which is safer for typical OpenClaw hosts and avoids accidentally downloading large CUDA wheels.

```bash
git clone https://github.com/dmoraine/clawEASA.git
cd clawEASA
./scripts/bootstrap-local-runtime.sh
./scripts/install-openclaw-skill.sh
./scripts/check-openclaw-runtime.sh
```

### Install only the skill package into OpenClaw

```bash
./scripts/install-openclaw-skill.sh
```

Optional test/install override:

```bash
OPENCLAW_SKILL_DST=/tmp/claw-easa-skill ./scripts/install-openclaw-skill.sh
```

Or manually:

```bash
mkdir -p ~/.openclaw/workspace/skills/claw-easa
rsync -a --delete skill/claw-easa/ ~/.openclaw/workspace/skills/claw-easa/
```

See also: `docs/openclaw-install.md`

## Audit output workflow

The audit workflow uses a canonical JSON report as the source of truth, stores it locally in SQLite, and exports it to CSV or XLSX when needed.

See also:
- `docs/audit-finding-versioning-plan.md` for the next-step design of finding versioning, lookup by ID, and discussion tracking.
- `claw-easa audit finding get <FINDING_ID>` to inspect the latest stored revision for a finding.
- `claw-easa audit finding history <FINDING_ID>` to inspect the revision history.

```bash
# Validate a canonical JSON report
claw-easa audit validate path/to/report.json

# Import the report into the local SQLite store
claw-easa audit import path/to/report.json

# Export a stored report
claw-easa audit export --report-id AUD-20260422-0001 --format xlsx --output output/report.xlsx
claw-easa audit export --report-id AUD-20260422-0001 --format csv --output output/report.csv
claw-easa audit export --report-id AUD-20260422-0001 --format json --output output/report.json
```

## Running tests

```bash
./scripts/bootstrap-local-runtime.sh
. .venv/bin/activate
pytest
```
