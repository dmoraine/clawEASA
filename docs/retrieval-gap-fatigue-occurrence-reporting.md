# Retrieval gap analysis: missing Annex I fatigue occurrence in occurrence-reporting searches

## Objective

Document precisely what happened in the recent failed retrieval, why the current retrieval stack missed a critical occurrence-reporting reference, and what should be implemented to prevent similar misses in the future.

The motivating missed reference was:

> **ANNEX I — OCCURRENCES RELATED TO THE OPERATION OF THE AIRCRAFT**  
> **(11) Crew fatigue impacting or potentially impacting their ability to perform safely their flight duties.**

This text exists in the local corpus and was successfully ingested, but it did not surface early enough when answering a natural-language question about references requiring crew members to report fatigue events or occurrences.

---

## Executive summary

This was **not an ingestion problem** and **not an indexing failure**.

The phrase was present in the database after reingestion, but it was missed because of a combination of:

1. **Query-to-text mismatch**
   - The user asked in obligation language: *"what are the references requiring the crew members to report fatigue events or occurrences?"*
   - The relevant text is phrased as an **occurrence list item**, not an explicit obligation sentence.

2. **Ranking bias toward discursive provisions**
   - Current `refs` and `hybrid` ranking tends to surface provisions that literally discuss reporting obligations (`CAT.GEN.MPA.100`, `Article 4`, AMC/GM on reporting) before annex list items.

3. **No occurrence-reporting-specific query expansion / source boosting**
   - The system does not currently recognize that queries about fatigue occurrence reporting should strongly prioritize the occurrence-reporting EAR and Annex I/II/III/IV occurrence lists.

4. **Annex ambiguity**
   - Multiple entries begin with `ANNEX I...`; the retrieval layer and manual checks can hit the wrong annex when the query is underspecified.

5. **Human/operator investigation error**
   - During manual verification, a `LIKE 'ANNEX I%'` query followed by `fetchone()` likely returned the wrong `ANNEX I` entry from `occurrence-reporting`, hiding the real match.

The core product issue is therefore:

> The retrieval stack is currently better at finding **narrative rule text** than **high-value items embedded inside long annex / appendix / list-style entries**.

That is an important weakness for regulatory retrieval.

---

## Evidence observed in the current codebase

### Retrieval behavior

Relevant current files:

- `src/claw_easa/retrieval/exact.py`
- `src/claw_easa/retrieval/snippets.py`
- `src/claw_easa/retrieval/hybrid.py`
- `src/claw_easa/retrieval/router.py`
- `src/claw_easa/retrieval/fts_compat.py`

### Current `refs` behavior

`search_references()` in `retrieval/exact.py`:

- runs FTS first using `entries_fts MATCH ?`
- falls back to SQL `LIKE` only if FTS returns nothing
- does **not** apply domain-aware boosting
- does **not** distinguish between broad narrative provisions and list-item-heavy annexes
- does **not** perform targeted fallback scans in likely source documents

### Current `hybrid` behavior

`hybrid_search()` in `retrieval/hybrid.py`:

- merges FTS + vector results
- normalizes by max score in each channel
- combines using fixed weights (`fts_weight=0.4`, `vector_weight=0.6`)
- does **not** apply domain-specific ranking heuristics
- does **not** boost specific source families or documents based on intent

### Current router behavior

`route_query()` in `retrieval/router.py`:

- recognizes exact references, refs, snippets, survey, answer
- does **not** infer a special intent for:
  - occurrence-reportability
  - reportable items in annexes
  - fatigue occurrence searches
  - annex/list-item lookup

### Current FTS query conversion

`to_fts5_query()` in `retrieval/fts_compat.py`:

- tokenizes phrases and words
- supports `OR` and negation
- does not add synonyms / domain expansions
- does not emit field-aware boosts
- does not decompose obligation queries into:
  - actor (`crew member`)
  - action (`report`)
  - domain (`occurrence-reporting`)
  - target concept (`crew fatigue`)

---

## Root-cause analysis

## 1. The system searched for the legal framing, not the occurrence item wording

The user question used these concepts:

- crew members
- requiring
- report
- fatigue events
- occurrences

