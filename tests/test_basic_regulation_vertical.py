from pathlib import Path

from claw_easa.ingest.parser import EASAOfficeXMLParser

FIXTURE = Path(__file__).parent / "fixtures" / "basic_regulation_2018_1139.xml"


def entries(doc):
    return [e for p in doc.parts for s in p.subparts for sec in s.sections for e in sec.entries]


def test_basic_regulation_article_hierarchy_and_types():
    doc = EASAOfficeXMLParser().parse_file(FIXTURE, "Basic Regulation (EU) 2018/1139")
    assert doc.parser_mode == "article-structured"
    refs = {e.entry_ref for e in entries(doc)}
    assert {"Article 1", "Article 3", "Article 4"} <= refs
    assert "AMC1 Article 3" in refs
    assert "GM1 Article 3" in refs
    typed = {e.entry_ref: e.entry_type for e in entries(doc)}
    assert typed["Article 1"] == "IR"
    assert typed["Article 3"] == "IR"
    assert typed["AMC1 Article 3"] == "AMC"
    assert typed["GM1 Article 3"] == "GM"


def test_basic_regulation_keeps_chapters_and_guidance_bodies_separate():
    doc = EASAOfficeXMLParser().parse_file(FIXTURE)
    assert [p.title for p in doc.parts] == ["CHAPTER I – General principles", "CHAPTER II – Requirements"]
    by_ref = {e.entry_ref: e for e in entries(doc)}
    assert "Acceptable means" in " ".join(by_ref["AMC1 Article 3"].body_lines)
    assert "Acceptable means" not in " ".join(by_ref["Article 3"].body_lines)


def test_basic_regulation_fixture_is_stable_and_source_is_identifiable():
    doc = EASAOfficeXMLParser().parse_file(FIXTURE, "Regulation (EU) 2018/1139")
    assert doc.title == "Regulation (EU) 2018/1139"
    assert doc.paragraph_count > 0
    assert doc.style_counts["Heading1"] == 2
