"""Tests for scripts/faq_dup_investigation.py on a tiny synthetic corpus."""
from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import sqlite3
from pathlib import Path

import pytest

from claw_easa.db.migrations import SCHEMA_FILE

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "faq_dup_investigation.py"
_spec = importlib.util.spec_from_file_location("faq_dup_investigation", SCRIPT)
mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(mod)

PARENT = [
    ("Question one?", "Answer text one."),
    ("Question two?", "Answer text two."),
    ("Question three?", "Answer text three."),
    ("Question four?", "Answer text four."),
    ("  question   FOUR ", "answer text FOUR"),  # N1 duplicate of the previous one
    ("Question five?", "Answer text five."),
]
CHILD = PARENT[:3]
OTHER = [
    ("Question seven?", "Answer seven alpha."),
    ("Question seven?", "Answer seven beta, longer."),
]
# entry id (insertion order) -> target_ref
REFS = {1: "XYZ.AB.100", 7: "XYZ.AB.100", 4: "XYZ.AB", 5: "XYZ.ABC.1",
        6: "xyz.ab.1", 10: "OTHER.1"}


def _add_document(conn: sqlite3.Connection, slug: str, short: str,
                  faqs: list[tuple[str, str]]) -> int:
    doc = conn.execute("INSERT INTO source_documents (slug, source_family, title) "
                       "VALUES (?, 'faq', ?)", (slug, slug)).lastrowid
    part = conn.execute("INSERT INTO regulation_parts (document_id, part_code, title) "
                        "VALUES (?, 'FAQ', 't')", (doc,)).lastrowid
    sub = conn.execute("INSERT INTO regulation_subparts (part_id, subpart_code, title) "
                       "VALUES (?, 'FAQ', 't')", (part,)).lastrowid
    sec = conn.execute("INSERT INTO regulation_sections (subpart_id, title) "
                       "VALUES (?, 't')", (sub,)).lastrowid
    for n, (title, body) in enumerate(faqs, 1):
        conn.execute(
            "INSERT INTO regulation_entries (document_id, part_id, subpart_id, section_id, "
            "entry_ref, entry_type, title, body_markdown, body_text, sort_order) "
            "VALUES (?, ?, ?, ?, ?, 'FAQ', ?, ?, ?, ?)",
            (doc, part, sub, sec, f"FAQ-{short}-{n:03d}", title, body, body, n))
    return doc


@pytest.fixture()
def data_dir(tmp_path: Path) -> Path:
    conn = sqlite3.connect(tmp_path / "claw_easa.db")
    conn.executescript(SCHEMA_FILE.read_text())
    _add_document(conn, "faq-parent", "parent", PARENT)
    _add_document(conn, "faq-child", "child", CHILD)
    _add_document(conn, "faq-other", "other", OTHER)
    conn.execute(
        "INSERT INTO regulation_entries (document_id, part_id, subpart_id, section_id, "
        "entry_ref, entry_type, title, body_text) VALUES (1, 1, 1, 1, 'XYZ.AB.100', 'IR', 'Rule', 'Rule body')")
    conn.executemany("INSERT INTO faq_regulation_refs (faq_entry_id, target_ref) VALUES (?, ?)",
                     sorted(REFS.items()))
    conn.execute("INSERT INTO schema_migrations (version) VALUES ('001_initial')")
    conn.execute("UPDATE source_documents SET revision = 'Rev 1' WHERE slug = 'faq-parent'")
    # entry 1 chunk carries the entry_ref context prefix, entry 2 does not; entry 12 is IR.
    conn.executemany("INSERT INTO entry_chunks (entry_id, chunk_text) VALUES (?, ?)",
                     [(1, "FAQ-parent-001 Question one?"), (2, "Question two?"),
                      (12, "XYZ.AB.100 Rule")])
    conn.execute("INSERT INTO faiss_mapping (faiss_position, chunk_id) VALUES (0, 1)")
    conn.commit()
    conn.close()
    return tmp_path


def _argv(data_dir: Path, *extra: str) -> list[str]:
    return ["--data-dir", str(data_dir), "--min-domain-size", "2",
            "--focus-domain", "faq-parent", "--focus-domain", "faq-child",
            "--ref-prefix", "XYZ.AB",
            "--regression-ref", "FAQ-parent-001", "--regression-ref", "FAQ-child-001",
            *extra]


