# FAQ duplicate grouping and stable FAQ identifiers — S0 investigation and design

Status: S0 deliverable (investigation + design, no production code). Branch `feature/faq-dedup-stable-ids`, base `origin/main` b9687bd.
Locked decisions (Didier, brief of 2026-11-26) are quoted as **D1–D9** and are not re-opened here. Everything else is a **proposal with a default**; the S1–S3 chain follows these defaults without a human stop, and Didier reviews this note before any real-database migration (Gate 3).

## 0. How to reproduce every figure

Data: read-only snapshot taken 2026-11-26 from the real data dir (`SNAP=/home/openclaw/.cache/claw-easa-faq-dedup/snapshot-20261126`, 7 630 entries, `sha256sum -c SHA256SUMS` OK before and after all measurements). Never run anything but `--immutable` on it (a plain `mode=ro` open of a WAL-mode file creates `-shm`/`-wal` next to it); anything that opens it through `Database` (CLI) needs a **copy** (`cp -a` of `claw_easa.db` and `claw_easa.faiss` to `~/.cache/claw-easa-faq-dedup/work-*`).

```
# sections 1-9, 11 (stdlib only, ~1 s, deterministic; two runs are byte-identical)
python scripts/faq_dup_investigation.py --data-dir $SNAP --immutable \
  --focus-domain faq-oroftl --focus-domain faq-part-oro --focus-domain faq-air-operations \
  --ref-prefix ORO.FTL \
  --regression-ref FAQ-oroftl-028 --regression-ref FAQ-air-operations-057 --regression-ref FAQ-part-oro-036

# section 10, over-fetch feasibility (needs the cached bge-small model; HF_HUB_OFFLINE is set by the script; run on a COPY)
python scripts/faq_dup_investigation.py --data-dir $WORKCOPY --overfetch \
  --query "FDP extension commander discretion fatigue" \
  --query "flight time limitations and rest requirements for crew" \
  --query "fatigue risk management system FRM requirements" \
  --query "disruptive schedules and unforeseen circumstances in actual operation" \
  --query "standby airport duty and reserve FTL" \
  --query "ORO.FTL.235 rest periods recurrent extended recovery rest"
```

Report sections of that script are cited as `R1`–`R11` (e.g. `R9` = canonical-domain rule); `§n` cites a part of this note. Mapping of the card's items (a)–(g) to this note: (a) = §1 claim table, (b) = §2, (c) = §3, (d) = §4, (e) = §5, (f) = §6, (g) = §7 (risks, alternatives, ids) and §8 (slice plan). Output is aggregates and entry refs only, never FAQ text. Tests of the script: `tests/test_faq_dup_investigation.py` (synthetic fixture, 16 tests).

Test baseline (clean worktree, HEAD b9687bd, no `-x`): **292 passed, 0 skipped, 0 xfailed, 0 failed**, ~56 s. Use `PYTHONPATH=src` — `PYTHONPATH=.` resolves `claw_easa` to the main checkout (editable install), not the worktree.

## 1. Claim table (request `2026-11-26-claw-easa-faq-duplicates.md`)

