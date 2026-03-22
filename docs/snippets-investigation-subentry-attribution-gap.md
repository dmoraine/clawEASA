# Investigation brief: `snippets` retrieval and sub-entry attribution gap

## Objective

Provide a precise, implementation-oriented analysis of the current `snippets` behavior in `clawEASA`, with special attention to regulatory texts that are physically present in the corpus but are surfaced under the wrong parent reference or with insufficient granularity.

This note is intended to help another coding agent investigate and fix the issue efficiently.

---

## Executive summary

There is a real retrieval / presentation gap in the current `snippets` path.

The problem is **not** that the text is missing from the database.
The problem is that `snippets` currently queries and returns **whole `regulation_entries`**, while the project now also stores **finer retrieval units in `entry_chunks`** (including `list_item` chunks).

As a result, a query can:

1. successfully match text that exists deep inside a long entry,
2. but be returned under the parent `entry_ref` / `title`,
3. and be formatted using the whole entry body,
4. which can hide the real logical sub-reference the user actually needs.

For regulatory use, this is a serious UX and correctness problem because it can lead to:

- correct text under the **wrong apparent reference**,
- missed or weak legal attribution,
- false impression that a provision is absent,
- manual misinterpretation of authority level,
- lower trust in the tool.

---

## Concrete motivating example

The following phrase was initially missed / weakly attributed during analysis:

> **In the case of a change of home base, the first recurrent extended recovery rest period prior to starting duty at the new home base is increased to 72 hours, including 3 local nights. Travelling time between the former home base and the new home base is positioning.**

This text is present in the local corpus under the `air-ops` source, and specifically appears as part of:

- **CS FTL.1.200 Home base**

However, current retrieval behavior can surface it under a much broader parent entry, notably an entry displayed as:

- `AMC1 ORO.FTL.250 Fatigue management training`

with the matched text buried later in the body, where `CS FTL.1.200 Home base` appears inline.

This creates the wrong user impression:

- the text is found,
- but it appears to belong to the wrong top-level regulatory label.

---

## What was observed

### Observed command behavior

Commands such as:

```bash
claw-easa snippets "change of home base 72 hours including 3 local nights" --slug air-ops
claw-easa snippets "Travelling time between the former home base and the new home base is positioning" --slug air-ops
```

returned or emphasized a result under:

- `AMC1 ORO.FTL.250 Fatigue management training`

But database inspection showed the phrase inside the body where a nested section appears:

- `CS FTL.1.200 Home base`

### Key implication

This means the issue is **not primarily a missing-ingestion problem**.
It is a mismatch between:

- the granularity at which the content is stored for retrieval,
- and the granularity at which `snippets` currently returns and formats results.

---

## Current code path (relevant files)

### Retrieval
- `src/claw_easa/retrieval/snippets.py`
- `src/claw_easa/retrieval/queries.py`
- `src/claw_easa/retrieval/fts_compat.py`

### Chunking / indexing
- `src/claw_easa/retrieval/chunking.py`
- `src/claw_easa/retrieval/indexing.py`
- `src/claw_easa/retrieval/vector.py`

### Answer formatting
- `src/claw_easa/retrieval/answering.py`

### Schema
- `src/claw_easa/db/schema.sql`

### Tests already relevant
- `tests/test_retrieval_gap_fix.py`
- `tests/test_retrieval_sqlite.py`

---

## Current `snippets` implementation: why it is structurally limited

## 1. `search_snippets()` queries `regulation_entries`, not `entry_chunks`

Current implementation (`src/claw_easa/retrieval/snippets.py`) builds SQL around:

- `entries_fts`
- `regulation_entries e`
- `source_documents d`

It returns fields such as:

- `e.entry_ref`
- `e.entry_type`
- `e.title`
- `e.body_text`

### Important observation
The query **does not query `entry_chunks`**.

So even though the system now builds:

- `whole` chunks
- `list_item` chunks

for indexing purposes, the `snippets` path is still effectively operating at the **whole-entry level**.

### Consequence
If the best match exists inside a fine-grained list item or nested subsection inside a long body:

- `snippets` still returns the parent entry,
- not the sub-entry / chunk,
- and not the smallest logical regulatory unit.

---

## 2. `format_snippets_answer()` prefers `body_text` over `chunk_text`

In `src/claw_easa/retrieval/answering.py`, `format_snippets_answer()` currently does:

```python
snippet = compact_snippet(row.get('body_text', ''), max_lines=3, max_chars=280)
```

### Important observation
It does **not** do the more robust fallback used elsewhere:

