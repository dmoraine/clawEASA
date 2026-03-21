# clawEASA — Reconstruction handoff after local repository destruction

Date: 2026-03-21

## Executive summary

The local repository `/home/openclaw/dev/clawEASA` was accidentally destroyed during an OpenClaw local-skill installation test.

The root cause was **a workspace skill symlink pointing back to the source repository**, combined with an `rsync --delete`-style install flow. Instead of syncing files into a separate install target, the install operation resolved through the symlink and deleted files inside the source repository itself.

A partial recovery has already been performed. The repo now contains:
- a valid git repository linked to `https://github.com/dmoraine/clawEASA.git`
- a rebuilt project skeleton
- a recovered `normalize.py`
- reconstructed metadata/docs/skill packaging files
- a safety note describing the incident

However, **most of the original implementation is still missing** and must be reconstructed.

---

## What exactly happened

### Initial intent
The goal was to test real OpenClaw skill integration using a local install flow.

The plan was:
1. prepare the project as an OpenClaw-compatible skill
2. install it into `~/.openclaw/workspace/skills/claw-easa`
3. verify detection and usage

### Dangerous pre-existing condition
There was already a symlink in place:

```bash
/home/openclaw/.openclaw/workspace/skills/claw-easa -> /home/openclaw/dev/clawEASA
```

This meant the OpenClaw workspace skill path did **not** point to an isolated installed copy. It pointed directly to the development repository.

### Destructive action
An install attempt used an `rsync --delete` flow targeting the workspace skill path.

Because that path was a symlink into the source repository, the sync operation effectively targeted the source tree itself.

This caused mass deletion of the repository contents.

### Why this was catastrophic
The install destination and the source repository were, through symlink resolution, the same filesystem target.

So the intended flow:

```text
repo source -> copy into workspace skill dir
```

became effectively:

```text
repo source -> sync into itself via symlinked alias with --delete
```

That destroyed the working tree.

---

## Root cause analysis

### Primary root cause
A symlink was used for local OpenClaw skill installation:

```bash
~/.openclaw/workspace/skills/claw-easa -> /home/openclaw/dev/clawEASA
```

This is unsafe for any install/update script that:
- deletes before copy
- uses `rsync --delete`
- assumes source and destination are separate

### Secondary root cause
The install logic did not defensively check:
- whether destination was a symlink
- whether destination resolved inside the source tree
- whether source and destination had the same `realpath`

### Contributing factor
The repository had not yet been pushed to GitHub, so there was no remote history to restore from.

---

## Current repository status

Current path:

```bash
/home/openclaw/dev/clawEASA
```

Current remote:

```bash
origin https://github.com/dmoraine/clawEASA.git
```

Current local status is effectively a newly reconstructed working tree with uncommitted files.

### Verified current minimal runtime proof
This command currently works:

```bash
cd /home/openclaw/dev/clawEASA
PYTHONPATH=src python3 -m claw_easa.cli status
```

Output observed:

```text
claw-easa recovery scaffold: CLI restored, full command set pending reimplementation
```

This proves only that a minimal Python package scaffold exists again.
It does **not** prove that the original product functionality has been restored.

---

## Files currently present

The following files are present now:

```text
.gitignore
README.md
manifest.json
pyproject.toml
scripts/install-openclaw-skill.sh
skill/claw-easa/SKILL.md
skill/claw-easa/references/easa-answering.md
skill/claw-easa/references/usage.md
src/claw_easa/__init__.py
src/claw_easa/cli/__init__.py
src/claw_easa/cli/__main__.py
src/claw_easa/db/__init__.py
src/claw_easa/ingest/__init__.py
src/claw_easa/ingest/normalize.py
src/claw_easa/ingest/parser.py
src/claw_easa/retrieval/__init__.py
src/claw_easa/answering/__init__.py
docs/recovery-notes-2026-03-21.md
```

---

## What was genuinely recovered vs reconstructed

### Genuinely recovered / evidenced from prior reads or local traces
These were restored from concrete evidence previously seen in-session or found in traces:

- `.gitignore`
- `pyproject.toml`
- `README.md`
- `manifest.json`
- `src/claw_easa/__init__.py`
- `src/claw_easa/cli/__main__.py`
- `src/claw_easa/ingest/normalize.py`
- knowledge that a skill package layout under `skill/claw-easa/` was desired

### Reconstructed placeholders / inferred scaffolding
These were recreated to restore shape, not original implementation:

- `src/claw_easa/cli/__init__.py`
- `src/claw_easa/db/__init__.py`
- `src/claw_easa/ingest/parser.py`
- `src/claw_easa/retrieval/__init__.py`
- `src/claw_easa/answering/__init__.py`
- `skill/claw-easa/SKILL.md`
- `skill/claw-easa/references/*`
- `scripts/install-openclaw-skill.sh`
- `docs/recovery-notes-2026-03-21.md`

