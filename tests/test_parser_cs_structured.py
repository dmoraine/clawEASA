from __future__ import annotations

from pathlib import Path
from xml.sax.saxutils import escape

from claw_easa.ingest.parser import EASAOfficeXMLParser


def _write_word_xml(path: Path, paragraphs: list[tuple[str, str]]) -> None:
    body = "\n".join(
        f"""
        <w:p>
          <w:pPr><w:pStyle w:val=\"{style}\"/></w:pPr>
          <w:r><w:t>{escape(text)}</w:t></w:r>
        </w:p>
        """
        for style, text in paragraphs
    )
    path.write_text(
        f"""<?xml version=\"1.0\" encoding=\"UTF-8\"?>
        <w:document xmlns:w=\"http://schemas.openxmlformats.org/wordprocessingml/2006/main\">
          <w:body>{body}</w:body>
        </w:document>
        """,
        encoding="utf-8",
    )


def _entries(doc):
    return [
        entry
        for part in doc.parts
        for subpart in part.subparts
        for section in subpart.sections
        for entry in section.entries
    ]


def test_cs_mmel_style_ear_without_annex_part_headings(tmp_path):
    xml_path = tmp_path / "cs_mmel.xml"
    _write_word_xml(
        xml_path,
        [
            ("Title", "Easy Access Rules for Master Minimum Equipment List (CS-MMEL)"),
            ("Heading1", "Table of contents"),
            ("TOC2", "CS MMEL.050 Scope10"),
            ("Heading1", "SUBPART A — GENERAL"),
            ("Heading2CS", "CS MMEL.050 Scope"),
            ("Normal", "These Certification Specifications establish the scope."),
            ("Heading2CS", "CS MMEL.100 Applicability"),
            ("Normal", "These Certification Specifications are applicable."),
            ("Heading3GM", "GM1 MMEL.100 Applicability"),
            ("Normal", "Guidance material for applicability."),
            ("Heading1", "SUBPART B — MASTER MINIMUM EQUIPMENT LIST"),
            ("Heading2GM", "CS MMEL.110 MMEL purpose"),
            ("Normal", "The MMEL purpose is described here."),
            ("Heading3GM", "GM1 MMEL.110 MMEL purpose"),
            ("Normal", "Additional guidance."),
            ("Heading5GM", "ATA 22 AUTOFLIGHT"),
            ("Heading6OrgManual", "Aircraft applicability: Aeroplanes & Helicopters"),
            ("TableNormal0", "Autopilot item guidance."),
        ],
    )

    doc = EASAOfficeXMLParser().parse_file(xml_path, "CS-MMEL fixture")
    entries = _entries(doc)

    assert doc.parser_mode == "cs-structured"
    assert len(doc.parts) == 1
    assert doc.parts[0].code == "CS-MMEL"
    assert [sp.code for sp in doc.parts[0].subparts] == ["SUBPART-A", "SUBPART-B"]
    assert [entry.entry_ref for entry in entries] == [
        "CS MMEL.050",
        "CS MMEL.100",
        "GM1 MMEL.100 Applicability",
        "CS MMEL.110",
        "GM1 MMEL.110 MMEL purpose",
        "ATA 22 AUTOFLIGHT",
    ]
    assert [entry.entry_type for entry in entries] == ["CS", "CS", "GM", "CS", "GM", "GM"]
    assert entries[-1].body_lines == [
        "Aircraft applicability: Aeroplanes & Helicopters",
        "Autopilot item guidance.",
    ]


def test_cs_gen_mmel_single_heading_document(tmp_path):
    xml_path = tmp_path / "cs_gen_mmel.xml"
    _write_word_xml(
        xml_path,
        [
            ("Heading1", "CS AND GM FOR GENERIC MASTER MINIMUM EQUIPMENT LIST"),
            ("Heading2CS", "CS GEN.MMEL.100 Applicability"),
            ("Normal", "Applicability text."),
            ("Heading2CS", "CS GEN.MMEL.105 Definitions"),
            ("Normal", "Definition text."),
            ("Heading3GM", "GM1 GEN.MMEL.105 Definitions"),
            ("Normal", "Definitions guidance."),
        ],
    )

    doc = EASAOfficeXMLParser().parse_file(xml_path, "CS-GEN-MMEL fixture")
    entries = _entries(doc)

    assert doc.parser_mode == "cs-structured"
    assert doc.parts[0].code == "CS-GEN"
    assert len(doc.parts[0].subparts) == 1
    assert doc.parts[0].subparts[0].code == "CS-AND-GM-FOR-GENERIC-MASTER-MINIMUM-EQU"
    assert [entry.entry_ref for entry in entries] == [
        "CS GEN.MMEL.100",
        "CS GEN.MMEL.105",
        "GM1 GEN.MMEL.105 Definitions",
    ]