The key missed text used:

- crew fatigue
- impacting / potentially impacting
- ability to perform safely
- flight duties

Notice what is absent from the annex text:

- no explicit `shall report`
- no explicit `crew members must report`
- no direct repetition of `occurrence reporting scheme`
- no narrative legal explanation

So from a plain ranking perspective, documents like these are naturally favored:

- `CAT.GEN.MPA.100`
- `Article 4` of Reg 376/2014
- `AMC1 ORO.FTL.120(b)(4)`
- FAQs explaining reporting

Those are relevant, but they do not substitute for the **actual reportable occurrence item** in Annex I.

### Consequence

The current engine is answering:

> “Which provisions discuss reporting duties?”

better than:

> “Where is this specific fatigue issue enumerated as a reportable occurrence?”

That distinction matters.

---

## 2. Annex/list-style entries are structurally disadvantaged in ranking

Large annex entries tend to be poor top-ranked results because:

- they are long and heterogeneous
- the important line is a small item buried deep inside the body
- neighboring text may dilute lexical relevance
- vector embeddings for a whole annex can drift toward a broad document-level meaning

In this specific case, the relevant signal was a single list item:

> `(11) Crew fatigue impacting or potentially impacting their ability to perform safely their flight duties.`

If the retrieval unit is the **whole annex entry**, then the ranking signal for that one line can be weaker than for compact discursive provisions explicitly containing words like:

- report
n- occurrence
- crew member
- comply
- reporting scheme

### Consequence

Even with correct ingestion and FAISS built, the retrieval unit granularity is too coarse to guarantee recall of important enumerated items.

---

## 3. No source-family awareness for occurrence-reporting queries

When a query includes concepts like:

- occurrence
- report
- reportable
- mandatory reporting
- crew fatigue
- fatigue event

there is a very strong prior that the answer may live in:

- `occurrence-reporting`
- `Article 4`
- `ANNEX I/II/III/IV` of Regulation (EU) 2015/1018
- occurrence-reporting FAQ material

Today the retrieval code does not exploit this prior.

### Consequence

High-probability source documents are not preferentially searched or boosted.

---

## 4. Ambiguous reference labels (`ANNEX I`) make both retrieval and manual checks fragile

The corpus contains multiple entries starting with `ANNEX I...`, including within `occurrence-reporting`.

Example observed:

- `ANNEX I — LIST OF REQUIREMENTS APPLICABLE...`
- `ANNEX I — OCCURRENCES RELATED TO THE OPERATION OF...`

A naive query such as:

- `entry_ref LIKE 'ANNEX I%'`

is therefore ambiguous.

### Consequence

- Retrieval can rank the wrong annex.
- Manual verification can hit the wrong annex if the code or operator uses first-match semantics.

This was part of what happened during manual checking.

---

## 5. Investigation ergonomics are insufficient

The CLI currently provides:

- `refs`
- `snippets`
- `hybrid`
- `lookup`
- `ask`

But it does not provide strong tooling to answer questions like:

- “show me every occurrence-reporting item containing fatigue”
- “search within occurrence-reporting only”
- “search inside Annex I only”
- “prefer list items / annex entries over commentary”

### Consequence

When a result is missed, it is too easy to confirm the wrong hypothesis instead of quickly disproving it.

---

## What should be implemented

## A. Add an explicit retrieval intent for occurrence-reportability

### Proposed new intent
Add a new routed intent, for example:

- `occurrence_reporting`
- or `reportable_occurrence_survey`

### Trigger heuristics
If the normalized query contains combinations such as:

- `occurrence` + `report`
- `reportable occurrence`
- `mandatory reporting`
- `fatigue` + `occurrence`
- `crew fatigue` + `report`
- `Annex I`
- `376/2014`
- `2015/1018`

route to this specialized retrieval path.

### Behavior of the new intent
The specialized path should:

1. search likely occurrence-reporting core docs first
2. expand the query with domain synonyms
3. run both whole-entry and snippet-level retrieval
4. boost annex occurrence lists heavily
5. return grouped results by legal level:
   - Article / IR
   - Annex list entries
   - AMC/GM
   - FAQ