```python
row.get('body_text') or row.get('chunk_text', '')
```

### Consequence
Even if `search_snippets()` were upgraded later to return chunk rows, the current formatter would still bias toward whole-entry text unless updated.

This is probably part of why the output remains anchored to parent entry bodies.

---

## 3. The project already contains chunk infrastructure that `snippets` is not exploiting

### Evidence from codebase
- `entry_chunks` exists in schema
- `build_list_item_chunks()` exists
- `build_whole_entry_chunk()` exists
- vector retrieval already joins `entry_chunks`
- `status` reports counts such as:
  - whole chunks
  - list-item chunks

### Chunking behavior
`src/claw_easa/retrieval/chunking.py` creates:

- one whole-entry chunk per entry
- multiple `list_item` chunks for long entries with numbered list items

Each chunk includes:
- `entry_id`
- `chunk_index`
- `chunk_kind`
- `breadcrumbs_text`
- `chunk_text`

### Consequence
The architecture already supports finer-grained retrieval units.
The `snippets` path simply does not make use of them yet.

---

## Why this matters particularly for EASA material

Regulatory texts often embed important content inside:

- annex lists
- enumerated sub-points
- embedded CS / AMC / GM blocks
- nested headings inside long imported bodies
- structured but flattened source material

This means that the **legal unit of meaning** is often much smaller than the parent database entry.

For example, users care about:

- a specific item `(11)` in an Annex,
- a particular subparagraph,
- a specific `CS FTL.1.200` clause,
- not the entire parent text blob in which it happens to be stored.

So this is not a cosmetic issue. It directly affects:

- citation quality,
- legal attribution,
- audit correctness,
- user trust.

---

## Root-cause hypotheses

## Hypothesis A — `snippets` is still whole-entry-first by design

This is strongly supported by the code.

`search_snippets()` queries only:

- `entries_fts`
- `regulation_entries`

So the result unit is fundamentally a whole entry.

### Likelihood
Very high.

---

## Hypothesis B — chunk-level improvements were added for indexing/vector retrieval, but not propagated to `snippets`

This also appears strongly supported:

- chunk tables exist
- chunk building exists
- vector search uses chunks
- `snippets` still does not

### Likelihood
Very high.

---

## Hypothesis C — some imported entries are semantically overloaded containers

The motivating example suggests that one parent entry body may contain multiple logical sub-sections, including:

- `AMC1 ORO.FTL.250 ...`
- then embedded `CS FTL.1.200 Home base`
- then the target phrase

If so, even whole-entry chunking is too coarse for clean snippet attribution.

### Likelihood
High.

---

## Hypothesis D — `list_item` chunking alone may not be enough for nested sub-heading attribution

The current list-item chunker splits only numbered items using this regex pattern:

```python
_LIST_ITEM_RE = re.compile(r'^[ \t]*[\-\u2013\u2022]?\s*\((\d+)\)\s+', re.MULTILINE)
```

This is good for `(1)`, `(2)`, `(11)`-style lists.

But it does **not** explicitly split on nested in-body sub-headings like:

- `CS FTL.1.200 Home base`
- `GM1 ...`
- `AMC1 ...`
- `Article 4`

if those appear embedded inside a flattened body.

### Consequence
Even chunk-level retrieval could still produce chunks whose displayed parent reference is broader than the logical sub-reference.

### Likelihood
Medium to high.

---

## Reproducing the problem step by step

An investigating agent should reproduce with:

```bash
claw-easa status
claw-easa snippets "change of home base 72 hours including 3 local nights" --slug air-ops
claw-easa snippets "Travelling time between the former home base and the new home base is positioning" --slug air-ops
```

Then compare with direct DB inspection:

```sql
SELECT d.slug, e.entry_ref, e.entry_type, e.title, e.body_text
FROM regulation_entries e
JOIN source_documents d ON d.id = e.document_id
WHERE d.slug='air-ops' AND lower(e.body_text) LIKE '%former home base%';
```

And then inspect chunks:

```sql
SELECT ec.id, ec.chunk_kind, ec.chunk_index, ec.chunk_text
FROM entry_chunks ec
JOIN regulation_entries e ON e.id = ec.entry_id
JOIN source_documents d ON d.id = e.document_id
WHERE d.slug='air-ops' AND lower(ec.chunk_text) LIKE '%former home base%';
```

This will show whether the chunk table already contains a better retrieval unit than the current snippet code returns.

---

## Concrete code-level problems to investigate

## Problem 1 — `SNIPPET_SEARCH_FTS_SQL` ignores `entry_chunks`

Current query in `src/claw_easa/retrieval/queries.py`:

