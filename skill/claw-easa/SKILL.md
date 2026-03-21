---
name: claw-easa
description: Query EASA Easy Access Rules locally with exact reference lookup, full-text search, and semantic search. Use when answering EASA regulatory questions, retrieving article/reference wording, checking applicability, or finding AMC, GM, and FAQ material from the local claw-easa index.
---

Use the local `claw-easa` CLI from this repository.

Preferred commands:
- `claw-easa lookup <REF>` for exact references such as `ORO.FTL.110`
- `claw-easa refs "<query>"` for reference-oriented search
- `claw-easa snippets "<query>"` for cited text excerpts
- `claw-easa hybrid "<query>"` for mixed lexical + semantic retrieval
- `claw-easa ask "<question>"` for routed natural-language queries
- `claw-easa status` to verify corpus/index availability
- `claw-easa ear-discover` to list Easy Access Rules available on the EASA website
- `claw-easa ear-list` to list built-in known sources

Answering rules:
- Prefer exact lookup when the user gives a regulation reference.
- Quote the retrieved text or excerpt before paraphrasing.
- Distinguish regulation text from AMC/GM/FAQ material.
- If retrieval is empty or ambiguous, say so explicitly instead of inferring.

Read these files only when needed:
- `references/usage.md` for repository-aware usage and installation notes
- `references/easa-answering.md` for answer format and evidence rules

Local install notes:
- The skill package lives under `skill/claw-easa/` in the repository.
- For OpenClaw local installation, copy this folder into `~/.openclaw/workspace/skills/claw-easa/`.
- Avoid symlinks that resolve outside the OpenClaw workspace.