| # | Claim in the request | Verdict | Evidence |
|---|---|---|---|
| 1 | 4 EAR + 2 722 FAQ across 192 FAQ domains | CONFIRMED | R1: `source_families: ear:3 rulebook:1 faq:204`; 2 722 FAQ entries in 192 domains (12 FAQ documents hold no entry). |
| 2 | FAISS holds 13 033 chunks | CONFIRMED | R1/R10: `entry_chunks_total` = `faiss_mapping_rows` = `faiss_ntotal` = 13 033; 2 807 of them belong to FAQ entries. |
| 3 | `schema_migrations` only `001_initial`; `corpus_builds` empty; FAQ `revision`/`published_at` empty | CONFIRMED | R1. Code: `db/migrations.py:12` records only `INITIAL_VERSION`; `SCHEMA_VERSION = "004_corpus_freshness"` (`migrations.py:17`) is a label, never written to the table. |
| 4 | 1 137 redundant copies, 778 duplicated texts (454×2, 311×3, 8×6, 4×4, 1×10) | **CORRECTED** | Under the locked rule (title + body, N1, R2): **1 113** redundant, **783** duplicated texts, histogram `2:461 3:318 4:2 6:2` (2 722 FAQ → 1 609 distinct). The request's figures are reproduced *exactly* (R3 `reproduced_by: e_alnum_body`) only by a different rule: **body only, ASCII `[a-z0-9]` only** — it ignores the question and drops every non-ASCII letter, so it merges 24 rows whose answers are equal but whose questions differ. That contradicts D3 (hash covers question + answer). |
| 5 | 34 duplicates inside a single domain | **CORRECTED** | **6** under N1 (R5). 34 is the body-only/ASCII-only figure; the 28 extra rows are the same answer under a *different question* (or differing only in non-ASCII characters), not duplicates. D2's "~34 cases" therefore means 6 ordinal-suffixed ids on this snapshot (R11 `ordinal_suffixed: 6`). |
| 6 | ORO.FTL: 158 FAQ cite it, 53 distinct texts | CONFIRMED | R7: 158 FAQ linked through `faq_regulation_refs` (`target_ref LIKE 'ORO.FTL%'`), **53 distinct hashes**, 105 redundant copies; same under the stricter `= 'ORO.FTL' or startswith 'ORO.FTL.'` match, and under every normalisation variant of R3. Acceptance criterion "53 unique groups, 158 copies kept" stands. |
| 7 | Inclusion 100 %: oroftl(77) ⊂ part-oro(85) ⊂ air-operations(170); part-spo(23), part-nccnco(19) ⊂ air-operations | CONFIRMED | R4: oroftl ⊂ part-oro ⊂ air-operations (air-operations has 170 rows but 169 distinct hashes: one intra-domain pair); part-spo ⊂ and part-nccnco ⊂ air-operations. `direct_parent` (transitive reduction): oroftl → part-oro; part-oro, part-spo, part-nccnco, part-cat → air-operations. |
| 8 | Partial inclusions: third-country-operators → applicability (23/65), aerodromes-adr → aerodromes (18/76); `*-general`/sub-domain pairs | **CORRECTED** | These are **full** inclusions of the *child*: applicability(23) ⊂ third-country-operators(66); aerodromes(18) ⊂ aerodromes-adr(76). "23/65" and "18/76" are child size over parent size, not a partial overlap. `*-general` pairs are full inclusions too (e.g. part-m-general ⊂ part-m). Only **3** domain pairs overlap without either containing the other (R4 `true_partial_pairs: 3`, shared 3, 1, 1 hashes). Consequence: there is nothing to decide about partial inclusions — D-open-question "partial inclusions are not collapsed, only identical hashes collapse" costs nothing. |
| 9 | `discover_faq_domains()` excludes only {regulations, faq, faqs, website}; each page = own source_document, no parent/child, no content hash | CONFIRMED | `ingest/faq_service.py:35,52`; one `upsert_source_document_from_values` per page (`faq_service.py:61`); no hash column anywhere in `db/schema.sql`. |
| 10 | `entry_ref = f"FAQ-{domain.slug}-{ingested+1:03d}"` is positional and shifts on re-ingestion | CONFIRMED | `faq_service.py:116`; match key in `ingest/repository.py:221` is `(entry_ref, document_id)`, so a shifted position overwrites a *different* FAQ's text in place (title/body updated at `repository.py:226-232`). The FAQ unique index excludes FAQ rows (`db/schema.sql:140-142`). |
| 11 | 5 groups share a title but have different bodies (e.g. "UK certificates for UAS" 1 661 vs 50 chars) | **CORRECTED** (6) | R6: **6** groups under N1; 5 under the request's rule. The sixth differs only by a trailing footnote marker in the title (`…holders?` vs `…holders*?`, N1 strips edge punctuation) with bodies of 56 vs 5 564 chars. The UK-certificates example is confirmed (50 vs 1 661 chars). All six stay separate because the hash covers question + answer (D3). Group ids are listed in §7 (g). |
| 12 | `hybrid` top-10: 6 of 10 are copies of 2 FAQ; FAQ crowd out AMC/GM | CONFIRMED | Real CLI on a snapshot copy, query "FDP extension commander discretion fatigue", `--top-k 10`: 8 FAQ + 1 AMC + 1 GM; the 8 FAQ are 3 distinct texts (3 + 3 + 2 copies) → only 5 distinct results. After grouping at the recommended over-fetch: `AMC:1 FAQ:4 GM:4 IR:1`, 5 of the 10 refs change (R10 q1). Same pattern for q5/q6 (FAQ 7/10 and 9/10 today). |
| 13 | Parent/child FAQ pages explain the copies (root cause) | CONFIRMED | 782 of the 783 duplicated hashes span ≥ 2 domains; 1 is intra-domain only (R2). Cause = EASA aggregation pages + an ingestion with no hash/group notion (claims 9, 10). |
| 14 | Origin of the intra-domain duplicates "to verify" (EASA vs parser) | **Verdict: EASA page content (parser artefact not supported)** | R5: 6 duplicate pairs, all byte-identical raw title and body; 4 of 6 are non-adjacent (positions e.g. 032/055, 039/061, 046/073, 003/021) — a parser artefact from nested `div.faq-child` would double *every* nested item adjacently. 2 of 6 are adjacent (`faq-air-operations` 162/163, `faq-part-spo` 021/022): **UNVERIFIED** without re-fetching the page (network is out of scope). `parse_faq_page` (`ingest/faq_parser.py:69-112`) has no de-duplication, so it would keep both; the ordinal suffix (D2) handles both causes. |
| 15 | Index rebuild ≈ 15 min | UNVERIFIED | Not measured; out of scope (D1: no rebuild). |
| 16 | "`source_documents.revision/published_at` empty → no freshness traceability" | CONFIRMED | R1 `faq_documents_with_revision: 0`, `…_published_at: 0`, `corpus_builds_rows: 0`. Out of scope for this programme; unchanged. |

Additional findings that change the design (not in the request):