### Why this matters
This moves the product from generic semantic search toward **regulatory-task-aware retrieval**.

---

## B. Add domain-specific query expansion for occurrence-reporting

### Proposed rewrite behavior
For queries matching the occurrence-reporting intent, generate an expanded internal query set including terms such as:

- `crew fatigue`
- `fatigue occurrence`
- `reportable occurrence`
- `mandatory reporting`
- `Regulation (EU) No 376/2014`
- `Regulation (EU) 2015/1018`
- `crew-related occurrences`
- `flight duties`
- `ability to perform safely`
- `ANNEX I occurrences related to the operation of the aircraft`

### Important note
This should be **internal expansion**, not necessarily user-visible rewriting.

### Why this matters
The current user phrasing may target the legal concept, while the relevant text uses operational wording. Expansion bridges the gap.

---

## C. Introduce source-aware boosting in FTS and hybrid ranking

### Boost candidates for occurrence-reporting queries
When the routed intent is occurrence-reporting related, apply score bonuses to results from:

1. `occurrence-reporting` source document family
2. `Article 4` in that family
3. `ANNEX I — OCCURRENCES RELATED TO THE OPERATION OF THE AIRCRAFT`
4. `ANNEX II`, `ANNEX III`, `ANNEX IV` occurrence lists when relevant
5. occurrence-reporting FAQ domains

### Boost candidates for fatigue-occurrence queries specifically
Additional bonus if:

- `body_text` contains `crew fatigue`
- `body_text` contains `flight duties`
- `body_text` contains `perform safely`
- `title` or `entry_ref` indicates annex occurrence list

### Implementation sketch
After collecting FTS and vector results, before final sort, add feature-based score increments such as:

- `+source_family_bonus`
- `+annex_occurrence_bonus`
- `+query_term_coverage_bonus`
- `+exact_phrase_bonus`

### Why this matters
It is much cheaper and safer than replacing the retrieval architecture entirely.

---

## D. Add a second-stage reranker focused on legal relevance

### Problem
FTS and vector retrieval are first-stage retrievers. They are not enough on their own for difficult regulatory tasks.

### Proposed second-stage reranking features
For the top N candidates from FTS + vector, rerank using features like:

- source family is `occurrence-reporting`
- result is an Annex entry
- title contains `OCCURRENCES RELATED TO`
- body contains exact or near-exact match of expanded target phrase
- result contains `crew fatigue`
- result contains `report`, `reported`, `occurrence`
- result legal level preference for the task

For this particular failure case, such a reranker would likely elevate the Annex I result above generic but relevant provisions.

### Why this matters
This improves precision without sacrificing recall.

---

## E. Support document-scoped retrieval

### Needed CLI / service capabilities
Add optional filters to `refs`, `snippets`, and `hybrid` such as:

- `--slug occurrence-reporting`
- `--source-family faq`
- `--entry-ref-prefix "ANNEX I"`
- `--title-contains "OCCURRENCES RELATED TO"`
- `--entry-type INFO`

### Example usage

```bash
claw-easa snippets "crew fatigue" --slug occurrence-reporting
claw-easa refs "fatigue" --slug occurrence-reporting --entry-ref-prefix "ANNEX I"
```

### Why this matters
This is valuable both for end users and for debugging retrieval problems.

---

## F. Index sub-entry list items as smaller retrieval units

This is likely the most structurally important medium-term fix.

### Current problem
A whole annex is indexed as one large body entry.

### Proposed change
During parsing or post-processing, create smaller retrieval units for list items such as:

- `(11) Crew fatigue impacting ...`
- `(10) Incapacitation ...`
- etc.

Possible storage strategies:

1. **New child table**
   - `regulation_entry_items`
   - fields:
     - `parent_entry_id`
     - `item_ref` (e.g. `ANNEX I / 1.4 / (11)` or similar)
     - `item_text`
     - `sort_order`
     - `document_id`
     - inherited metadata

2. **Chunked retrieval only**
   - split annexes into semantic chunks / list-item chunks for FTS + vector indexing
   - keep parent entry intact for display

3. **Hybrid**
   - preserve parent entry records
   - add item-level search index rows used only in retrieval