These are scaffolding aids, not recovery of the missing system.

---

## Important traces still available for reconstruction

The following local traces were found and may still help recover structure, names, prompts, or snippets:

### Cursor project traces
```bash
/home/openclaw/.cursor/projects/home-openclaw-dev-clawEASA/
```

Relevant contents seen:
- `agent-transcripts/*.jsonl`
- `agent-tools/*.txt`
- `worker.log`
- `repo.json`

### Strange workspace mirror remnants
These were found and may contain fragments:

```bash
/home/openclaw/.openclaw/workspace/\pll/vhome/openclaw/dev/clawEASA/
/home/openclaw/.openclaw/workspace/:•rustc/home/openclaw/dev/clawEASA/
```

Observed useful fragments:
- `src/claw_easa/ingest/normalize.py`
- `manifest.json`

### Memory hints
Project-level historical memory exists mentioning major architecture decisions and progress:
- `memory/2026-03-11.md`
- `MEMORY.md`

These are useful for product intent but not sufficient to recover code.

---

## Missing areas to reconstruct

The following areas are still largely or entirely missing.

### 1. Database layer
Missing or incomplete:
- `src/claw_easa/db/core.py`
- `src/claw_easa/db/sqlite.py`
- `src/claw_easa/db/sql.py`
- `src/claw_easa/db/migrations.py`
- `src/claw_easa/db/schema.sql`

Need to restore:
- SQLite schema and connection layer
- migration support
- repository/data access helpers
- compatibility with current ingestion/retrieval assumptions

### 2. Retrieval layer
Missing or incomplete:
- `src/claw_easa/retrieval/exact.py`
- `src/claw_easa/retrieval/hybrid.py`
- `src/claw_easa/retrieval/indexing.py`
- `src/claw_easa/retrieval/vector.py`
- `src/claw_easa/retrieval/faiss_store.py`
- `src/claw_easa/retrieval/snippets.py`
- `src/claw_easa/retrieval/queries.py`
- `src/claw_easa/retrieval/router.py`
- `src/claw_easa/retrieval/service.py`
- related formatting/rewrite/profile modules

Need to restore:
- exact reference lookup
- lexical search / FTS lookup
- FAISS embedding index usage
- hybrid ranking
- snippet extraction
- routed query/ask mode

### 3. Ingestion/parser system
Still incomplete despite recovered `normalize.py`:
- source catalog/discovery
- fetch/download logic
- XML parsing logic
- FAQ parsing services
- persistence orchestration
- diagnostics/anomaly handling

Likely missing files include:
- `src/claw_easa/ingest/parser.py` (currently placeholder)
- `src/claw_easa/ingest/service.py`
- `src/claw_easa/ingest/sources.py`
- `src/claw_easa/ingest/catalog.py`
- `src/claw_easa/ingest/repository.py`
- FAQ-related modules

### 4. Answering layer
Missing:
- answer formatting
- evidence/citation handling
- strict ref-only mode implementation
- natural-language answer mode

### 5. CLI surface
Currently only placeholder `status` exists.
Need to restore command set such as:
- `status`
- `init`
- `sources-list`
- `sources-discover`
- `ingest fetch <slug>`
- `ingest parse <slug>`
- `index build`
- `index rebuild`
- `lookup <ref>`
- `refs <query>`
- `snippets <query>`
- `hybrid <query>`
- `ask <query>`

### 6. Tests
All tests appear missing.
Need to reconstruct:
- parser tests
- retrieval tests
- routing tests
- FAQ tests
- benchmark/smoke tests
- configuration tests

### 7. Migrations
Missing migration files and migration state logic.
Need to restore all SQL migrations and ensure they match the actual runtime schema assumptions.

### 8. Documentation
Only minimal docs remain. Must reconstruct:
- design docs
- implementation plan
- benchmark notes
- calibration notes
- FAQ ranking policy
- specialist prompt/spec if still relevant

---

## Recommended reconstruction strategy

### Phase 0 — freeze and protect
Before more work:
1. commit current recovery state immediately
2. push to GitHub immediately
3. never use a workspace symlink pointing to the source repo again

Recommended local install rule forever:
- source repo in `/home/openclaw/dev/clawEASA`
- installable skill package in `skill/claw-easa/`
- local OpenClaw install by **copy**, never symlink

### Phase 1 — recover from traces before rewriting
Search all local traces for recoverable source or exact file names:
- Cursor transcripts
- Cursor tool outputs
- weird workspace remnants
- shell history if available
- any backups or rsync snapshots

Goal:
- recover as much exact code as possible before hand-reimplementation

### Phase 2 — restore runnable package shape
Target minimal real package with:
- functioning CLI group
- working `status`
- working `lookup`
- valid DB layer
- valid package install

Goal:
- repo can install and run basic commands again