def _run(capsys, argv: list[str]) -> str:
    assert mod.main(argv) == 0
    return capsys.readouterr().out


def test_missing_data_dir_exits_2(tmp_path, capsys):
    assert mod.main(["--data-dir", str(tmp_path / "nope")]) == 2
    assert "not found" in capsys.readouterr().err
    assert mod.main(["--data-dir", str(tmp_path)]) == 2  # dir exists, db does not


def test_report_counts(data_dir, capsys):
    report = json.loads(_run(capsys, _argv(data_dir, "--json")))

    corpus = report["1. Corpus"]
    assert corpus["entries_by_type"] == {"FAQ": 11, "IR": 1}
    assert corpus["faq_domains"] == 3
    assert corpus["schema_migrations"] == ["001_initial"]
    assert corpus["faq_empty_body"] == 0
    assert corpus["source_families"] == {"faq": 3}
    assert corpus["faq_documents_with_revision"] == 1
    assert corpus["faq_documents_with_published_at"] == 0
    assert corpus["corpus_builds_rows"] == 0
    assert corpus["entry_chunks_faq"] == 2
    assert corpus["entry_chunks_total"] == 3
    assert corpus["faiss_mapping_rows"] == 1
    assert corpus["faq_chunks_starting_with_entry_ref"] == 1

    dup = report["2. Duplication under N1"]
    assert dup["faq_entries"] == 11
    assert dup["distinct_hashes"] == 7
    assert dup["redundant_copies"] == 4
    assert dup["multiplicity_histogram"] == {"2": 4}
    assert dup["multi_domain_hashes"] == 3
    assert dup["single_domain_duplicated_hashes"] == 1

    variants = report["3. Reconciliation of variants"]
    assert variants["a_raw"]["redundant"] == 3
    assert variants["c_n1"]["intra_domain_redundant"] == 1
    assert variants["reproduced_by"] == "none"

    dom = report["4. Domain inclusion"]
    assert dom["full_inclusion_pairs"] == 1
    assert dom["equal_pairs"] == 0
    assert dom["direct_parent"] == ["faq-child -> faq-parent"]
    assert dom["true_partial_pairs"] == 0  # child is a strict subset, other is disjoint
    assert dom["true_partial_overlaps"] == []
    assert "faq-child subset_of faq-parent: yes" in dom["focus"]
    assert "faq-parent subset_of faq-child: no" in dom["focus"]

    intra = report["5. Intra-domain duplicates"]
    assert intra["intra_domain_redundant"] == 1
    assert intra["examples"] == ["faq-parent: FAQ-parent-004, FAQ-parent-005"]
    assert intra["consecutive_position_pairs"] == 1
    assert intra["raw_identical"] == 0
    assert intra["body_only_extra"] == 0

    same = report["6. Same title, different body"]
    lengths = sorted(len(b) for _, b in OTHER)
    assert same["groups"] == 1
    assert same["group_list"] == [f"refs=[FAQ-other-001, FAQ-other-002] body_lengths={lengths}"]

    refs = report["7. Ref linkage"]
    assert refs["prefix_match"] == {"faq_entries": 4, "distinct_hashes": 2, "redundant": 2}
    assert refs["strict_match"] == {"faq_entries": 3, "distinct_hashes": 2, "redundant": 1}

    reg = report["8. Regression refs"]
    assert reg["same_hash"] == "yes"
    assert reg["FAQ-parent-001"].startswith("found=yes domain=faq-parent hash8=")

    canon = report["9. Canonical-domain rule"]
    assert canon["multi_domain_groups"] == 3
    assert canon["canonical_distribution_top15"] == {"faq-child": 3}
    assert canon["rule_total_order"] == "yes"
    assert canon["ties_resolved_alphabetically"] == 0
    assert canon["focus_order"] == "faq-child(3) < faq-parent(6)"
    assert canon["groups_with_all_focus"] == 3
    assert canon["focus_canonical_distribution"] == {"faq-child": 3}

    assert report["10. Over-fetch feasibility"] == {"skipped": "no --overfetch"}


