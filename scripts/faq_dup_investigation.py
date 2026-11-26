#!/usr/bin/env python3
"""Read-only, deterministic measurement of FAQ duplication in a claw-easa data dir.

The report prints aggregates and entry_ref ids only, never question or answer
text.  The database is opened read-only (``mode=ro`` URI, ``query_only``), which
still reads a live WAL correctly but may create ``-shm``/``-wal`` files next to a
WAL-mode database.  ``--immutable`` adds ``immutable=1`` so nothing is ever
created: use --immutable only on a pristine snapshot with no writer.

Normalisation rule N1 (``normalise``):
    Unicode NFKC, casefold, collapse every run of whitespace to one space,
    then strip leading/trailing characters that are whitespace or of Unicode
    category P* (punctuation).  No stemming.

Hash definition:
    hash  = sha256(normalise(title) + "\\n" + normalise(body_text)).hexdigest()
    hash8 = hash[:8]

The default mode uses the standard library only.  ``--overfetch`` lazily
imports claw_easa, numpy, faiss and sentence_transformers.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import re
import sqlite3
import sys
import unicodedata
import urllib.parse
from collections import Counter, defaultdict
from collections.abc import Callable
from pathlib import Path

REQUEST_FIGURES = {
    "redundant": 1137,
    "duplicated_texts": 778,
    "histogram": {"2": 454, "3": 311, "4": 4, "6": 8, "10": 1},
    "intra_domain_redundant": 34,
}
PARSER_NOTE = (
    "faq_parser.parse_faq_page keeps every div.faq-child with an h4 and a body; "
    "it has no de-duplication and discards the category"
)
LADDER = [10, 15, 20, 30, 40, 60, 80, 100, 120, 200, 300, 500]
SCHEDULE = [3 * 10, 60, 100]  # over-fetch schedule: K0 = 3 * top_k, then 60, cap 100
FAQ_SQL = (
    "SELECT e.id, e.entry_ref, e.title, e.body_text, d.slug "
    "FROM regulation_entries e JOIN source_documents d ON d.id = e.document_id "
    "WHERE e.entry_type = 'FAQ' ORDER BY e.id"
)
_WS_RE = re.compile(r"\s+")
_NON_ALNUM_RE = re.compile(r"[^a-z0-9]")
_POS_RE = re.compile(r"-(\d+)$")
_LEGACY_REF_RE = re.compile(r"^FAQ-(.+)-(\d{3,})$")

Faq = dict  # keys: id, ref, domain, title, body, hash


# ── Normalisation ──────────────────────────────────────────────────────────

def _strippable(ch: str) -> bool:
    return ch.isspace() or unicodedata.category(ch).startswith("P")


def normalise(text: str | None) -> str:
    """Rule N1 (see module docstring)."""
    t = _WS_RE.sub(" ", unicodedata.normalize("NFKC", text or "").casefold())
    start, end = 0, len(t)
    while start < end and _strippable(t[start]):
        start += 1
    while end > start and _strippable(t[end - 1]):
        end -= 1
    return t[start:end]


def n1_hash(title: str, body: str) -> str:
    return hashlib.sha256(f"{normalise(title)}\n{normalise(body)}".encode()).hexdigest()


def _lower_ws(s: str) -> str:
    return " ".join(s.lower().split())


def _alnum(s: str) -> str:
    return _NON_ALNUM_RE.sub("", s.casefold())


VARIANTS: dict[str, Callable[[str, str], str]] = {
    "a_raw": lambda t, b: f"{t}\n{b}",
    "b_lower_ws": lambda t, b: f"{_lower_ws(t)}\n{_lower_ws(b)}",
    "c_n1": lambda t, b: f"{normalise(t)}\n{normalise(b)}",
    "d_n1_body": lambda t, b: normalise(b),
    "e_alnum_body": lambda t, b: _alnum(b),
    "f_alnum_title_body": lambda t, b: f"{_alnum(t)}\n{_alnum(b)}",
}


# ── Data access ────────────────────────────────────────────────────────────

def connect_ro(db_path: Path, immutable: bool = False) -> sqlite3.Connection:
    path = urllib.parse.quote(str(db_path.resolve()))
    flags = "mode=ro&immutable=1" if immutable else "mode=ro"
    conn = sqlite3.connect(f"file:{path}?{flags}", uri=True)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA query_only = ON")
    return conn


def load_faqs(conn: sqlite3.Connection) -> list[Faq]:
    faqs = []
    for r in conn.execute(FAQ_SQL):
        title, body = r["title"] or "", r["body_text"] or ""
        faqs.append({"id": r["id"], "ref": r["entry_ref"], "domain": r["slug"],
                     "title": title, "body": body, "hash": n1_hash(title, body)})
    return faqs


# ── Pure helpers ───────────────────────────────────────────────────────────

def histogram(counts: Counter) -> dict[str, int]:
    hist = Counter(c for c in counts.values() if c > 1)
    return {str(k): hist[k] for k in sorted(hist)}


def dup_stats(faqs: list[Faq], key: Callable[[str, str], str]) -> dict:
    keys = [key(f["title"], f["body"]) for f in faqs]
    counts = Counter(keys)
    per_domain: dict[str, list[str]] = defaultdict(list)
    for f, k in zip(faqs, keys):
        per_domain[f["domain"]].append(k)
    return {
        "distinct": len(counts),
        "redundant": len(keys) - len(counts),
        "duplicated_texts": sum(c > 1 for c in counts.values()),
        "histogram": histogram(counts),
        "intra_domain_redundant": sum(len(v) - len(set(v)) for v in per_domain.values()),
    }


def hash_sets(faqs: list[Faq]) -> dict[str, set[str]]:
    sets: dict[str, set[str]] = defaultdict(set)
    for f in faqs:
        sets[f["domain"]].add(f["hash"])
    return sets


def position(ref: str) -> int | None:
    m = _POS_RE.search(ref)
    return int(m.group(1)) if m else None


def type_counts(rows: list[dict]) -> dict[str, int]:
    return dict(sorted(Counter(r["entry_type"] for r in rows).items()))


# ── Sections ───────────────────────────────────────────────────────────────

def section_corpus(conn: sqlite3.Connection, faqs: list[Faq]) -> dict:
    by_type = {r[0]: r[1] for r in conn.execute(
        "SELECT entry_type, COUNT(*) FROM regulation_entries GROUP BY entry_type ORDER BY entry_type")}
    try:
        versions = [r[0] for r in conn.execute("SELECT version FROM schema_migrations ORDER BY version")]
    except sqlite3.OperationalError:
        versions = ["(no schema_migrations table)"]
    try:
        builds = conn.execute("SELECT COUNT(*) FROM corpus_builds").fetchone()[0]
    except sqlite3.OperationalError:
        builds = 0

    def count(sql: str) -> int:
        return conn.execute(sql).fetchone()[0]

    faq_doc = "SELECT COUNT(*) FROM source_documents WHERE source_family = 'faq' AND "
    faq_chunks = ("SELECT COUNT(*) FROM entry_chunks ec "
                  "JOIN regulation_entries e ON e.id = ec.entry_id WHERE e.entry_type = 'FAQ'")
    return {
        "entries_by_type": by_type,
        "faq_entries": len(faqs),
        "faq_domains": len({f["domain"] for f in faqs}),
        "schema_migrations": versions,
        "faq_empty_body": sum(not f["body"].strip() for f in faqs),
        "source_families": {r[0]: r[1] for r in conn.execute(
            "SELECT source_family, COUNT(*) FROM source_documents "
            "GROUP BY source_family ORDER BY source_family")},
        "faq_documents_with_revision": count(faq_doc + "TRIM(COALESCE(revision, '')) != ''"),
        "faq_documents_with_published_at": count(
            faq_doc + "TRIM(COALESCE(published_at, '')) != ''"),
        "corpus_builds_rows": builds,
        "entry_chunks_faq": count(faq_chunks),
        "entry_chunks_total": count("SELECT COUNT(*) FROM entry_chunks"),
        "faiss_mapping_rows": count("SELECT COUNT(*) FROM faiss_mapping"),
        "faq_chunks_starting_with_entry_ref": count(
            faq_chunks + " AND substr(ec.chunk_text, 1, length(e.entry_ref)) = e.entry_ref"),
    }


def section_duplication(faqs: list[Faq]) -> dict:
    counts = Counter(f["hash"] for f in faqs)
    domains: dict[str, set[str]] = defaultdict(set)
    for f in faqs:
        domains[f["hash"]].add(f["domain"])
    return {
        "faq_entries": len(faqs),
        "distinct_hashes": len(counts),
        "redundant_copies": len(faqs) - len(counts),
        "multiplicity_histogram": histogram(counts),
        "multi_domain_hashes": sum(len(d) > 1 for d in domains.values()),
        "single_domain_duplicated_hashes": sum(
            counts[h] > 1 and len(d) == 1 for h, d in domains.items()),
    }


def section_variants(variant_stats: dict[str, dict]) -> dict:
    fields = ("redundant", "duplicated_texts", "histogram", "intra_domain_redundant")
    matching = [name for name, s in variant_stats.items()
                if all(s[k] == REQUEST_FIGURES[k] for k in fields)]
    return {**variant_stats, "request_figures": REQUEST_FIGURES,
            "reproduced_by": ", ".join(matching) or "none"}


def section_domains(faqs: list[Faq], min_size: int, focus: list[str]) -> dict:
    sizes = Counter(f["domain"] for f in faqs)
    hsets = hash_sets(faqs)
    doms = sorted(d for d in hsets if sizes[d] >= min_size)
    strict = equal = 0
    partial: list[tuple] = []
    parents: dict[str, list[str]] = {d: [] for d in doms}
    for a in doms:
        for b in doms:
            ha, hb = hsets[a], hsets[b]
            if a == b:
                continue
            if ha == hb:
                equal += a < b
            elif ha < hb:
                strict += 1
                parents[a].append(b)
            elif a < b and not hb < ha:
                k = len(ha & hb)
                if k:
                    partial.append((-k, a, b, k))
    direct = []
    for child, ps in parents.items():
        best = [p for p in ps if not any(hsets[q] < hsets[p] for q in ps)]
        if best:
            direct.append(f"{child} -> {', '.join(sorted(best))}")
    partial.sort()
    out: dict = {
        "min_domain_size": min_size,
        "domains_considered": len(doms),
        "full_inclusion_pairs": strict + equal,
        "equal_pairs": equal,
        "direct_parent": direct,
        "true_partial_pairs": len(partial),
        "true_partial_overlaps": [
            f"{a}({len(hsets[a])}) ~ {b}({len(hsets[b])}): shared {k} = "
            f"{100 * k / len(hsets[a]):.1f}% of {a}, {100 * k / len(hsets[b]):.1f}% of {b}"
            for _, a, b, k in partial],
    }
    if not focus:
        out["focus"] = "skipped (no --focus-domain)"
        return out
    lines = []
    present = [f for f in focus if f in hsets]
    for f in focus:
        if f not in hsets:
            lines.append(f"{f}: NOT FOUND")
            continue
        shared = " ".join(f"{g}:{len(hsets[f] & hsets[g])}" for g in present if g != f)
        lines.append(f"{f}: size={sizes[f]} distinct={len(hsets[f])} shared=[{shared}]")
    for f in present:
        for g in present:
            if f != g:
                lines.append(f"{f} subset_of {g}: {'yes' if hsets[f] <= hsets[g] else 'no'}")
    out["focus"] = lines
    return out


def section_intra(faqs: list[Faq], variant_stats: dict[str, dict]) -> dict:
    groups: dict[tuple[str, str], list[Faq]] = defaultdict(list)
    for f in faqs:  # faqs are sorted by id, so g[0] is the smallest id
        groups[(f["domain"], f["hash"])].append(f)
    dups = sorted((g for g in groups.values() if len(g) > 1),
                  key=lambda g: (g[0]["domain"], g[0]["id"]))

    def consecutive(g: list[Faq]) -> bool:
        pos = sorted(p for p in (position(f["ref"]) for f in g) if p is not None)
        return any(b - a == 1 for a, b in zip(pos, pos[1:]))

    return {
        "intra_domain_redundant": sum(len(g) - 1 for g in dups),
        "intra_domain_duplicated_hashes": len(dups),
        "examples": [f"{g[0]['domain']}: {', '.join(f['ref'] for f in g)}" for g in dups[:3]],
        "consecutive_position_pairs": sum(consecutive(g) for g in dups),
        "raw_identical": sum(len({(f["title"], f["body"]) for f in g}) == 1 for g in dups),
        "body_only_extra": (variant_stats["d_n1_body"]["intra_domain_redundant"]
                            - variant_stats["c_n1"]["intra_domain_redundant"]),
        "parser_note": PARSER_NOTE,
    }


def section_same_title(faqs: list[Faq]) -> dict:
    by_title: dict[str, list[Faq]] = defaultdict(list)
    for f in faqs:
        by_title[normalise(f["title"])].append(f)
    groups = sorted(
        (sorted(f["ref"] for f in g), sorted(len(f["body"]) for f in g))
        for g in by_title.values() if len({f["hash"] for f in g}) > 1)
    return {
        "groups": len(groups),
        "group_list": [f"refs=[{', '.join(refs)}] body_lengths={lens}" for refs, lens in groups],
    }


def section_refs(conn: sqlite3.Connection, faqs: list[Faq], prefix: str | None) -> dict:
    if not prefix:
        return {"skipped": "no --ref-prefix"}
    hash_by_id = {f["id"]: f["hash"] for f in faqs}
    base = "SELECT DISTINCT faq_entry_id FROM faq_regulation_refs WHERE "

    def measure(where: str, params: tuple) -> dict:
        ids = {r[0] for r in conn.execute(base + where, params)} & hash_by_id.keys()
        hashes = {hash_by_id[i] for i in ids}
        return {"faq_entries": len(ids), "distinct_hashes": len(hashes),
                "redundant": len(ids) - len(hashes)}

    # substr() comparison is case-sensitive and has no LIKE wildcards to escape.
    return {
        "ref_prefix": prefix,
        "prefix_match": measure("substr(target_ref, 1, ?) = ?", (len(prefix), prefix)),
        "strict_match": measure("target_ref = ? OR substr(target_ref, 1, ?) = ?",
                                (prefix, len(prefix) + 1, prefix + ".")),
    }


def section_regression(faqs: list[Faq], refs: list[str]) -> dict:
    if not refs:
        return {"skipped": "no --regression-ref"}
    by_ref: dict[str, Faq] = {}
    for f in faqs:
        by_ref.setdefault(f["ref"], f)
    out: dict = {}
    hashes = set()
    for ref in refs:
        f = by_ref.get(ref)
        if f is None:
            out[ref] = "NOT FOUND"
        else:
            hashes.add(f["hash"])
            out[ref] = f"found=yes domain={f['domain']} hash8={f['hash'][:8]}"
    all_found = all(r in by_ref for r in refs)
    out["same_hash"] = "yes" if all_found and len(hashes) == 1 else "no"
    return out


def section_canonical(faqs: list[Faq], focus: list[str]) -> dict:
    sizes = Counter(f["domain"] for f in faqs)
    hash_domains: dict[str, set[str]] = defaultdict(set)
    for f in faqs:
        hash_domains[f["hash"]].add(f["domain"])
    multi = {h: ds for h, ds in sorted(hash_domains.items()) if len(ds) > 1}
    canon: dict[str, str] = {}
    ties = 0
    for h, ds in multi.items():
        smallest = min(sizes[d] for d in ds)
        candidates = sorted(d for d in ds if sizes[d] == smallest)
        ties += len(candidates) > 1
        canon[h] = candidates[0]

    def top(values: list[str], n: int) -> dict[str, int]:
        return dict(sorted(Counter(values).items(), key=lambda kv: (-kv[1], kv[0]))[:n])

    out: dict = {
        "rule": "fewest FAQ rows in the domain; tie -> alphabetical slug",
        "multi_domain_groups": len(multi),
        "canonical_distribution_top15": top(list(canon.values()), 15),
        "ties_resolved_alphabetically": ties,
        "rule_total_order": "yes",  # ties are broken alphabetically
    }
    if not focus:
        out["focus"] = "skipped (no --focus-domain)"
        return out
    order = sorted(focus, key=lambda d: (sizes[d], d))
    with_all = [h for h, ds in multi.items() if set(focus) <= ds]
    out["focus_order"] = " < ".join(f"{d}({sizes[d]})" for d in order)
    out["groups_with_all_focus"] = len(with_all)
    out["focus_canonical_distribution"] = top([canon[h] for h in with_all], len(sizes))
    return out


def _id_prefix(f: Faq) -> str:
    m = _LEGACY_REF_RE.match(f["ref"])
    # A ref outside the legacy pattern has no prefix: fall back to the slug minus "faq-".
    return m.group(1) if m else f["domain"].removeprefix("faq-")


def _stable_base(f: Faq) -> str:
    return f"FAQ-{_id_prefix(f)}-{f['hash'][:8]}"


def stable_ids(faqs: list[Faq]) -> dict[int, str]:
    """Row id -> proposed stable id; repeats of one base get -2, -3, ... by row id."""
    seen: Counter = Counter()
    out = {}
    for f in faqs:  # faqs are sorted by id, so the first row keeps the bare id
        base = _stable_base(f)
        seen[base] += 1
        out[f["id"]] = base if seen[base] == 1 else f"{base}-{seen[base]}"
    return out


def _hash8_collisions(hashes: set[str]) -> int:
    by8 = Counter(h[:8] for h in hashes)
    return sum(by8[h[:8]] > 1 for h in hashes)


def section_stable_ids(faqs: list[Faq]) -> dict:
    matches = [_LEGACY_REF_RE.match(f["ref"]) for f in faqs]
    ids = stable_ids(faqs)
    id_counts = Counter(ids.values())
    by_prefix: dict[str, set[str]] = defaultdict(set)
    for f in faqs:
        by_prefix[_id_prefix(f)].add(f["hash"])
    return {
        "legacy_ref_pattern_mismatch": sum(m is None for m in matches),
        "legacy_prefix_vs_slug_mismatch": sum(
            m is not None and m.group(1) != f["domain"].removeprefix("faq-")
            for f, m in zip(faqs, matches)),
        "duplicate_faq_refs": sum(c > 1 for c in Counter(f["ref"] for f in faqs).values()),
        "stable_ids_total": len(ids),
        "ordinal_suffixed": len(faqs) - len({_stable_base(f) for f in faqs}),
        "stable_id_collisions": sum(c for c in id_counts.values() if c > 1),
        "hash8_prefix_collisions_global": _hash8_collisions({f["hash"] for f in faqs}),
        "hash8_prefix_collisions_same_prefix": sum(
            _hash8_collisions(hs) for hs in by_prefix.values()),
        "aliases_to_create": len(faqs),
    }


# ── Section 10: over-fetch (optional heavy dependencies) ───────────────────

# Copied from claw_easa.retrieval.exact.search_references (FTS branch, no slug).
FTS_SQL = (
    "SELECT e.id, e.entry_ref, e.entry_type, d.slug, -fts.rank AS fts_score "
    "FROM entries_fts fts "
    "JOIN regulation_entries e ON e.id = fts.rowid "
    "JOIN source_documents d ON d.id = e.document_id "
    "JOIN regulation_parts p ON p.id = e.part_id "
    "JOIN regulation_subparts sp ON sp.id = e.subpart_id "
    "WHERE entries_fts MATCH ? "
    "ORDER BY fts.rank "
    "LIMIT ?"
)
# Same joins as claw_easa.retrieval.vector.vector_search.
VEC_SQL = (
    "SELECT fm.faiss_position, ec.id AS chunk_id, ec.entry_id, "
    "       e.entry_ref, e.entry_type, d.slug "
    "FROM faiss_mapping fm "
    "JOIN entry_chunks ec ON ec.id = fm.chunk_id "
    "JOIN regulation_entries e ON e.id = ec.entry_id "
    "JOIN source_documents d ON d.id = e.document_id "
    "WHERE fm.faiss_position IN ({})"
)


def _load_overfetch_tools(data_dir: Path, queries: list[str]) -> dict:
    os.environ.setdefault("HF_HUB_OFFLINE", "1")
    import faiss
    import numpy as np
    from sentence_transformers import SentenceTransformer

    from claw_easa.config import get_settings
    from claw_easa.retrieval.fts_compat import to_fts5_query
    from claw_easa.retrieval.hybrid import _WORD_RE, _source_relevance_boost

    index_path = data_dir / "claw_easa.faiss"
    if not index_path.is_file():
        raise FileNotFoundError("claw_easa.faiss not found in data dir")
    model = SentenceTransformer(get_settings().embedding_model)
    # One query per encode() call, as embedder.encode_texts does in production.
    vectors = [model.encode([q], normalize_embeddings=True)[0] for q in queries]
    return {"np": np, "index": faiss.read_index(str(index_path)), "vectors": vectors,
            "to_fts5_query": to_fts5_query, "boost": _source_relevance_boost,
            "word_re": _WORD_RE}


def _fetch_fts(conn: sqlite3.Connection, tools: dict, query: str, k: int) -> list[dict]:
    # Only the FTS branch: the reference-query and LIKE fallbacks are not replicated.
    fts = tools["to_fts5_query"](query)
    if not fts.has_terms:
        return []
    try:
        return [dict(r) for r in conn.execute(FTS_SQL, (fts.match_expr, k))]
    except sqlite3.Error:
        return []


def _fetch_vec(conn: sqlite3.Connection, tools: dict, qi: int, k: int) -> list[dict]:
    np = tools["np"]  # mirrors FAISSStore.search + vector.vector_search
    q = np.asarray(tools["vectors"][qi], dtype=np.float32)
    norm = np.linalg.norm(q)
    if norm > 0:
        q = q / norm
    scores, indices = tools["index"].search(q.reshape(1, -1).astype(np.float32), k)
    hits = [(int(i), float(s)) for i, s in zip(indices[0], scores[0]) if i >= 0]
    if not hits:
        return []
    score_map = dict(hits)
    rows = [dict(r) for r in conn.execute(
        VEC_SQL.format(",".join("?" * len(hits))), [p for p, _ in hits])]
    for row in rows:
        row["vector_score"] = score_map.get(row["faiss_position"], 0.0)
    rows.sort(key=lambda r: r["vector_score"], reverse=True)
    return rows[:k]


def _merge(fts_results: list[dict], vec_results: list[dict], query: str, tools: dict,
           fts_weight: float = 0.4, vector_weight: float = 0.6) -> list[dict]:
    """Faithful copy of claw_easa.retrieval.hybrid.hybrid_search merging (no slug)."""
    merged: dict[int, dict] = {}
    if fts_results:
        max_fts = max((r.get("fts_score", 0) for r in fts_results), default=1) or 1
        for row in fts_results:
            row["hybrid_score"] = (row.get("fts_score", 0) / max_fts) * fts_weight
            merged[row["id"]] = row
    if vec_results:
        max_vec = max((r.get("vector_score", 0) for r in vec_results), default=1) or 1
        for row in vec_results:
            eid = row.get("entry_id") or row.get("id")
            norm_score = (row.get("vector_score", 0) / max_vec) * vector_weight
            if eid in merged:
                merged[eid]["hybrid_score"] += norm_score
            else:
                row["hybrid_score"] = norm_score
                row["id"] = eid
                merged[eid] = row
    query_words = set(tools["word_re"].findall(query.lower()))
    if query_words:
        for row in merged.values():
            boost = tools["boost"](query_words, row.get("slug", ""))
            if boost > 0:
                row["hybrid_score"] += boost
    return sorted(merged.values(), key=lambda r: r["hybrid_score"], reverse=True)


def _grouped(rows: list[dict], hash_by_id: dict[int, str]) -> list[dict]:
    """Keep the best-ranked row per N1 hash (FAQ) or per entry id (others)."""
    seen, out = set(), []
    for row in rows:
        eid = row.get("entry_id") or row.get("id")
        key = ("faq", hash_by_id[eid]) if eid in hash_by_id else ("entry", eid)
        if key not in seen:
            seen.add(key)
            out.append(row)
    return out


def _min_k(counts: dict[int, int], available: int | None = None, label: str = "") -> int | str:
    k = next((k for k in LADDER if counts[k] >= 10), None)
    if k is not None:
        return k
    if available is not None and available < LADDER[-1]:
        return f"unreachable (only {available} {label} rows)"
    return f">{LADDER[-1]}"


def _overfetch_query(conn, tools, qi: int, query: str, hash_by_id: dict[int, str]) -> dict:
    hybrid, fts_groups, vec_groups = {}, {}, {}
    for k in LADDER:
        fts_rows, vec_rows = _fetch_fts(conn, tools, query, k), _fetch_vec(conn, tools, qi, k)
        fts_groups[k] = len(_grouped(fts_rows, hash_by_id))
        vec_groups[k] = len(_grouped(vec_rows, hash_by_id))
        hybrid[k] = _merge(fts_rows, vec_rows, query, tools)[:k]
    # After the loop fts_rows / vec_rows hold the fetch at K = LADDER[-1] (500).
    groups = {k: len(_grouped(hybrid[k], hash_by_id)) for k in LADDER}
    sched_k = next((k for k in SCHEDULE if groups[k] >= 10), None)
    sched_top = _grouped(hybrid[sched_k or SCHEDULE[-1]], hash_by_id)[:10]
    today = hybrid[10][:10]
    min_k = _min_k(groups)
    top = _grouped(hybrid[min_k if isinstance(min_k, int) else LADDER[-1]], hash_by_id)[:10]
    faq_today = [r for r in today if r["id"] in hash_by_id]
    return {
        "query": query,
        "groups_by_K": {str(k): groups[k] for k in LADDER},
        "ten_groups_by_K": {str(k): "yes" if groups[k] >= 10 else "no" for k in LADDER},
        "current_top10": {"faq_rows": len(faq_today),
                          "faq_groups": len({hash_by_id[r["id"]] for r in faq_today}),
                          "entry_types": type_counts(today)},
        "min_K_for_10_groups": min_k,
        "top10_after_grouping_at_that_K": {
            "entry_types": type_counts(top),
            "changed_vs_today": len({r["entry_ref"] for r in top} - {r["entry_ref"] for r in today})},
        "fts_rows_available": len(fts_rows),
        "fts_groups_at_500": fts_groups[LADDER[-1]],
        "fts_only_min_K": _min_k(fts_groups, len(fts_rows), "FTS"),
        "vector_only_min_K": _min_k(vec_groups, len(vec_rows), "vector"),
        "schedule_K_used": sched_k or f"not reached ({SCHEDULE[-1]})",
        "schedule_top10_entry_types": type_counts(sched_top),
    }


def section_overfetch(conn: sqlite3.Connection, data_dir: Path, queries: list[str],
                      faqs: list[Faq]) -> dict:
    if not queries:
        return {"overfetch": "unavailable (no --query given)"}
    try:
        tools = _load_overfetch_tools(data_dir, queries)
    except Exception as exc:  # any missing piece makes the section unavailable
        return {"overfetch": f"unavailable ({type(exc).__name__}: {exc})"}
    hash_by_id = {f["id"]: f["hash"] for f in faqs}
    out: dict = {"faiss_ntotal": int(tools["index"].ntotal)}
    min_ks: list[int | str] = []
    scheduled: list[bool] = []
    for qi, query in enumerate(queries):
        res = _overfetch_query(conn, tools, qi, query, hash_by_id)
        min_ks.append(res["min_K_for_10_groups"])
        scheduled.append(isinstance(res["schedule_K_used"], int))
        out.update({f"q{qi + 1}.{key}": value for key, value in res.items()})
    if all(isinstance(k, int) for k in min_ks):
        worst = max(min_ks)
        out["recommended_factor"] = math.ceil(worst / 10)
        out["worst_case_K"] = worst
    else:
        out["recommended_factor"] = f">{LADDER[-1] // 10}"
        out["worst_case_K"] = f">{LADDER[-1]}"
    out["schedule_reached_for_all_queries"] = "yes" if all(scheduled) else "no"
    return out


# ── Report assembly and rendering ──────────────────────────────────────────

def build_report(conn: sqlite3.Connection, data_dir: Path, args: argparse.Namespace) -> dict:
    faqs = load_faqs(conn)
    variant_stats = {name: dup_stats(faqs, key) for name, key in VARIANTS.items()}
    return {
        "1. Corpus": section_corpus(conn, faqs),
        "2. Duplication under N1": section_duplication(faqs),
        "3. Reconciliation of variants": section_variants(variant_stats),
        "4. Domain inclusion": section_domains(faqs, args.min_domain_size, args.focus_domain),
        "5. Intra-domain duplicates": section_intra(faqs, variant_stats),
        "6. Same title, different body": section_same_title(faqs),
        "7. Ref linkage": section_refs(conn, faqs, args.ref_prefix),
        "8. Regression refs": section_regression(faqs, args.regression_ref),
        "9. Canonical-domain rule": section_canonical(faqs, args.focus_domain),
        "10. Over-fetch feasibility": (
            section_overfetch(conn, data_dir, args.query, faqs)
            if args.overfetch else {"skipped": "no --overfetch"}),
        "11. Stable-id preview": section_stable_ids(faqs),
    }


def fmt(value: object, nested: bool = False) -> str:
    if isinstance(value, dict):
        s = " ".join(f"{k}:{fmt(v, True)}" for k, v in value.items())
        return f"{{{s}}}" if nested else (s or "-")
    if isinstance(value, list):
        return f"[{', '.join(fmt(v, True) for v in value)}]"
    return str(value)


def render_text(report: dict) -> str:
    lines: list[str] = []
    for title, section in report.items():
        lines.append(f"== {title} ==")
        if set(section) == {"skipped"}:
            lines.append(f"skipped ({section['skipped']})")
            section = {}
        for key, value in section.items():
            if isinstance(value, list):
                lines.append(f"{key}:" if value else f"{key}: (none)")
                lines.extend(f"  {fmt(v, True)}" for v in value)
            else:
                lines.append(f"{key}: {fmt(value)}")
        lines.append("")
    return "\n".join(lines)


def parse_args(argv: list[str] | None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--data-dir", required=True, help="claw-easa data directory")
    p.add_argument("--focus-domain", action="append", default=[], help="FAQ domain slug (repeatable)")
    p.add_argument("--ref-prefix", help="regulation reference prefix for section 7")
    p.add_argument("--regression-ref", action="append", default=[], help="FAQ entry_ref (repeatable)")
    p.add_argument("--min-domain-size", type=int, default=5, help="min FAQs per domain in section 4")
    p.add_argument("--overfetch", action="store_true", help="run section 10 (needs model + FAISS)")
    p.add_argument("--query", action="append", default=[], help="query for --overfetch (repeatable)")
    p.add_argument("--json", action="store_true", help="print one JSON object instead of text")
    p.add_argument("--immutable", action="store_true",
                   help="open with immutable=1 so no -shm/-wal file is created; "
                        "use --immutable only on a pristine snapshot with no writer")
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    data_dir = Path(args.data_dir)
    db_path = data_dir / "claw_easa.db"
    if not data_dir.is_dir() or not db_path.is_file():
        print(f"error: database not found: {db_path}", file=sys.stderr)
        return 2
    conn = connect_ro(db_path, args.immutable)
    try:
        report = build_report(conn, data_dir, args)
    finally:
        conn.close()
    if args.json:
        print(json.dumps(report, sort_keys=True, indent=2))
    else:
        print(render_text(report), end="")
    return 0


if __name__ == "__main__":
    sys.exit(main())