### Recommended approach
Prefer **hybrid**:
- keep the current top-level regulation entry model intact
- add item/chunk-level retrieval rows for long annexes and appendices

### Why this matters
The issue is fundamentally one of retrieval granularity. List-item-level indexing would dramatically improve recall for exactly this class of question.

---

## G. Add targeted fallback search for likely misses

### Proposed fallback rule
If a query contains any of:

- `fatigue`
- `occurrence`
- `report`
- `reportable`

and top results do not include the `occurrence-reporting` source family, run a fallback high-recall scan on:

- `occurrence-reporting`
- Article 4
- all `ANNEX I/II/III/IV` entries

### Fallback methods
In descending order:

1. FTS in filtered source scope
2. SQL `LIKE` in filtered source scope
3. exact phrase / near-phrase scan in filtered source scope

### Why this matters
This creates a safety net without changing the default path for all queries.

---

## H. Improve answer assembly so occurrence lists are not omitted

Even if retrieval returns Article 4 and CAT.GEN.MPA.100 first, answer synthesis for this intent should explicitly ask:

- “Is the target concept also explicitly enumerated in an annex/list of reportable occurrences?”

If yes, the answer formatter should surface that separately:

- **Legal reporting basis**
- **Occurrence list item explicitly naming the event**
- **AMC/GM supporting fatigue-reporting process**

### Why this matters
For regulatory work, the user often needs both:

- the obligation to report
- and the explicit listing of the event as reportable

Those are different kinds of evidence.

---

## I. Improve annex identity / normalization

### Problem
`ANNEX I` is ambiguous.

### Proposed changes
Add normalized metadata such as:

- `entry_ref_normalized`
- `annex_label` (`ANNEX I`)
- `annex_topic` (`occurrences related to the operation of the aircraft`)
- `entry_family` (`annex_occurrence_list`)

For example:

- `entry_ref = "ANNEX I — OCCURRENCES RELATED TO THE OPERATION OF THE AIRCRAFT"`
- `annex_label = "ANNEX I"`
- `annex_topic = "occurrences related to the operation of the aircraft"`
- `entry_family = "occurrence_list"`

### Why this matters
This improves both search and programmatic filtering.

---

## J. Add debugging / audit tooling for missed retrievals

### Proposed command(s)
Examples:

```bash
claw-easa debug-search "crew fatigue report occurrence"
claw-easa debug-search "crew fatigue report occurrence" --slug occurrence-reporting
```

### Output should include
- normalized query
- rewritten query / expansions
- routed intent
- FTS expression
- top FTS results
- top vector results
- merged / reranked output
- filtered-source fallback output

### Why this matters
This will greatly reduce guesswork when debugging failures.

---

## K. Add non-regression tests for this exact failure mode

This is mandatory.

### Test category 1: router tests
Input examples:

- `What are the references requiring the crew members to report fatigue events or occurrences?`
- `Where is crew fatigue listed as a reportable occurrence?`
- `Does occurrence reporting mention crew fatigue?`

Expected:
- routed to the new occurrence-reporting intent, or at minimum to a path that applies occurrence-reporting boosts.

### Test category 2: retrieval recall tests
Expected top-N should contain at least one of:

- `ANNEX I — OCCURRENCES RELATED TO THE OPERATION OF THE AIRCRAFT`
- `Article 4`
- `CAT.GEN.MPA.100`

And for the most specific query:

- `Crew fatigue impacting or potentially impacting ...`

the Annex I result should be top-ranked or near top-ranked.

### Test category 3: snippet extraction tests
Ensure a query like:

- `crew fatigue impacting or potentially impacting`

returns the exact line from Annex I.

### Test category 4: ambiguity tests
Ensure `ANNEX I` scoped search in `occurrence-reporting` can distinguish:

- the requirements annex
- the occurrence list annex

---

## L. Add documentation for retrieval limitations and intended behavior

User-facing / maintainer-facing docs should explain:

- when `refs` is best
- when `snippets` is best
- when `hybrid` is best
- that list-heavy annexes may require source scoping today
- what the improved occurrence-reporting behavior is expected to do after implementation

---

## Revised implementation plan

