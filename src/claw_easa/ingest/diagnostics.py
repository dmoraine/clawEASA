from __future__ import annotations

import logging
from collections import Counter
from pathlib import Path

log = logging.getLogger(__name__)


def document_diagnostics(path: Path, title: str) -> dict:
    from claw_easa.ingest.parser import EASAOfficeXMLParser

    parser = EASAOfficeXMLParser()
    parsed = parser.parse_file(path, title)

    entry_count = 0
    nonempty_body_count = 0
    empty_body_count = 0
    refs: list[str] = []
    titles_same_as_ref = 0
    long_title_count = 0
    empty_section_count = 0
    ref_counter: Counter[str] = Counter()

    for part in parsed.parts:
        for subpart in part.subparts:
            for section in subpart.sections:
                section_entries = 0
                for entry in section.entries:
                    entry_count += 1
                    section_entries += 1
                    ref_counter[entry.entry_ref] += 1
                    refs.append(entry.entry_ref)

                    body = "\n".join(entry.body_lines).strip()
                    if body:
                        nonempty_body_count += 1
                    else:
                        empty_body_count += 1

                    if entry.title.strip() == entry.entry_ref.strip():
                        titles_same_as_ref += 1
                    if len(entry.title) > 120:
                        long_title_count += 1

                if section_entries == 0:
                    empty_section_count += 1

    duplicate_ref_count = sum(1 for c in ref_counter.values() if c > 1)

    return {
        "parser_mode": getattr(parser, '_last_mode', 'unknown'),
        "paragraph_count": getattr(parser, '_paragraph_count', 0),
        "part_count": len(parsed.parts),
        "subpart_count": sum(len(p.subparts) for p in parsed.parts),
        "section_count": sum(
            len(sp.sections) for p in parsed.parts for sp in p.subparts
        ),
        "entry_count": entry_count,
        "nonempty_body_count": nonempty_body_count,
        "empty_body_count": empty_body_count,
        "article_like_heading_count": 0,
        "top_styles": [],
        "sample_refs": refs[:10],
        "duplicate_ref_count": duplicate_ref_count,
        "titles_same_as_ref_count": titles_same_as_ref,
        "long_title_count": long_title_count,
        "empty_section_count": empty_section_count,
        "info_entry_count": 0,
    }