- **F1.** `FAQ-…` refs are not reference queries (`references.py:50-72` needs a dotted code), so `lookup FAQ-oroftl-028` works through the exact `entry_ref =` branch of `resolve_reference` (`retrieval/exact.py:41-73`) and `refs FAQ-oroftl-028` works through FTS because `entry_ref` is an FTS column. Both break silently once `entry_ref` is rewritten, unless aliases are resolved explicitly (see §4(d)).
- **F2.** All 2 807 FAQ chunks start with the owning ref: `chunk_text = "{entry_ref} ({type}) — {title}\n{body}"` (`retrieval/chunking.py`, `build_whole_entry_chunk`), R1 `faq_chunks_starting_with_entry_ref: 2807`. Rewriting `entry_ref` leaves that prefix stale in chunk text and in the embedded vectors (accepted by D1; text prefix handled in §6).
- **F3.** `retrieval/embedder.py:13` builds `SentenceTransformer(model_name)` on **every** `encode_texts` call and `vector_search` calls it once per search. Over-fetch must therefore be a **single wider fetch**, not a retry loop (each retry would reload the model: real CLI call = 10.1 s wall, 974 MB peak RSS on the copy).
- **F4.** `hybrid_search` merges by `entry_id`, and several chunks of one entry accumulate their scores (`retrieval/hybrid.py:61-84`). Query q4 shows 10 raw vector chunks collapsing to **1** entry (an INFO entry): a part of the starvation is chunk multiplicity, not FAQ copies. Grouping by entry fixes only the FAQ part; the over-fetch margin below covers both.
- **F5.** `Database.open()` runs `PRAGMA journal_mode = WAL` (`db/sqlite.py`) — any CLI call writes to the DB file set it opens. Rehearsals must always use a copy (D7), and the investigation script uses its own read-only connection.

## 2. Normalisation rule and hash (decision proposal, default kept)

One rule **N1** (the default of the brief; no change proposed, because it reproduces the ORO.FTL figure 158 → 53 and keeps D3):

1. Unicode NFKC; 2. `casefold()`; 3. collapse every whitespace run to one space; 4. strip leading and trailing characters that are whitespace or Unicode category `P*`. No stemming, no internal punctuation removal.

`content_hash = sha256( N1(title) + "\n" + N1(body_text) )` as 64 hex chars; `hash8 = content_hash[:8]`. `title` is the question, `body_text` the answer (`body_markdown` equals `body_text` for FAQ rows: verified on all 2 722). The `"\n"` separator is unambiguous because N1 output contains no newline.