The original plan was revised after critical analysis. The domain-specific
intent routing and manual query expansion approaches (sections A-B) were
deprioritized in favor of **structural fixes that solve the problem
generically** across all source types.

### What was implemented (single sprint)

| # | Fix | Files changed |
|---|-----|---------------|
| 1 | **Sub-entry list-item chunking** — long entries containing numbered items `(1)`, `(2)`, etc. are split into individual retrieval-unit chunks in addition to the whole-entry chunk. This is the structural fix: a focused chunk like `"(11) Crew fatigue impacting..."` scores much higher in vector search than a 2000-char annex blob. | `retrieval/chunking.py`, `retrieval/indexing.py` |
| 2 | **Source-scoped retrieval (`--slug`)** — all search commands (`refs`, `snippets`, `hybrid`) accept an optional `--slug` parameter to restrict results to a specific source document. This works for both FTS and vector search. | `retrieval/exact.py`, `retrieval/snippets.py`, `retrieval/vector.py`, `retrieval/hybrid.py`, `retrieval/service.py`, `cli/__init__.py` |
| 3 | **Generic source-aware boosting** — after merging FTS + vector results, hybrid search applies a small score boost when query words overlap with a result's source slug (e.g. query "occurrence reporting" boosts results from `occurrence-reporting`). No domain-specific configuration needed. | `retrieval/hybrid.py` |
| 4 | **Non-regression tests** — 14 fixture-backed tests covering chunking behavior, source-scoped search, boosting logic, and hybrid slug filtering. | `tests/test_retrieval_gap_fix.py` |

### What was NOT implemented (and why)

- **Domain-specific intent routing** (original section A) — would become a patchwork of per-domain heuristics. The structural fixes (chunking + scoping) solve the problem generically.
- **Manual query expansion** (original section B) — fragile and expensive to maintain. Sub-entry chunking removes the need.
- **Domain-specific reranker** (original section D) — over-specified for this case. A cross-encoder reranker could be added later as a generic second-stage.
- **Annex normalization metadata** (original section I) — nice-to-have but not required now that source scoping and list-item chunks exist.
- **Debug-search command** (original section J) — deferred to a future sprint; `--slug` scoping already covers the most common debugging scenario.

### Possible future improvements

1. **Chunk-level FTS** — a second FTS5 virtual table indexed on `entry_chunks.chunk_text` would improve lexical recall for sub-entry items. Currently only vector search benefits from list-item chunks.
2. **Cross-encoder reranker** — a lightweight `ms-marco-MiniLM` reranker as a generic second-stage pass.
3. **Entry-type filtering** — add `--entry-type IR|AMC|GM|INFO` to search commands.

---

## Concrete acceptance criteria

Implementation should be considered successful when all of the following are true.

### Acceptance criterion 1
Query:

> `What are the references requiring the crew members to report fatigue events or occurrences?`

Should return, within top results:

- `Article 4` (occurrence-reporting)
- `ANNEX I — OCCURRENCES RELATED TO THE OPERATION OF THE AIRCRAFT`
- `CAT.GEN.MPA.100`

### Acceptance criterion 2
Scoped query:

> `claw-easa snippets "crew fatigue" --slug occurrence-reporting`

Should return Annex I containing the fatigue line.

### Acceptance criterion 3
Query:

> `Crew fatigue impacting or potentially impacting their ability to perform safely their flight duties`

Should surface the exact Annex I entry as top or near-top result (especially in vector/hybrid with list-item chunks).

### Acceptance criterion 4
Scoped query on `occurrence-reporting` should not return entries from other sources.

### Acceptance criterion 5
All 14 non-regression tests pass (`pytest tests/test_retrieval_gap_fix.py`).

---

## Appendix: precise failure statement

The system failed not because the phrase was absent, but because:

- the question was posed as an obligation search,
- the key answer lived inside an annex occurrence list item,
- the retrieval layer favored more obviously narrative reporting provisions,
- and annex ambiguity plus a weak manual verification method hid the correct record.

This is a classic **recall + ranking + granularity** problem in regulatory retrieval.
The fix addresses granularity (list-item chunks), scope (source filtering), and
ranking (source-aware boosting).