```sql
SELECT e.id, e.entry_ref, e.entry_type, e.title, e.body_text, ...
FROM entries_fts fts
JOIN regulation_entries e ON e.id = fts.rowid
...
```

### Why this is a problem
It guarantees that the search result unit is the parent entry, not the best matching chunk.

### Investigation question
Should `snippets` query a chunk-level FTS index instead?
Or should it perform a second-stage chunk localization after entry retrieval?

---

## Problem 2 — no snippet-localization step inside matched entry

Even if the system continues to retrieve at entry level first, it currently does not appear to do a second pass such as:

- find the closest matching chunk within the entry,
- detect nearest nested heading,
- attribute snippet to sub-reference.

### Investigation question
Could a lightweight localization step within `body_text` dramatically improve output without changing schema?

---

## Problem 3 — formatter does not display nested match context

Current source line format is:

```python
- {entry_ref} ({entry_type}) — {title} [{slug}]
```

### Why this is insufficient
If the true match is under a nested in-body section, the user never sees that.

### Investigation question
Can we augment rows with:

- `matched_subref`
- `matched_heading`
- `chunk_kind`
- `chunk_index`

and render them?

Example desired output:

```text
- AMC1 ORO.FTL.250 (AMC) — Fatigue management training [air-ops]
  matched inside: CS FTL.1.200 Home base
  "(b) In the case of a change of home base ..."
```

or ideally:

```text
- CS FTL.1.200 (CS) — Home base [air-ops]
  "(b) In the case of a change of home base ..."
```

---

## Problem 4 — no preference for smallest matching unit

In a system with both whole-entry and list-item chunks, snippet retrieval should likely prefer:

1. exact phrase hit in a `list_item` chunk
2. exact phrase hit in another fine-grained chunk
3. whole-entry hit

Current whole-entry-only logic prevents this.

### Investigation question
Would a ranking rule like this solve most cases?

- exact phrase match in `list_item` chunk >
- FTS match in `list_item` chunk >
- exact phrase in whole entry >
- FTS whole entry

---

## Proposed improvement paths

## Path A — minimum viable fix (fastest)

### Goal
Improve `snippets` without a major schema rewrite.

### Steps
1. Keep current entry-level snippet retrieval.
2. After entry retrieval, run a secondary scan against `entry_chunks` for each returned `entry_id`.
3. If a matching chunk is found:
   - attach `chunk_text`
   - attach `chunk_kind`
   - attach `chunk_index`
   - use `chunk_text` for snippet display instead of `body_text`
4. Update formatter to prefer `chunk_text`.
5. Add nested match context when chunk is finer than whole entry.

### Pros
- low-risk
- probably enough to fix many cases
- easy to test

### Cons
- still entry-first
- may miss chunk-only hits that whole-entry FTS did not rank highly

---

## Path B — proper snippet retrieval over chunks (recommended)

### Goal
Make `snippets` operate on the same fine-grained units the index now supports.

### Steps
1. Create a chunk-level FTS index or equivalent searchable path for `entry_chunks.chunk_text`.
2. Query chunks directly in `search_snippets()`.
3. Join back to parent `regulation_entries` only for metadata.
4. Return rows including both:
   - parent reference fields
   - chunk fields
5. Rank by:
   - phrase match quality
   - chunk granularity (`list_item` preferred over `whole`)
   - slug scope bonus
6. Format snippets from `chunk_text`, not `body_text`.

### Pros
- aligns retrieval unit with chunk indexing architecture
- strongest long-term solution
- best for annexes / enumerated items

### Cons
- requires more code changes
- possibly schema / migration work if chunk FTS table does not yet exist

---

## Path C — smarter heading-aware chunking (best long-term)

### Goal
Not only split list items, but also split on nested regulatory headings embedded in flattened bodies.

### Needed capability
Detect in-body sub-headings such as:

- `CS FTL.1.200 Home base`
- `AMC1 ...`
- `GM1 ...`
- `Article 4`
- `Annex I ...`

and create chunk/sub-entry units aligned with those boundaries.

### Why this matters
The motivating example suggests that some entries contain multiple logical regulatory units in a single flattened body. List-item chunking helps, but does not fully solve heading-level attribution.

### Pros
- best legal attribution quality
- would improve lookup, snippets, and maybe refs

### Cons
- highest implementation effort
- requires careful parser / chunker design

---

## Recommended implementation order

## Phase 1 — high-value, low-risk

1. Update `format_snippets_answer()` to prefer:
   - `row.get('chunk_text') or row.get('body_text', '')`
