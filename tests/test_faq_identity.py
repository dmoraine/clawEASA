"""S1 — pure FAQ identity rules: N1 normalisation, content hash, stable ids."""

import pytest

from claw_easa.ingest.faq_identity import (
    StableIdCollision,
    assign_stable_ids,
    content_hash,
    hash8,
    normalise,
    parse_legacy_ref,
)

# Golden values pin the frozen N1 rule: changing them means a new migration.
GOLDEN_PLAIN = "d0de50f4812060d473d71dfe6702c052a624587aee4949b1b8f6807ad45e8095"
GOLDEN_UNICODE = "5e05703449f7fe51e1ec7bdae194b72e9a212e36182ca1e44bcae164946f70d4"
GOLDEN_EMPTY = "01ba4719c80b6fe911b091a7c05124b64eeece964e09c058ef8f9805daca546b"


class TestNormalise:
    def test_collapses_whitespace_and_strips_edge_punctuation(self):
        assert normalise("  Hello,  World!  ") == "hello, world"

    def test_keeps_internal_punctuation(self):
        assert normalise("(a) b.c - d?") == "a) b.c - d"

    def test_full_width_and_curly_quotes(self):
        # NFKC folds full-width letters; curly quotes are kept inside, stripped at edges.
        assert normalise("Ｗhat is the “FDP”?") == "what is the “fdp"

    def test_has_no_newline(self):
        assert "\n" not in normalise("line one\n\nline two\r\n")


class TestContentHash:
    def test_golden_plain(self):
        assert content_hash(
            "What is a duty period?",
            "A duty period starts when the crew member reports.",
        ) == GOLDEN_PLAIN

    def test_golden_unicode(self):
        assert content_hash(
            "Ｗhat is the “FDP”?", "Answer text here."
        ) == GOLDEN_UNICODE

    def test_golden_empty(self):
        assert content_hash("", "") == GOLDEN_EMPTY

    def test_whitespace_and_unicode_variants_share_hash(self):
        base = content_hash("What is a duty period?", "A duty period starts when the crew member reports.")
        variants = [
            ("  what is a duty period  ", "A duty period  starts when the crew member reports"),
            ("WHAT IS A DUTY PERIOD?", "A duty period starts\nwhen the crew member reports."),
            ("Ｗhat is a duty period?", "\tA duty period starts when the crew member reports.\n"),
        ]
        for title, body in variants:
            assert content_hash(title, body) == base

    def test_same_title_different_body_differ(self):
        title = "Can a UK certificate be used?"
        bodies = [
            "No.",
            "No, it cannot be used after the transition period.",
            "Yes, until the end of the transition period.",
            "See the answer above, with a footnote*",
            "See the answer above with a footnote*",
        ]
        hashes = {content_hash(title, b) for b in bodies}
        assert len(hashes) == 5

    def test_body_and_title_are_not_interchangeable(self):
        assert content_hash("ab", "c") != content_hash("a", "bc")

    def test_hash8_is_prefix(self):
        assert hash8(GOLDEN_PLAIN) == "d0de50f4"


class TestAssignStableIds:
    def test_base_id(self):
        h = content_hash("q one", "a one")
        assert assign_stable_ids("FAQ-dom", [h]) == [f"FAQ-dom-{h[:8]}"]

    def test_same_text_in_two_domains_same_hash(self):
        h = content_hash("q one", "a one")
        assert assign_stable_ids("FAQ-a", [h])[0].endswith(h[:8])
        assert assign_stable_ids("FAQ-b", [h])[0].endswith(h[:8])

    def test_ordinal_suffix_for_repeated_text(self):
        h1, h2 = content_hash("q one", "a one"), content_hash("q two", "a two")
        ids = assign_stable_ids("FAQ-dom", [h1, h2, h1, h1])
        assert ids == [
            f"FAQ-dom-{h1[:8]}",
            f"FAQ-dom-{h2[:8]}",
            f"FAQ-dom-{h1[:8]}-2",
            f"FAQ-dom-{h1[:8]}-3",
        ]

    def test_hash8_collision_between_different_hashes_raises(self):
        a = "abcdef12" + "0" * 56
        b = "abcdef12" + "1" * 56
        with pytest.raises(StableIdCollision):
            assign_stable_ids("FAQ-dom", [a, b])


class TestParseLegacyRef:
    def test_legacy(self):
        assert parse_legacy_ref("FAQ-air-operations-057") == ("FAQ-air-operations", 57)

    def test_long_number(self):
        assert parse_legacy_ref("FAQ-x-1234") == ("FAQ-x", 1234)

    def test_not_legacy(self):
        assert parse_legacy_ref("FAQ-oroftl-590f5a9e") is None
        assert parse_legacy_ref("FAQ-x-12") is None
        assert parse_legacy_ref("ORO.FTL.110") is None
