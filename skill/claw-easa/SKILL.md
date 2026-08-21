---
name: claw-easa
description: Query EASA Easy Access Rules locally with exact reference lookup, full-text search, and semantic search. Use when answering EASA regulatory questions, retrieving article/reference wording, checking applicability, or finding AMC, GM, and FAQ material from the local claw-easa index.
---

Use the local `claw-easa` CLI from this repository.

Preferred commands:
- `claw-easa lookup <REF>` for exact references such as `ORO.FTL.110`
  - `--json` for the whole provision plus the provenance to cite it (corpus,
    EASA revision, page URL, part/subpart, regulation). Use it before quoting:
    the default output is a five-line extract.
- `claw-easa refs "<query>"` for reference-oriented search
  - add `--json` to get the same results as JSON, each with the source it can
    be cited from: `url` (the entry's own URL, else the document's, else
    `null`), `url_kind`, `locator`, `slug` and `revision`
- `claw-easa snippets "<query>"` for cited text excerpts
- `claw-easa hybrid "<query>"` for mixed lexical + semantic retrieval
- `claw-easa ask "<question>"` for routed natural-language queries
- `claw-easa status` to verify corpus/index availability

Source-scoped search — use `--slug <source>` to restrict results to a specific
source document. This is important when a broad query returns too many results
from different sources, or when you know which source is most relevant:
- `claw-easa lookup ORO.FTL.110 --slug air-ops`
- `claw-easa refs "crew fatigue" --slug occurrence-reporting`
- `claw-easa snippets "crew fatigue" --slug occurrence-reporting`
- `claw-easa hybrid "fatigue reporting" --slug occurrence-reporting`
Use `claw-easa sources-list` to see available slugs.
- `claw-easa sources-list` to list ingested EARs and FAQ domains (supports `--type ear|faq`)
- `claw-easa ear-discover` to list Easy Access Rules available on the EASA website
- `claw-easa ear-list` to list built-in known source aliases
- `claw-easa ingest fetch <slug>` to download a source (use `--url` to bypass catalog)
  - The normal HTTP fetch currently works and should be tried first.
  - If EASA conditionally returns its JavaScript bot-challenge, use
    `claw-easa ingest fetch <slug> --browser` (needs optional `playwright`) or
    feed a browser-downloaded file to
    `claw-easa ingest parse <slug> --file <path>`.
- `claw-easa ingest parse <slug>` to parse a fetched source into the database
  (use `--file <path>` to ingest a manually-downloaded ZIP/XML)
- `claw-easa ingest diagnose <slug>` to verify parser coverage against the source XML
- `claw-easa ingest faq-all` to ingest all EASA FAQs (crawls every sub-domain)
- `claw-easa ingest faq <domain>` to ingest FAQs for a specific domain
- `claw-easa ingest faq-discover` to list available FAQ domains on the EASA website

Answering rules:
- Prefer exact lookup when the user gives a regulation reference.
- Quote the retrieved text or excerpt before paraphrasing.
- Distinguish regulation text from AMC/GM/FAQ material.
- Link a citation only to the `url` a result carries. When it is `null`, cite
  the slug, the locator and the revision instead — never build a URL out of a
  reference.
- If retrieval is empty or ambiguous, say so explicitly instead of inferring.
- When the question targets a specific regulation domain (e.g. occurrence
  reporting, aircrew, air operations), use `--slug` to scope the search.
  This dramatically improves recall for items buried in long annexes.

Read these files only when needed:
- `references/usage.md` for repository-aware usage and installation notes
- `references/runtime-setup.md` when the local `claw-easa` CLI may not be installed yet
- `references/easa-answering.md` for answer format and evidence rules

Local install notes:
- The skill package lives under `skill/claw-easa/` in the repository.
- For OpenClaw local installation, copy this folder into `~/.openclaw/workspace/skills/claw-easa/`.
- Avoid symlinks that resolve outside the OpenClaw workspace.