Measured (R2/R3): 2 722 FAQ → 1 609 distinct hashes; 1 113 redundant copies; histogram `2:461 3:318 4:2 6:2`; 782 hashes span several domains. Variants (R3, all `distinct/redundant/dup-texts/intra`): raw title+body 1 609/1 113/783/6; lower+whitespace 1 609/1 113/783/6 (so the orchestrator's quick measure agrees); **N1** 1 609/1 113/783/6; N1 body-only 1 589/1 133/781/27; alnum body-only 1 585/1 137/778/34; alnum title+body 1 608/1 114/782/9. N1 and the two "plain" variants coincide on this corpus: the choice among them is not load-bearing, which is why N1 (the safest on non-ASCII) is kept.

The rule is **frozen**: it defines every future stable id. Changing it means a new migration, so S1 pins golden hash values in tests (including a full-width/NBSP/curly-quote case). Deferred hardening: a `hash_rule` version column (not needed while N1 is frozen — YAGNI).

## 3. Data model (S1) — three additive tables, no change to `regulation_entries`

`regulation_entries` is deliberately **not** altered: adding a column means rebuilding a table that backs the external-content FTS5 index and its three triggers (`db/schema.sql:170-200`), and that table is not in `MigrationRunner._MANAGED_TABLES`. The hash lives next to the group instead.

```sql
-- all in db/schema.sql, CREATE ... IF NOT EXISTS (so MigrationRunner.init_schema creates them, empty, on any open)
CREATE TABLE IF NOT EXISTS faq_groups (
    group_id INTEGER PRIMARY KEY AUTOINCREMENT,
    content_hash TEXT NOT NULL UNIQUE,                       -- 64 hex, N1 rule
    canonical_entry_id INTEGER NOT NULL
        REFERENCES regulation_entries(id) ON DELETE CASCADE,
    member_count INTEGER NOT NULL,
    domain_count INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS faq_entry_groups (
    entry_id INTEGER PRIMARY KEY REFERENCES regulation_entries(id) ON DELETE CASCADE,
    group_id INTEGER NOT NULL REFERENCES faq_groups(group_id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_faq_entry_groups_group ON faq_entry_groups(group_id);
CREATE TABLE IF NOT EXISTS faq_aliases (
    alias_ref TEXT NOT NULL,
    entry_id INTEGER NOT NULL REFERENCES regulation_entries(id) ON DELETE CASCADE,
    alias_kind TEXT NOT NULL DEFAULT 'legacy_positional',
    PRIMARY KEY (alias_ref, entry_id)
);
```

- Every FAQ entry belongs to exactly one group (singletons included: 1 609 groups on the snapshot), so read paths use one uniform join. `faq_groups` and `faq_entry_groups` are **derived data**: `rebuild_faq_groups(db)` recomputes them from the entries in one pass (2 722 rows, milliseconds) and is idempotent; it runs at the end of the migration and of each FAQ ingestion.
- Unmigrated databases (tables exist, empty): every read path treats an entry without a group row as its own group, so behaviour is identical to today until the migration is applied. Merging the code before the real-DB migration is therefore safe.
- `uix_faq_entries_ref_doc` — `CREATE UNIQUE INDEX ON regulation_entries(entry_ref, document_id) WHERE entry_type='FAQ'` — is created by the migration `up` (and dropped by `down`), not by `schema.sql`, so that an old database with colliding FAQ refs cannot make `init_schema` fail. Snapshot: `duplicate_faq_refs: 0`, `stable_id_collisions: 0` (R11).

### Canonical domain rule (D5) — data-driven, proved on the snapshot

A *domain* is the FAQ `source_documents.slug` of the entry. `domain_size` = number of FAQ rows of that document. For a hash group, the **canonical member** is the member whose domain has the smallest `(domain_size, slug)`; among members of that same domain the lowest `entry.id` (the one that carries no ordinal suffix). No slug is named anywhere in code (D9).

Proof (R9): 782 multi-domain groups; the three focus domains order as `faq-oroftl(77) < faq-part-oro(85) < faq-air-operations(170)`; **77 groups contain all three and in all 77 the canonical domain is `faq-oroftl`**. `faq-part-spo(23)` and `faq-part-nccnco(19)` win against `faq-air-operations` the same way. 30 groups tie on domain size and are resolved alphabetically (total order, so the result is deterministic). The canonical choice is a *display* choice: every copy keeps its own stable id, so a canonical that flips after a re-ingestion (domain sizes change) invalidates no citation.

## 4. Stable ids, aliases, resolution points (S1 ids/aliases, S2 resolution)

### (d) Stable id algorithm

`prefix` = the legacy prefix of the entry: at ingestion `FAQ-{domain.slug}` (`faq_service.py:116`); at migration the captured part of `^FAQ-(.+)-(\d{3,})$` — snapshot: 0 pattern mismatches, 0 rows where the prefix differs from `source_documents.slug` minus `faq-` (R11).

```
base   = f"{prefix}-{hash8}"                                   # D2: FAQ-{slug}-{hash8}
id     = base                      for the first row (lowest entry id / page order) with that base
       = f"{base}-{n}"             n = 2, 3, ... for the next rows with the same base (identical text twice in one domain)
```

Snapshot (R11): 2 722 ids, **6 carry an ordinal suffix**, 0 collisions, 0 `hash8` collisions (global and within a prefix). If a different full hash ever collides on `hash8` within a prefix (probability ≈ 3·10⁻⁴ per corpus of this size), ingestion/migration **raises** rather than silently suffixing (the suffix is reserved for identical text). Deferred: automatic hash12 fallback.

Old refs: one alias row `(legacy_ref, entry_id, 'legacy_positional')` per migrated FAQ (2 722). New rows created by later ingestions get **no** positional alias (a position is meaningless once ids are content-based). Aliases are only produced by the migration.

### Resolution points (S2) — one helper, wired at these places

New module `retrieval/faq_groups.py` with `resolve_faq_alias(db, ref) -> entry_id | None`, `collapse_faq_duplicates(...)`, `format_also_in(row)`. Order of precedence everywhere: current `entry_ref` first, then alias.

| Path | Code today | Change |
|---|---|---|
| `lookup`, `ask` exact lookup | `resolve_reference` (`exact.py:41-73`) called by `lookup_reference` (`exact.py:76`) | after the exact `e.entry_ref = ?` attempt and before the canonical-prefix fallback add `run("e.id IN (SELECT entry_id FROM faq_aliases WHERE alias_ref = ?)", (wanted,))`. Single place: it is shared by the 5-line extract and by `entry_detail`. |
| `lookup --json` | `entry_detail()` → `_matching_rows()` (`entry_detail.py:50,89`) → `resolve_reference` | inherited; add additive keys (below). A looked-up copy is returned **as itself** (identity-preserving), with its group info. |
| `refs` / `refs --json` | `service.refs` (`service.py:39`) → `search_references` (`exact.py:122`); `refs_payload` (`provenance.py:83`) | pre-step in `search_references`: if the normalised query equals an alias or a current FAQ `entry_ref`, return that row (score 1.0) like `_search_by_reference` does for dotted refs; otherwise FTS as today, over-fetched and collapsed (§5). |
| `snippets` | `service.snippets` (`service.py:47`) → `search_snippets` (`snippets.py:38`) | same alias pre-step; over-fetch + collapse after `_enrich_with_chunks`. |
| `hybrid` | `hybrid_search` (`hybrid.py:34`); reference short-circuit at `hybrid.py:43` only for `looks_like_reference` (no `FAQ-` prefix) | alias pre-step before FTS/vector (so `ask "FAQ-oroftl-028"` and `hybrid "FAQ-oroftl-028"` resolve); collapse after the merge (§5). |
| `ask` | `cli/__init__.py:659-696`; `exact_lookup` → `lookup` (`:674`); `format_answer_answer` (`answering.py:78-120`) promotes a FAQ and de-duplicates by `row['entry_ref']` | rows arrive already collapsed, so the `entry_ref` comparisons in the promotion/"Related FAQs" logic are unchanged and now compare canonical refs. `_format_source_line` (`answering.py:6`) and the "Related FAQs" lines (`answering.py:119-122`) append `format_also_in(row)`. `ask` cites the **canonical** stable id with `also in:` as a secondary suffix (open question answered: yes). |
| ingestion | `upsert_faq_entry` (`repository.py:206`) keyed on positional `(entry_ref, document_id)` | keyed on the stable id (§6, re-ingestion). |

`chunk_text` shown by `snippets`/`ask` starts with the entry ref (F2); the migration rewrites that text prefix (§6) so output never shows the retired positional ref.

## 5. Query-time grouping (S2)

### (e) Algorithm

Grouping is a read-side step after ranking (D1: no FAISS rebuild, FTS is per entry so it is needed anyway).

```
raw_k = top_k * FAQ_OVERFETCH_FACTOR                     # default 10; setting CLAW_EASA_FAQ_OVERFETCH_FACTOR
fts   = search_references(db, query, limit=raw_k, slug=slug)
vec   = vector_search(db, query, top_k=raw_k, slug=slug)   # one model load, one wider FAISS search
merged = existing merge + slug boost, unchanged             # hybrid.py:61-92
rows   = collapse_faq_duplicates(db, sorted(merged), slug)
return rows[:top_k]
```

`collapse_faq_duplicates`, in ranking order: non-FAQ rows and FAQ rows without a group row pass through; FAQ rows are keyed by `group_id`; the first (best) row of a group opens it, later members only add themselves to `also_in`. Group score = best member score (`max`, never a sum, so three copies cannot outvote one). **Displayed row = the canonical member** (loaded by `canonical_entry_id` when it was not among the candidates; text, ref and slug are the canonical's, `chunk_text`/`matched_subref` come from the best-scoring member — identical text by construction). `also_in` = the *other* members `[{"entry_ref", "slug"}]`, ordered by `(domain_size, slug)`, **all** members of the group, not only those present in the candidates (read from `faq_entry_groups`). `content_hash` and `group_size` are added. Every FAQ row of an unmigrated DB passes through unchanged.

Interactions:

- **FTS + vector merge:** grouping runs after the merge, so a FAQ matched by FTS in one domain and by vector in another becomes one group with the better of the two merged scores; merge weights and normalisation (max of each raw list) are untouched. A wider fetch does not change the normaliser: the top hit of each list is the same for any `raw_k`.
- **`top_k`:** applies to groups, after collapse. `ask` survey (`top_k=30`) and answer (`top_k=15` default via `service.hybrid`) scale `raw_k` accordingly (300 / 150).
- **`slug` filter:** the filter is applied before grouping (as today: FTS in SQL, vector post-filter on `3 × raw_k` chunks, `vector.py:25-26`). With a slug, the displayed row is the **best member inside that slug** (the user asked for that corpus), `also_in` still lists the copies elsewhere. Within one slug only the 6 intra-domain pairs can collapse.
- **`refs` / `snippets`:** `limit × factor` raw rows → collapse → `limit`. `refs --json` additive keys per result: `also_in` (always present, `[]` for non-FAQ), `content_hash` (`null` for non-FAQ), `legacy_refs` (list of retired positional refs for that entry). `lookup --json` adds the same three keys plus `group_size`, `canonical_ref` and `is_canonical` to `entry` and to each item of `matches[]`. `schema_version` stays `"1.0"` (additive only, no existing key changes meaning).
- **Text outputs:** one suffix ` [also in: faq-part-oro, faq-air-operations]` (slugs, canonical first omitted) via `format_also_in`; FAQ/IR/AMC/GM labels untouched (D6).
- **Exact reference path** (`hybrid.py:43-50`) returns rows without grouping: a dotted reference does not identify FAQ copies.

### Over-fetch measurement (R10, snapshot copy, offline embedder `BAAI/bge-small-en-v1.5` from the local cache)

The script replicates the merge of `hybrid_search` faithfully (and agrees with the real CLI on q1: 8 FAQ / 1 AMC / 1 GM in today's top 10). Raw size K = number of FTS rows and of vector *chunks* fetched, each.

| query | today's top-10 (types, FAQ groups) | min K for 10 groups | after grouping at that K |
|---|---|---|---|
| q1 "FDP extension commander discretion fatigue" | FAQ 8 (3 groups), AMC 1, GM 1 | 20 | AMC 1, FAQ 4, GM 4, IR 1 (5 refs changed) |
| q2 "flight time limitations and rest requirements for crew" | AMC 3, GM 3, IR 4 | 10 | unchanged |
| q3 "fatigue risk management system FRM requirements" | AMC 5, GM 3, INFO 1, IR 1 | 10 | unchanged |
| q4 "disruptive schedules and unforeseen circumstances in actual operation" | **1 row** (10 chunks of one INFO entry) | **60** | AMC 1, FAQ 3, GM 2, INFO 3, IR 1 |
| q5 "standby airport duty and reserve FTL" | FAQ 7 (3 groups), GM 2, IR 1 | 20 | FAQ 6, GM 3, IR 1 |
| q6 "ORO.FTL.235 rest periods recurrent extended recovery rest" | FAQ 9 (4 groups), GM 1 | 20 | AMC 1, FAQ 6, GM 2, IR 1 |

- FAQ-driven cases (q1, q5, q6) need K = 20 = 2 × top_k. The worst case (q4, 60 = **6 × top_k**) is driven by chunk multiplicity (F4), not by FAQ copies.
- FTS alone can never supply 10 groups for these queries (it returns only 7/39/11/0/2/14 rows: the AND-style FTS query is narrow), so the vector list carries the over-fetch; `vector_only_min_K` = 20/10/10/60/20/20.
- **Recommended: fixed factor 10** (`raw_k = 10 × top_k`; 100 chunks for `top_k=10`). It covers the measured worst case (6×) with a 1.7× margin; K = 100 costs one flat FAISS search over 13 033 vectors (milliseconds) and one SQL `IN (…100…)` — a CLI call is dominated by process start and the per-call model load, not by the search (real `hybrid` call on the copy: **10.1 s wall, 974 MB peak RSS**; the script's offline section, 6 queries × 12 ladder sizes: 8.5 s, 989 MB; the model-load/search split was not measured separately). A fixed factor is preferred to an adaptive retry loop because of F3. Worst case with the default factor: a query whose best 100 chunks collapse to fewer than `top_k` groups returns fewer than `top_k` results — acceptable and visible (`count` < `limit`); S2 tests assert it never returns *duplicates* instead. Deferred hardening: one adaptive second pass with a cached query vector.

## 6. Migration (S1 code, S3 rehearsal/runbook)

### (f) Fit with `db/migrations.py`

`MigrationRunner.init_schema()` (`migrations.py:78-90`) is a **shape sync** run on every open: `_sync_tables` rebuilds stale tables of `_MANAGED_TABLES`, `schema.sql` is replayed (`CREATE … IF NOT EXISTS`), then `001_initial` is recorded. That is the right mechanism for the three **new empty tables** (§3): adding them to `schema.sql` creates them everywhere, harmlessly, and they need no `_MANAGED_TABLES` entry. It is the wrong mechanism for the **data change** (rewriting `entry_ref`, filling aliases/groups): that changes the identity of rows, must be explicit, rehearsed and human-gated (D7, Gate 3), and must be reversible. So:

- **Explicit versioned migration `005_faq_stable_ids`** (continues the code's own `004_corpus_freshness` label), implemented in `db/faq_migration.py` with four functions that take an open `Database` and never run implicitly: `plan(db)`, `up(db)`, `down(db)`, `verify(db)`. It is recorded in `schema_migrations` (so `current_version()` finally means something) and exposed by CLI `claw-easa db faq-ids {plan|backup|apply|rollback|verify} --data-dir DIR` (S3 added `backup`, `verify --ref-prefix/--against`, `rollback --against`, the `faq_tables_*` plan fields and a typed confirmation for the default data dir; see `docs/faq-dedup-migration-runbook.md`).
- **`--data-dir` is mandatory** (no environment or default fallback) and the command refuses `default_data_dir()` (`~/.local/share/claw-easa`) unless `--allow-default-data-dir` is given: rehearsals only ever touch the directory they are told to touch (D7).

### `plan` / dry-run (read-only: opens `mode=ro`, writes nothing, prints counts only)

```
faq_rows: 2722   domains: 192   groups: 1609   redundant_copies: 1113   multi_domain_groups: 782
aliases_to_create: 2722   entry_refs_to_rewrite: 2722   ordinal_suffixed: 6   faq_chunks_to_update: 2807
stable_id_collisions: 0   hash8_collisions: 0   legacy_pattern_mismatch: 0   duplicate_faq_refs: 0
already_applied: no      would_apply: yes
```
Exit code 0 when `would_apply: yes` or already applied, 1 when a precheck fails (and says which).

### `up` (one `BEGIN IMMEDIATE` transaction, all-or-nothing)

1. Prechecks of `plan` (any failure → abort, nothing written). Already recorded → no-op, exit 0 (**idempotent**).
2. For each FAQ row ordered by `id`: compute hash and stable id; insert the legacy alias; `UPDATE regulation_entries SET entry_ref = <stable id>` (the existing `entries_fts_update` trigger keeps `entries_fts` in step); `UPDATE entry_chunks SET chunk_text = <new ref> || substr(chunk_text, length(<old ref>)+1)` for the entry's chunks that start with the old ref (text prefix only; vectors and `faiss_mapping` are untouched — D1).
3. `rebuild_faq_groups(db)`; create `uix_faq_entries_ref_doc`; insert `005_faq_stable_ids` into `schema_migrations`.
4. Post-check inside the transaction: FAQ row count equal to before, alias count = FAQ count, every FAQ in exactly one group; otherwise rollback.

**Backup requirement:** `apply` requires `--backup-to PATH` (must not exist): it takes a SQLite online backup (`Connection.backup`) *before* `BEGIN`, runs `PRAGMA integrity_check` on the backup, compares row counts of `regulation_entries`/`faq_regulation_refs`/`entry_chunks`, and aborts on any difference. The FAISS file is not modified by the migration; the runbook copies it anyway. The migration needs exclusive use of the DB (no concurrent CLI call).

### `down` (rollback) and verification

`down` precondition: `005` recorded and no FAQ row without a legacy alias (rows created by an ingestion *after* the migration cannot be restored positionally; then `down` refuses and prints their number, unless `--force` is given — documented loss of those rows' positional refs only, never of rows). Steps in one transaction: restore `entry_ref` from the alias (and the chunk text prefix), delete rows of `faq_aliases`/`faq_entry_groups`/`faq_groups` (tables stay: they are part of the schema), drop `uix_faq_entries_ref_doc`, delete the `005` row.

`verify(db)` computes two checksums:
- **Immutable fingerprint** (must be identical before `up`, after `up`, after `down`): sha256 of `(id, document_id, entry_type, title, body_text, body_markdown, source_url)` for all FAQ rows ordered by id, plus all `faq_regulation_refs (faq_entry_id, target_ref)` ordered by id → proves "no FAQ lost, text untouched, refs untouched".
- **Restorable fingerprint** (identical before `up` and after `down`): sha256 of `(id, entry_ref)` for FAQ rows plus `(entry_chunks.id, chunk_text)` of FAQ chunks → proves the rollback restores the exact pre-migration identities.
Plus: `PRAGMA integrity_check`, FTS `integrity-check`, resolution check (every legacy alias resolves to a row whose immutable fields equal the pre-migration ones — done on the copy in the S3 rehearsal against the pre-migration database).

### Re-ingestion after the migration

`ingest_faq_domain` (S1) computes `(hash, stable id)` for each parsed item (ordinal for repeated text on the page), upserts by `(document_id, stable id)` (an unchanged FAQ is a no-op update; a cosmetic change that N1 absorbs keeps its id; an edited FAQ gets a **new id and a new row**), links refs, then rebuilds the groups. FAQ rows that disappeared from the page are left untouched, as today (deferred hardening: flag them as retired). Old edited rows therefore stay citable and keep their own hash group. **Guard:** `ingest` refuses to run when FAQ rows exist but `005` is not recorded (it would otherwise insert a second copy of every FAQ under new ids) — a database with no FAQ rows records `005` itself on first ingestion. Positional refs are never generated again.

## 7. Risks, rejected alternatives, deferred hardening

Risks (with mitigation): (1) *Stale embedding prefix* — vectors were computed from chunks starting with the old ref (D1, accepted; ranking effect is nil, a later `index rebuild` re-embeds with new refs). (2) *Canonical flips* on re-ingestion — display only, ids unaffected (§3). (3) *FTS no longer finds `FAQ-slug-NNN` tokens* — covered by the alias pre-step, which handles exact legacy refs only (a partial legacy token such as `oroftl 028` no longer hits). (4) *Over-fetch cost* — one wider search, measured above. (5) *Ingest-before-migrate duplication* — guard in §6. (6) *Hash rule frozen* — golden-value tests. (7) *Concurrency* — migration needs exclusive access; documented in the runbook. (8) *2 adjacent intra-domain pairs unverified* — harmless (suffix). (9) *`hash8` collision* — raises, probability ≈ 3·10⁻⁴.

Alternatives rejected: rebuilding/deduplicating FAISS or one chunk per group (D1); title-only hash (D3); the request's body-only ASCII-alnum rule (reproduces its figures but merges different questions and drops non-ASCII letters); a `content_hash` column on `regulation_entries` (table rebuild around the external-content FTS table and its triggers for no read-path benefit); hard-coded domain ordering or slug lists (D9); collapsing partial overlaps (only 3 true partial pairs exist, and D-open-question says no); storing canonical as a flag on entries (stale after any re-ingestion, the group table is derived); auto-applying the data migration from `init_schema` (identity change must be human-gated); an adaptive retry loop for over-fetch (F3).

Deferred hardening: hash12 fallback; flagging retired/removed FAQs on re-ingestion; adaptive second over-fetch pass; populating `revision`/`published_at`/`corpus_builds` for FAQ (separate programme); verifying the 2 adjacent intra-domain pairs against the live EASA pages.

Open decisions, proposed defaults (no human stop): normalisation N1; partial inclusions not collapsed; `ask` cites canonical id with `also in`; over-fetch factor 10 fixed and configurable; JSON keys `also_in`, `legacy_refs`, `content_hash` (+ `group_size`, `canonical_ref`, `is_canonical` on `lookup --json`); chunk text prefix rewritten, vectors not; `down` refuses when post-migration rows exist unless `--force`; migration id `005_faq_stable_ids`.

### (g) Identification by id (no text)

- Regression refs (R8): `FAQ-oroftl-028` (faq-oroftl), `FAQ-air-operations-057` (faq-air-operations), `FAQ-part-oro-036` (faq-part-oro) all exist and share hash8 `590f5a9e` — **the refs in the request are right, no correction needed**; expected stable ids `FAQ-oroftl-590f5a9e`, `FAQ-air-operations-590f5a9e`, `FAQ-part-oro-590f5a9e`; canonical = `FAQ-oroftl-590f5a9e`, `also_in` = part-oro, air-operations.
- Same-title / different-body groups (R6, 6 groups, refs before migration):
  1. `FAQ-air-operations-007`, `FAQ-air-operations-general-007`, `FAQ-general-008`
  2. `FAQ-aircraft-operations-006`, `FAQ-brexit-047`, `FAQ-drones-uas-010`, `FAQ-provisions-applicable-both-open-and-specific-category-010`, `FAQ-regulations-uas-drone-explained-010` (UK certificates: 50 vs 1 661 chars)
  3. `FAQ-certification-products-and-organisations-047`, `FAQ-general-aviation-028`
  4. `FAQ-continuing-airworthiness-015`, `FAQ-continuing-airworthiness-general-003`, `FAQ-initial-airworthiness-017` (the sixth group, absent from the request: footnote asterisk)
  5. `FAQ-continuing-airworthiness-050`, `FAQ-part-145-004`, `FAQ-part-145-general-004`, `FAQ-supplemental-type-certificates-102`
  6. ten `FAQ-drones-uas-*`/`open-category`/`specific-category`/`responsibilities-*` refs (R6 lists them; bodies of 950 vs 951 chars: one-character differences)
- Intra-domain duplicate examples (R5): `FAQ-air-operations-162`/`-163`; `FAQ-certification-products-and-organisations-003`/`-021`; `FAQ-drones-uas-032`/`-055`.

## 8. Slice plan

Each slice is one vertical, ~10–25 min Claude Code tranche per card (RED → GREEN → minimal refactor), serial on the same branch, full suite only in S3. Tests use synthetic fixtures (no EASA text in git). Test command: `PYTHONPATH=src /home/openclaw/dev/clawEASA/.venv/bin/python -m pytest -rs -p no:cacheprovider`.

**S1 — data layer** (identity, migration, ingestion). Files: `db/schema.sql` (3 tables); new `ingest/faq_identity.py` (pure: `normalise`, `content_hash`, `hash8`, `assign_stable_ids(items)` with ordinals, `parse_legacy_ref`); new `db/faq_migration.py` (`plan/up/down/verify`, `rebuild_faq_groups`, canonical rule, backup helper); `ingest/repository.py` (`upsert_faq_entry` keyed by stable id, `refresh groups`); `ingest/faq_service.py` (ids, `005` guard, group rebuild; no network in tests: `parse_faq_page`/`http` stubbed); `cli/__init__.py` (`db faq-ids` subcommands). Tests: golden hashes (incl. NFKC/NBSP/curly quotes), ordinal suffix, hash8-collision raises, canonical rule on a 3-domain synthetic corpus (child/parent/grandparent + tie), migration `plan` counts, `up` → immutable fingerprint equal / row count equal / aliases resolve / groups complete, idempotent second `up`, `down` restores the restorable fingerprint, `down` refuses with post-migration rows, backup required, refuses default data dir, re-ingestion stability (unchanged page → 0 new rows; inserted FAQ at position 1 → no id shifts; edited FAQ → new id, old kept), ingest-before-migrate guard, FTS in sync after `up`/`down`. Acceptance: no retrieval code touched; existing 292 tests green.

**S2 — retrieval.** Files: new `retrieval/faq_groups.py` (`resolve_faq_alias`, `collapse_faq_duplicates`, `format_also_in`); `retrieval/exact.py` (`resolve_reference` alias step; `search_references` alias pre-step + `limit × factor` support); `retrieval/snippets.py`; `retrieval/hybrid.py` (alias pre-step, `raw_k`, collapse after merge, slug rule); `retrieval/service.py` (`refs`/`snippets` over-fetch + collapse); `retrieval/provenance.py` (`refs_payload` additive keys); `retrieval/entry_detail.py` (additive keys); `retrieval/answering.py` + `cli/__init__.py` (`also in` suffix); `config.py` (`faq_overfetch_factor`, env `CLAW_EASA_FAQ_OVERFETCH_FACTOR`; add to the key tuple at `config.py:83`). Tests (synthetic DB with groups built by S1 helpers): the three regression refs → ONE result listing 3 domains in `refs`, `snippets`, `hybrid`, `ask`; the 6 same-title/different-body groups stay separate; every legacy ref resolves in `refs`, `snippets`, `lookup`, `lookup --json`, `hybrid`, `ask`; `lookup` of a non-canonical copy returns itself with group info; slug filter picks the in-slug member; unmigrated DB behaves exactly as before; top-10 `hybrid` has no two results with identical normalised text and non-FAQ rows are not evicted (stubbed vector search); JSON additive-only (existing keys byte-identical); FAQ/IR/AMC/GM labels unchanged. Opt-in snapshot check as a script (not in the default pytest run).

**S3 — consolidation.** Files: `scripts/faq_migration_rehearsal.py` (copies the snapshot to a fresh work dir given explicitly, runs `plan` → `apply --backup-to` → `verify` → acceptance checks (FAQ count equal, every legacy ref resolves, ORO.FTL = 53 groups / 158 rows, regression refs collapse, 6 groups separate) → real `hybrid` before/after on the 6 queries with aggregates only → `rollback` → fingerprint compare → idempotent re-run; prints counts only); `docs/faq-migration-runbook.md` (Gate 3 procedure: stop writers, verified sqlite backup + FAISS copy, `plan`, canary on a copy, apply, `verify`, re-audit counts, rollback steps, what re-ingestion does); README/skill text for the new keys and flags; full suite without `-x` compared to the S0 baseline (292 / 0 / 0 / 0, plus new tests, no existing test edited unless documented); push the branch and open a **draft** PR with aggregates only in the body. No merge, no real-DB write.