2. Add chunk enrichment after entry retrieval in `search_snippets()`.
3. Prefer exact matching `list_item` chunk when available.
4. Expose `chunk_kind` and `chunk_index` in returned rows.
5. Add a rendered line like `matched inside: ...` if a sub-reference can be inferred.

## Phase 2 — robust retrieval improvement

6. Implement chunk-level snippet retrieval.
7. Add ranking preference for smallest matching unit.
8. Add source-scoped chunk search.

## Phase 3 — structural refinement

9. Add heading-aware sub-entry chunking.
10. Add true sub-reference attribution when nested headers exist inside a flattened body.

---

## Specific test cases that should be added

## Test 1 — direct phrase on nested FTL home base text

Query:

```text
change of home base 72 hours including 3 local nights
```

Expected:
- `snippets` should return a result that clearly exposes:
  - `CS FTL.1.200 Home base`
- not merely a broad parent such as `AMC1 ORO.FTL.250 ...`

## Test 2 — phrase on occurrence-reporting list item

Query:

```text
Crew fatigue impacting or potentially impacting their ability to perform safely their flight duties
```

Expected:
- result should prefer the specific occurrence list chunk
- output should not drown the item inside a broad annex blob

## Test 3 — chunk preference

Given both:
- whole-entry chunk match
- list-item chunk match

Expected:
- `snippets` prefers `list_item`

## Test 4 — scoped snippet search

Query:

```text
crew fatigue
```

with:

```text
--slug occurrence-reporting
```

Expected:
- no unrelated source families
- result should surface the occurrence-reporting annex item clearly

## Test 5 — formatting fallback

If row contains `chunk_text`, snippet formatter must use it preferentially.

---

## Suggested investigation checklist for another agent

1. Read:
   - `src/claw_easa/retrieval/snippets.py`
   - `src/claw_easa/retrieval/queries.py`
   - `src/claw_easa/retrieval/chunking.py`
   - `src/claw_easa/retrieval/vector.py`
   - `src/claw_easa/retrieval/answering.py`
2. Inspect schema for `entry_chunks` and any chunk-search capability.
3. Verify whether chunk FTS exists; if not, estimate effort to add it.
4. Reproduce the home-base phrase failure.
5. Compare whole-entry vs chunk-level match quality.
6. Implement the smallest safe fix first.
7. Add regression tests before / alongside patch.

---

## Practical interpretation of the issue

This is best described as:

> a **sub-entry attribution gap** in `snippets`

not merely a ranking bug.

The system finds text that exists, but it can present that text under a parent regulatory wrapper that is too broad or misleading.

In a regulatory auditing context, that is enough to create incorrect conclusions.

---

## Final recommendation

The minimum acceptable fix is:

- make `snippets` prefer chunk-level text when available,
- and render that chunk-level context clearly.

The better long-term fix is:

- make `snippets` a true chunk-aware retrieval path,
- with ranking that prefers the smallest legally meaningful matching unit,
- and, where possible, explicit nested sub-reference attribution.

That is the most direct way to reduce future misses and misattributions of EASA regulatory text.

---

## Implementation status (2026-03-22)

The following fixes have been implemented:

### 1. Query-aware `compact_snippet` (formatting.py)
`compact_snippet()` now accepts an optional `query` parameter. When provided,
it locates the lines with the highest word overlap and extracts a contextual
window, instead of always taking the first N lines. This alone resolves the
majority of the presentation problem across all retrieval paths.

### 2. Sub-heading chunking (chunking.py)
New `build_subheading_chunks()` function detects embedded regulatory
sub-headings (CS, AMC, GM) within long entries and creates separate indexed
chunks at the sub-heading boundary. Each chunk carries the sub-heading in its
breadcrumbs for proper attribution.

### 3. Chunk enrichment in `search_snippets` (snippets.py)
After entry-level FTS retrieval, `search_snippets()` now performs a secondary
scan of `entry_chunks` to find the best matching sub-chunk (list-item or
subheading). When found, the row is enriched with `chunk_text`,
`chunk_kind`, and `matched_subref` (for subheading chunks).

### 4. Display updates (answering.py, cli/__init__.py)
All snippet formatting now:
- Prefers `chunk_text` over `body_text` when available
- Passes the query to `compact_snippet` for query-aware windowing
- Displays `[matched inside: CS FTL.1.200 Home base]` when a
  sub-heading chunk matched

### Tests
13 new tests in `tests/test_snippets_subentry.py` covering:
- Query-aware snippet windowing
- Sub-heading chunk creation
- Chunk enrichment with matched_subref
- Sub-reference extraction
