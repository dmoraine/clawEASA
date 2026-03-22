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

## Running tests

```bash
./scripts/bootstrap-local-runtime.sh
. .venv/bin/activate
pytest
```