def test_text_report_and_skips(data_dir, capsys):
    out = _run(capsys, _argv(data_dir))
    assert "== 2. Duplication under N1 ==" in out
    assert "multiplicity_histogram: 2:4" in out
    assert "full_inclusion_pairs: 1" in out
    assert "true_partial_overlaps: (none)" in out
    assert "== 11. Stable-id preview ==" in out
    out = _run(capsys, ["--data-dir", str(data_dir), "--regression-ref", "FAQ-missing-001"])
    assert "skipped (no --ref-prefix)" in out
    assert "focus: skipped (no --focus-domain)" in out
    assert "FAQ-missing-001: NOT FOUND" in out
    assert "same_hash: no" in out


def test_read_only(data_dir, capsys):
    db = data_dir / "claw_easa.db"
    before = (hashlib.sha256(db.read_bytes()).hexdigest(), sorted(os.listdir(data_dir)))
    _run(capsys, _argv(data_dir))
    _run(capsys, _argv(data_dir, "--json"))
    assert (hashlib.sha256(db.read_bytes()).hexdigest(), sorted(os.listdir(data_dir))) == before


def test_immutable_leaves_wal_snapshot_untouched(data_dir, capsys):
    db = data_dir / "claw_easa.db"
    conn = sqlite3.connect(db)
    assert conn.execute("PRAGMA journal_mode=WAL").fetchone()[0] == "wal"
    conn.close()
    for suffix in ("-wal", "-shm"):
        Path(f"{db}{suffix}").unlink(missing_ok=True)
    before = sorted(os.listdir(data_dir))
    assert before == ["claw_easa.db"]
    _run(capsys, ["--data-dir", str(data_dir), "--immutable"])
    assert sorted(os.listdir(data_dir)) == before


def test_stable_id_preview(data_dir, capsys):
    report = json.loads(_run(capsys, _argv(data_dir, "--json")))
    assert report["11. Stable-id preview"] == {
        "legacy_ref_pattern_mismatch": 0,
        "legacy_prefix_vs_slug_mismatch": 0,
        "duplicate_faq_refs": 0,
        "stable_ids_total": 11,
        "ordinal_suffixed": 1,
        "stable_id_collisions": 0,
        "hash8_prefix_collisions_global": 0,
        "hash8_prefix_collisions_same_prefix": 0,
        "aliases_to_create": 11,
    }
    conn = mod.connect_ro(data_dir / "claw_easa.db")
    try:
        ids = mod.stable_ids(mod.load_faqs(conn))
    finally:
        conn.close()
    four = mod.n1_hash(*PARENT[3])[:8]
    one = mod.n1_hash(*PARENT[0])[:8]
    # entries 4 and 5 are the intra-domain N1 duplicate in faq-parent
    assert ids[4] == f"FAQ-parent-{four}"
    assert ids[5] == f"FAQ-parent-{four}-2"
    # same text in another domain keeps its own prefix and needs no suffix
    assert ids[1] == f"FAQ-parent-{one}"
    assert ids[7] == f"FAQ-child-{one}"
    assert len(set(ids.values())) == 11


def test_deterministic(data_dir, capsys):
    assert _run(capsys, _argv(data_dir)) == _run(capsys, _argv(data_dir))
    assert _run(capsys, _argv(data_dir, "--json")) == _run(capsys, _argv(data_dir, "--json"))


def test_json_agrees_with_text(data_dir, capsys):
    text = _run(capsys, _argv(data_dir))
    report = json.loads(_run(capsys, _argv(data_dir, "--json")))
    for title in ("2. Duplication under N1", "5. Intra-domain duplicates", "9. Canonical-domain rule"):
        for key, value in report[title].items():
            if not isinstance(value, list):
                assert f"{key}: {mod.fmt(value)}" in text


@pytest.mark.parametrize(("raw", "expected"), [
    ("ＡＢＣ　ｑｕｅｓｔｉｏｎ", "abc question"),  # NFKC full-width letters and space
    ("Straße", "strasse"),  # casefold
    ("  a \t\n  b  ", "a b"),  # whitespace collapsed and stripped
    ("¿«Hello, world!»?", "hello, world"),  # surrounding punctuation stripped
    ("e.g. A.B.", "e.g. a.b"),  # internal punctuation kept
    (" - item - ", "item"),
    ("?!", ""),
    (None, ""),
])
def test_normalise(raw, expected):
    assert mod.normalise(raw) == expected