### Phase 3 — restore ingestion pipeline
Priority:
1. source discovery/listing
2. fetch
3. parse
4. persist to SQLite
5. rebuild local data/index

Goal:
- ingest Air Ops, Aircrew, Basic Regulation again

### Phase 4 — restore retrieval core
Priority:
1. exact lookup
2. snippets
3. lexical/FTS search
4. vector search
5. hybrid ranking

Goal:
- produce reliable answers for exact refs and topic queries

### Phase 5 — restore ask/answering and strict modes
Need to support:
- exact quote / raw text retrieval
- reference list only
- short cited explanatory answer
- separation of IR / AMC / GM / FAQ

### Phase 6 — restore tests and docs
Rebuild tests once implementation stabilizes.

---

## Immediate actionable TODO list for the next agent

### Safety and repo hygiene
- [ ] Commit current reconstructed state immediately
- [ ] Push to `origin main`
- [ ] Verify GitHub now contains at least the recovered scaffold
- [ ] Confirm no symlink exists at `~/.openclaw/workspace/skills/claw-easa`

### Trace mining
- [ ] Search `/home/openclaw/.cursor/projects/home-openclaw-dev-clawEASA/agent-transcripts/`
- [ ] Search `/home/openclaw/.cursor/projects/home-openclaw-dev-clawEASA/agent-tools/`
- [ ] Search `/home/openclaw/.openclaw/workspace/\pll/vhome/openclaw/dev/clawEASA/`
- [ ] Search `/home/openclaw/.openclaw/workspace/:•rustc/home/openclaw/dev/clawEASA/`
- [ ] Extract any exact file content that can be reconstructed from logs

### Core package reconstruction
- [ ] Rebuild full `src/claw_easa/db/`
- [ ] Rebuild full `src/claw_easa/retrieval/`
- [ ] Rebuild full `src/claw_easa/answering/`
- [ ] Rebuild real `src/claw_easa/ingest/parser.py`
- [ ] Rebuild CLI command surface

### Product functionality
- [ ] Restore `lookup ORO.FTL.110`
- [ ] Restore `refs "split duty"`
- [ ] Restore `snippets` and/or `hybrid`
- [ ] Restore indexing flow
- [ ] Restore FAQ-capable structure

### Packaging and OpenClaw integration
- [ ] Keep AgentSkill under `skill/claw-easa/`
- [ ] Verify `SKILL.md` frontmatter is valid
- [ ] Make `scripts/install-openclaw-skill.sh` the only recommended local install path
- [ ] Test local install by copy into `~/.openclaw/workspace/skills/claw-easa/`
- [ ] Do not use symlinks for installation

### Testing
- [ ] Recreate a minimal smoke test suite first
- [ ] Re-add parser tests
- [ ] Re-add retrieval tests
- [ ] Re-add exact-ref regression tests
- [ ] Add a guard test for safe local skill install assumptions if relevant

---

## Known architectural intent that should be preserved

From prior project state and memory:
- local EASA Easy Access Rules querying tool
- OpenClaw-compatible skill packaging
- exact reference lookup + full-text + semantic search
- strong distinction between regulatory text and explanatory material
- strict no-LLM / ref-only modes are important
- FAQ support was considered important for future extension
- local data/index storage was already moved toward SQLite + FAISS in the later state

Do not blindly revert to an earlier PostgreSQL-only design unless there is a deliberate reason.

---

## Safe local install pattern going forward

Correct local skill install pattern:

```bash
mkdir -p ~/.openclaw/workspace/skills/claw-easa
rsync -a --delete /home/openclaw/dev/clawEASA/skill/claw-easa/ ~/.openclaw/workspace/skills/claw-easa/
```

Do **not** do this:

```bash
ln -s /home/openclaw/dev/clawEASA ~/.openclaw/workspace/skills/claw-easa
```

Do **not** run any install/update script that targets a symlinked workspace skill path without first verifying `realpath` separation.

---

## Suggested first commands for the SSH reconstruction agent

```bash
cd /home/openclaw/dev/clawEASA
git status
find . -maxdepth 4 -type f | sort

# inspect traces
find /home/openclaw/.cursor/projects/home-openclaw-dev-clawEASA -type f | sort
find '/home/openclaw/.openclaw/workspace/\pll/vhome/openclaw/dev/clawEASA' -type f | sort 2>/dev/null
find '/home/openclaw/.openclaw/workspace/:•rustc/home/openclaw/dev/clawEASA' -type f | sort 2>/dev/null
```

Then:

```bash
git add .
git commit -m "Recover clawEASA scaffold after accidental local repo destruction"
git push -u origin main
```

Then begin structured reconstruction.

---

## Final note

The current repo is **recoverable as a project**, but **not yet recovered as a product**.

What exists now is a stabilized, documented, non-symlinked base with enough surviving context to let another agent rebuild methodically without losing the story of what happened.
