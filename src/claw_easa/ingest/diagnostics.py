"""Post-ingestion diagnostics for verifying parser coverage.

Provides two levels of analysis:
- ``document_diagnostics``: parse-time stats (entry types, body length, gaps)
- ``coverage_report``:  cross-references the XML TOC with parsed entries to
  detect missing content.
"""
from __future__ import annotations

import re
import logging
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

log = logging.getLogger(__name__)


@dataclass
class CoverageReport:
    slug: str
    parser_mode: str
    paragraph_count: int
    parts: int
    subparts: int
    sections: int
    entries: int
    by_type: dict[str, int] = field(default_factory=dict)
    empty_body: int = 0
    short_body: int = 0
    avg_body_chars: float = 0.0
    toc_article_count: int = 0
    parsed_article_count: int = 0
    missing_articles: list[str] = field(default_factory=list)
    uncaptured_headings: list[str] = field(default_factory=list)
    heading_coverage_pct: float = 0.0


def coverage_report(xml_path: Path, title: str) -> CoverageReport:
    """Cross-reference parsed entries against the raw XML to detect gaps."""
    from claw_easa.ingest.parser import EASAOfficeXMLParser

    parser = EASAOfficeXMLParser()
    root = parser._load_root(xml_path)
    paragraphs = parser._extract_paragraphs(root)
    doc = parser.parse_file(xml_path, title)

    # --- Collect parsed entries ---
    type_counts: Counter[str] = Counter()
    total_body_chars = 0
    empty_body = 0
    short_body = 0
    parsed_article_nums: set[str] = set()
    total_entries = 0

    for part in doc.parts:
        for sp in part.subparts:
            for sec in sp.sections:
                for entry in sec.entries:
                    total_entries += 1
                    type_counts[entry.entry_type] += 1
                    body_len = sum(len(l) for l in entry.body_lines)
                    total_body_chars += body_len
                    if body_len == 0:
                        empty_body += 1
                    elif body_len < 50:
                        short_body += 1
                    m = re.match(r"Article\s+(\d+[A-Z]*)", entry.entry_ref)
                    if m:
                        parsed_article_nums.add(m.group(1))

    avg_body = total_body_chars / total_entries if total_entries else 0.0

    # --- TOC cross-reference ---
    toc_articles: set[str] = set()
    for p in paragraphs:
        if p.style.startswith("TOC"):
            text = p.text.strip()
            m = re.match(r"^Article\s+(\d+[A-Z]*)\s+\S", text)
            if m:
                toc_articles.add(m.group(1))

    missing = sorted(
        toc_articles - parsed_article_nums,
        key=lambda x: int(re.match(r"(\d+)", x).group(1)),
    ) if toc_articles else []

    # --- Uncaptured heading-style paragraphs ---
    entry_heading_styles = {
        "Heading3IR", "Heading3AMC", "Heading3GM",
        "Heading4IR", "Heading4AMC", "Heading4GM",
        "Heading5AMC", "Heading5GM", "Heading5IR",
    }
    captured_list_positions: set[int] = set()
    for part in doc.parts:
        for sp in part.subparts:
            for sec in sp.sections:
                for entry in sec.entries:
                    if entry.source_locator:
                        m = re.match(r"paragraphs:(\d+)-", entry.source_locator)
                        if m:
                            captured_list_positions.add(int(m.group(1)) - 1)

    uncaptured: list[str] = []
    total_entry_headings = 0
    for list_pos, p in enumerate(paragraphs):
        if p.style in entry_heading_styles:
            total_entry_headings += 1
            if list_pos not in captured_list_positions:
                uncaptured.append(f"[{p.style}] {p.text[:80]}")

    coverage_pct = (
        (total_entry_headings - len(uncaptured)) / total_entry_headings * 100
        if total_entry_headings
        else 100.0
    )

    return CoverageReport(
        slug=title,
        parser_mode=doc.parser_mode,
        paragraph_count=doc.paragraph_count,
        parts=len(doc.parts),
        subparts=sum(len(p.subparts) for p in doc.parts),
        sections=sum(len(sp.sections) for p in doc.parts for sp in p.subparts),
        entries=total_entries,
        by_type=dict(type_counts.most_common()),
        empty_body=empty_body,
        short_body=short_body,
        avg_body_chars=avg_body,
        toc_article_count=len(toc_articles),
        parsed_article_count=len(parsed_article_nums),
        missing_articles=missing,
        uncaptured_headings=uncaptured[:20],
        heading_coverage_pct=coverage_pct,
    )


def format_report(r: CoverageReport) -> str:
    """Format a CoverageReport as a human-readable string."""
    lines = [
        f"=== Coverage report: {r.slug} ===",
        f"Parser mode:       {r.parser_mode}",
        f"Paragraphs:        {r.paragraph_count}",
        f"Structure:         {r.parts} parts / {r.subparts} subparts / {r.sections} sections",
        f"Entries:           {r.entries}",
        f"  By type:         {r.by_type}",
        f"  Empty body:      {r.empty_body}",
        f"  Short body (<50): {r.short_body}",
        f"  Avg body chars:  {r.avg_body_chars:.0f}",
    ]

    if r.toc_article_count:
        lines.append(f"TOC articles:      {r.toc_article_count}")
        lines.append(f"Parsed articles:   {r.parsed_article_count}")
        if r.missing_articles:
            lines.append(f"!! MISSING articles: {', '.join(r.missing_articles)}")
        else:
            lines.append("  All TOC articles present.")

    lines.append(f"Heading coverage:  {r.heading_coverage_pct:.1f}%")
    if r.uncaptured_headings:
        lines.append(f"!! Uncaptured headings ({len(r.uncaptured_headings)}):")
        for h in r.uncaptured_headings:
            lines.append(f"   {h}")

    ok = (
        r.empty_body == 0
        and not r.missing_articles
        and r.heading_coverage_pct >= 99.0
    )
    lines.append(f"\nVerdict: {'PASS' if ok else 'REVIEW NEEDED'}")
    return "\n".join(lines)
