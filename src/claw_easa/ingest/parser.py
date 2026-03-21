"""EASA Easy Access Rules XML parser.

Parses Office Open XML (WordprocessingML) documents downloaded from the EASA
website and extracts the hierarchical regulation structure:

    Document -> Parts -> Subparts -> Sections -> Entries

Each EASA Easy Access Rules document is an XML Package containing Word-style
paragraphs with heading styles that encode the regulation hierarchy.
"""

from __future__ import annotations

import logging
import re
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

from lxml import etree

logger = logging.getLogger(__name__)

# ── Data classes ────────────────────────────────────────────────────────────


@dataclass
class OfficeXMLParagraph:
    """A paragraph extracted from the Office Open XML document."""
    index: int
    style: str
    text: str
    level: int = 0
    is_list: bool = False
    list_level: int | None = None


@dataclass
class ParsedEntry:
    """A single regulation entry (IR, AMC, GM, or INFO)."""
    entry_ref: str
    entry_type: str  # 'IR', 'AMC', 'GM', 'INFO'
    title: str
    body_lines: list[str] = field(default_factory=list)
    sort_order: int = 0
    source_locator: str | None = None


@dataclass
class ParsedSection:
    """A section within a subpart, containing entries."""
    title: str
    sort_order: int = 0
    entries: list[ParsedEntry] = field(default_factory=list)


@dataclass
class ParsedSubpart:
    """A subpart within a part, containing sections."""
    code: str
    title: str
    sort_order: int = 0
    sections: list[ParsedSection] = field(default_factory=list)


@dataclass
class ParsedPart:
    """A regulation part (annex), containing subparts."""
    code: str
    title: str
    annex: str
    sort_order: int = 0
    subparts: list[ParsedSubpart] = field(default_factory=list)


@dataclass
class ParsedDocument:
    """Result of parsing an EASA Easy Access Rules document."""
    title: str = ''
    paragraph_count: int = 0
    parser_mode: str = ''
    parts: list[ParsedPart] = field(default_factory=list)
    style_counts: dict[str, int] = field(default_factory=dict)


# ── Parser ──────────────────────────────────────────────────────────────────


class EASAOfficeXMLParser:
    """
    Production parser for EASA Easy Access Rules Office XML documents.

    Stateless: each call to parse_file() is self-contained.

    Supports three parsing modes:
    - ``part``: documents organised by ANNEX / Part-XXX (e.g. air-ops annexes)
    - ``article-structured``: documents organised by Articles (e.g. basic-regulation)
    - ``hybrid``: documents with both cover-regulation articles *and* Part annexes
    """

    NAMESPACES = {
        'w': 'http://schemas.openxmlformats.org/wordprocessingml/2006/main',
        'pkg': 'http://schemas.microsoft.com/office/2006/xmlPackage',
    }

    HIERARCHY = {
        'Heading1': 1,
        'Heading2IR': 2,
        'Heading2': 2,
        'Heading2CR': 2,
        'Heading3': 3,
        'Heading3IR': 3,
        'Heading3GM': 3,
        'Heading3AMC': 3,
        'Heading4IR': 4,
        'Heading4AMC': 4,
        'Heading4GM': 4,
        'Heading5AMC': 5,
        'Heading5GM': 5,
        'Heading5IR': 5,
        'Heading5OrgManual': 5,
        'Heading6OrgManual': 6,
        'Heading6AMC': 6,
        'Heading6GM': 6,
        'Heading7OrgManual': 7,
    }

    PART_PATTERN = re.compile(
        r'ANNEX\s+([IVX]+)\s+\(Part-([A-Z]+)\)',
        re.IGNORECASE,
    )
    SUBPART_PATTERN = re.compile(
        r'SUBPART\s+([A-Z]+)[\s:–-]+(.*)',
        re.IGNORECASE,
    )
    SECTION_PATTERN = re.compile(
        r'SECTION\s+(\d+)\s*[–-]\s*(.*)',
        re.IGNORECASE,
    )
    ARTICLE_IR_PATTERN = re.compile(
        r'^((?:[A-Z]{2,}\s+)?[A-Z]{2,}(?:\.[A-Z0-9-]+)+(?:\([^)]*\)(?:;\([^)]*\))*)?)\s*(.*)',
    )
    ARTICLE_AMC_PATTERN = re.compile(
        r'^(AMC\d+\s*.+)',
        re.IGNORECASE,
    )
    ARTICLE_GM_PATTERN = re.compile(
        r'^(GM\d+\s*.+)',
        re.IGNORECASE,
    )
    BASIC_ARTICLE_PATTERN = re.compile(
        r'^Article\s+(\d+[A-Z]*)\s*[–-]?\s*(.*)',
        re.IGNORECASE,
    )
    CHAPTER_PATTERN = re.compile(
        r'^CHAPTER\s+([IVX]+)\s*[–-]\s*(.*)',
        re.IGNORECASE,
    )
    COVER_GM_PATTERN = re.compile(
        r'^((?:GM|AMC)\d+\s+Article\s+\d+.*)',
        re.IGNORECASE,
    )
    ANNEX_I_HEADING_PATTERN = re.compile(
        r'^ANNEX\s+I\s*[–-]\s*(.*)',
        re.IGNORECASE,
    )
    ANNEX_I_GM_PATTERN = re.compile(
        r'^((?:GM|AMC)\d+\s+Annex\s+I\b.*)',
        re.IGNORECASE,
    )

    # ── Public API ──────────────────────────────────────────────────────

    def parse_file(self, xml_path: Path, document_title: str | None = None) -> ParsedDocument:
        root = self._load_root(xml_path)
        paragraphs = self._extract_paragraphs(root)
        title = document_title or xml_path.stem
        if self._looks_like_article_structured(paragraphs, title):
            parts = self._parse_article_structured(paragraphs)
            parser_mode = 'article-structured'
        else:
            annex_parts = self._parse_parts(paragraphs)
            if self._has_cover_regulation(paragraphs):
                cover_parts = self._parse_cover_regulation(paragraphs)
                parts = cover_parts + annex_parts
                parser_mode = 'hybrid'
            else:
                parts = annex_parts
                parser_mode = 'part'
        return ParsedDocument(
            title=title,
            parts=parts,
            paragraph_count=len(paragraphs),
            parser_mode=parser_mode,
            style_counts=dict(Counter(p.style for p in paragraphs)),
        )

    # ── XML loading ─────────────────────────────────────────────────────

    def _load_root(self, xml_path: Path) -> etree._Element:
        tree = etree.parse(str(xml_path))
        return tree.getroot()

    def _extract_paragraphs(self, root: etree._Element) -> list[OfficeXMLParagraph]:
        paragraphs: list[OfficeXMLParagraph] = []
        xml_paragraphs = root.xpath('.//w:p', namespaces=self.NAMESPACES)
        for i, p in enumerate(xml_paragraphs):
            style = self._get_style(p)
            text = self._get_text(p)
            if not text.strip():
                continue
            level = self._style_level(style)
            is_list = 'ListLevel' in style
            list_level = None
            if is_list:
                match = re.search(r'ListLevel(\d+)', style)
                if match:
                    list_level = int(match.group(1))
            paragraphs.append(OfficeXMLParagraph(
                index=i,
                style=style,
                text=text,
                level=level,
                is_list=is_list,
                list_level=list_level,
            ))
        return paragraphs

    def _get_style(self, paragraph: etree._Element) -> str:
        pStyle = paragraph.find('.//w:pStyle', namespaces=self.NAMESPACES)
        if pStyle is not None:
            return pStyle.get(f'{{{self.NAMESPACES["w"]}}}val')
        return 'Normal'

    def _get_text(self, paragraph: etree._Element) -> str:
        texts = paragraph.xpath('.//w:t/text()', namespaces=self.NAMESPACES)
        return ''.join(texts)

    def _style_level(self, style: str) -> int:
        if style in self.HIERARCHY:
            return self.HIERARCHY[style]
        if style.startswith('Heading'):
            match = re.search(r'(\d+)', style)
            if match:
                level = int(match.group(1))
                if 1 <= level <= 7:
                    return level
        return 0

    # ── Mode detection ──────────────────────────────────────────────────

    def _looks_like_article_structured(
        self, paragraphs: list[OfficeXMLParagraph], title: str,
    ) -> bool:
        part_hits = sum(
            1 for p in paragraphs
            if p.style == 'Heading1' and self.PART_PATTERN.search(p.text)
        )
        if part_hits >= 1:
            return False
        article_hits = sum(
            1 for p in paragraphs
            if p.level >= 2 and self.BASIC_ARTICLE_PATTERN.match(p.text)
        )
        return article_hits >= 3

    # ── Cover regulation (hybrid mode) ──────────────────────────────────

    def _first_part_annex_idx(self, paragraphs: list[OfficeXMLParagraph]) -> int:
        for idx, para in enumerate(paragraphs):
            if para.style == 'Heading1' and self.PART_PATTERN.search(para.text):
                return idx
        return len(paragraphs)

    def _has_cover_regulation(self, paragraphs: list[OfficeXMLParagraph]) -> bool:
        first_part_idx = self._first_part_annex_idx(paragraphs)
        cr_count = sum(
            1 for p in paragraphs[:first_part_idx]
            if p.style == 'Heading2CR' and self.BASIC_ARTICLE_PATTERN.match(p.text)
        )
        return cr_count >= 3

    def _identify_cover_entry(self, para: OfficeXMLParagraph) -> dict[str, str] | None:
        if para.style == 'Heading2CR':
            match = self.BASIC_ARTICLE_PATTERN.match(para.text)
            if match:
                return {
                    'entry_type': 'IR',
                    'entry_ref': f'Article {match.group(1)}',
                    'title': match.group(2).strip() or para.text,
                }

        if para.style in ('Heading3GM', 'Heading3IR', 'Heading3AMC'):
            text = para.text.strip()
            gm_match = self.COVER_GM_PATTERN.match(text)
            if gm_match:
                entry_type = 'AMC' if text.upper().startswith('AMC') else 'GM'
                return {'entry_type': entry_type, 'entry_ref': gm_match.group(1), 'title': text}
            annex_match = self.ANNEX_I_GM_PATTERN.match(text)
            if annex_match:
                entry_type = 'AMC' if text.upper().startswith('AMC') else 'GM'
                return {'entry_type': entry_type, 'entry_ref': annex_match.group(1), 'title': text}

        if para.style == 'Heading1':
            match = self.ANNEX_I_HEADING_PATTERN.match(para.text)
            if match:
                return {
                    'entry_type': 'IR',
                    'entry_ref': 'Annex I',
                    'title': match.group(1).strip() or 'Definitions',
                }

        return None

    def _parse_cover_regulation(self, paragraphs: list[OfficeXMLParagraph]) -> list[ParsedPart]:
        first_part_idx = self._first_part_annex_idx(paragraphs)

        first_cr_idx = None
        for idx in range(first_part_idx):
            para = paragraphs[idx]
            if para.style == 'Heading2CR' and self.BASIC_ARTICLE_PATTERN.match(para.text):
                first_cr_idx = idx
                break
        if first_cr_idx is None:
            return []

        entry_positions: list[tuple[int, dict[str, str]]] = []
        for idx in range(first_cr_idx, first_part_idx):
            info = self._identify_cover_entry(paragraphs[idx])
            if info:
                entry_positions.append((idx, info))

        if not entry_positions:
            return []

        all_entries: list[ParsedEntry] = []
        for order, (start, info) in enumerate(entry_positions):
            end = entry_positions[order + 1][0] if order + 1 < len(entry_positions) else first_part_idx
            all_entries.append(self._parse_cover_entry(paragraphs, info, order + 1, start, end))

        reg_entries: list[ParsedEntry] = []
        annex_entries: list[ParsedEntry] = []
        for entry in all_entries:
            ref_normalized = entry.entry_ref.replace('\xa0', ' ')
            if 'Annex I' in ref_normalized:
                annex_entries.append(entry)
            else:
                reg_entries.append(entry)

        parts: list[ParsedPart] = []
        if reg_entries:
            section = ParsedSection(title='Cover Regulation', sort_order=1, entries=reg_entries)
            subpart = ParsedSubpart(code='REGULATION', title='Cover Regulation', sort_order=1, sections=[section])
            parts.append(ParsedPart(code='REGULATION', title='Cover Regulation', annex='COVER', sort_order=0, subparts=[subpart]))
        if annex_entries:
            section = ParsedSection(title='Definitions', sort_order=1, entries=annex_entries)
            subpart = ParsedSubpart(code='ANNEX-I', title='Definitions', sort_order=1, sections=[section])
            parts.append(ParsedPart(code='ANNEX-I', title='ANNEX I \u2013 Definitions', annex='I', sort_order=0, subparts=[subpart]))
        return parts

    def _parse_cover_entry(
        self,
        paragraphs: list[OfficeXMLParagraph],
        info: dict[str, str],
        sort_order: int,
        start_idx: int,
        end_idx: int,
    ) -> ParsedEntry:
        body_lines: list[str] = []
        for idx in range(start_idx + 1, end_idx):
            formatted = self._format_paragraph(paragraphs[idx])
            if formatted:
                body_lines.append(formatted)

        return ParsedEntry(
            entry_ref=info['entry_ref'],
            entry_type=info['entry_type'],
            title=info['title'],
            body_lines=body_lines,
            sort_order=sort_order,
            source_locator=f'paragraphs:{start_idx + 1}-{end_idx}',
        )

    # ── Part-structured parsing ─────────────────────────────────────────

    def _parse_parts(self, paragraphs: list[OfficeXMLParagraph]) -> list[ParsedPart]:
        part_indices: list[tuple[int, str, str, str]] = []
        for i, para in enumerate(paragraphs):
            if para.style == 'Heading1':
                match = self.PART_PATTERN.search(para.text)
                if match and 'Appendix' not in para.text:
                    annex = match.group(1)
                    code = match.group(2)
                    part_indices.append((i, annex, code, para.text))

        parts: list[ParsedPart] = []
        for idx, (start, annex, code, title) in enumerate(part_indices):
            end = part_indices[idx + 1][0] if idx + 1 < len(part_indices) else len(paragraphs)
            subparts = self._parse_subparts(paragraphs, start, end)
            parts.append(ParsedPart(
                code=code,
                title=title,
                annex=annex,
                sort_order=idx + 1,
                subparts=subparts,
            ))
        return parts

    def _parse_subparts(
        self, paragraphs: list[OfficeXMLParagraph], start_idx: int, end_idx: int,
    ) -> list[ParsedSubpart]:
        subpart_indices: list[tuple[int, str, str]] = []
        for i in range(start_idx, end_idx):
            para = paragraphs[i]
            if para.style in ('Heading2', 'Heading2IR'):
                match = self.SUBPART_PATTERN.search(para.text)
                if match:
                    subpart_indices.append((i, match.group(1), match.group(2).strip()))

        subparts: list[ParsedSubpart] = []
        for idx, (sp_start, code, title) in enumerate(subpart_indices):
            sp_end = subpart_indices[idx + 1][0] if idx + 1 < len(subpart_indices) else end_idx
            sections = self._parse_sections(paragraphs, sp_start, sp_end)
            subparts.append(ParsedSubpart(
                code=code, title=title, sort_order=idx + 1, sections=sections,
            ))

        if not subpart_indices:
            sections = self._parse_sections(paragraphs, start_idx, end_idx)
            if sections:
                subparts.append(ParsedSubpart(
                    code='GENERAL', title='General', sort_order=1, sections=sections,
                ))
        return subparts

    def _parse_sections(
        self, paragraphs: list[OfficeXMLParagraph], start_idx: int, end_idx: int,
    ) -> list[ParsedSection]:
        section_indices: list[tuple[int, str]] = []
        for i in range(start_idx, end_idx):
            para = paragraphs[i]
            if para.style == 'Heading3':
                match = self.SECTION_PATTERN.search(para.text)
                if match:
                    section_indices.append((i, para.text))

        sections: list[ParsedSection] = []
        if not section_indices:
            entries = self._parse_entries(paragraphs, start_idx, end_idx)
            if entries:
                sections.append(ParsedSection(title='General', sort_order=1, entries=entries))
        else:
            for idx, (sec_start, title) in enumerate(section_indices):
                sec_end = section_indices[idx + 1][0] if idx + 1 < len(section_indices) else end_idx
                entries = self._parse_entries(paragraphs, sec_start, sec_end)
                sections.append(ParsedSection(title=title, sort_order=idx + 1, entries=entries))
        return sections

    def _parse_entries(
        self, paragraphs: list[OfficeXMLParagraph], start_idx: int, end_idx: int,
    ) -> list[ParsedEntry]:
        entry_indices: list[tuple[int, dict[str, str]]] = []
        for i in range(start_idx, end_idx):
            info = self._identify_entry(paragraphs[i])
            if info:
                entry_indices.append((i, info))

        entries: list[ParsedEntry] = []
        for idx, (ent_start, info) in enumerate(entry_indices):
            ent_end = entry_indices[idx + 1][0] if idx + 1 < len(entry_indices) else end_idx
            entries.append(self._parse_entry(paragraphs, info, idx + 1, ent_start, ent_end))
        return entries

    def _identify_entry(self, para: OfficeXMLParagraph) -> dict[str, str] | None:
        valid_styles = (
            'Heading3IR', 'Heading3AMC', 'Heading3GM',
            'Heading4IR', 'Heading4AMC', 'Heading4GM',
            'Heading5AMC', 'Heading5GM', 'Heading5IR',
        )
        if para.style not in valid_styles:
            return None
        text = para.text.strip()

        if 'AMC' in para.style:
            match = self.ARTICLE_AMC_PATTERN.match(text)
            if match:
                return {'entry_type': 'AMC', 'entry_ref': match.group(1), 'title': text}

        if 'GM' in para.style:
            match = self.ARTICLE_GM_PATTERN.match(text)
            if match:
                return {'entry_type': 'GM', 'entry_ref': match.group(1), 'title': text}

        if 'IR' in para.style:
            match = self.ARTICLE_IR_PATTERN.match(text)
            if match:
                return {'entry_type': 'IR', 'entry_ref': match.group(1), 'title': text}

        # Style/content mismatch: check text prefix regardless of style
        amc_match = self.ARTICLE_AMC_PATTERN.match(text)
        if amc_match:
            return {'entry_type': 'AMC', 'entry_ref': amc_match.group(1), 'title': text}
        gm_match = self.ARTICLE_GM_PATTERN.match(text)
        if gm_match:
            return {'entry_type': 'GM', 'entry_ref': gm_match.group(1), 'title': text}
        ir_match = self.ARTICLE_IR_PATTERN.match(text)
        if ir_match:
            return {'entry_type': 'IR', 'entry_ref': ir_match.group(1), 'title': text}

        return {'entry_type': 'INFO', 'entry_ref': text[:50], 'title': text}

    def _parse_entry(
        self,
        paragraphs: list[OfficeXMLParagraph],
        info: dict[str, str],
        sort_order: int,
        start_idx: int,
        end_idx: int,
    ) -> ParsedEntry:
        body_lines: list[str] = []
        for idx in range(start_idx + 1, end_idx):
            formatted = self._format_paragraph(paragraphs[idx])
            if formatted:
                body_lines.append(formatted)

        return ParsedEntry(
            entry_ref=info['entry_ref'],
            entry_type=info['entry_type'],
            title=info['title'],
            body_lines=body_lines,
            sort_order=sort_order,
            source_locator=f'paragraphs:{start_idx + 1}-{end_idx}',
        )

    # ── Article-structured parsing ──────────────────────────────────────

    def _parse_article_structured(
        self, paragraphs: list[OfficeXMLParagraph],
    ) -> list[ParsedPart]:
        chapter_indices = self._find_chapter_boundaries(paragraphs)
        if chapter_indices:
            return self._parse_chaptered_articles(paragraphs, chapter_indices)
        return self._parse_flat_articles(paragraphs)

    def _find_chapter_boundaries(
        self, paragraphs: list[OfficeXMLParagraph],
    ) -> list[tuple[int, str, str]]:
        """Find CHAPTER headings in the document. Returns [(idx, code, title)]."""
        chapters: list[tuple[int, str, str]] = []
        for i, para in enumerate(paragraphs):
            if para.style == 'Heading1':
                match = self.CHAPTER_PATTERN.match(para.text.strip())
                if match:
                    chapters.append((i, match.group(1), match.group(2).strip()))
        return chapters

    ROMAN_SECTION_PATTERN = re.compile(
        r'^SECTION\s+([IVX]+)\s*[–-]\s*(.*)',
        re.IGNORECASE,
    )

    def _find_section_boundaries(
        self, paragraphs: list[OfficeXMLParagraph], start_idx: int, end_idx: int,
    ) -> list[tuple[int, str]]:
        """Find SECTION headings within a chapter range."""
        sections: list[tuple[int, str]] = []
        for i in range(start_idx, end_idx):
            para = paragraphs[i]
            if para.style == 'Heading2':
                text = para.text.strip()
                if (self.SECTION_PATTERN.match(text)
                        or self.ROMAN_SECTION_PATTERN.match(text)):
                    sections.append((i, text))
        return sections

    def _collect_article_entries(
        self, paragraphs: list[OfficeXMLParagraph], start_idx: int, end_idx: int,
    ) -> list[ParsedEntry]:
        """Collect article entries within a paragraph range."""
        entry_indices: list[tuple[int, dict[str, str]]] = []
        for i in range(start_idx, end_idx):
            info = self._identify_article_entry(paragraphs[i])
            if info:
                entry_indices.append((i, info))

        entries: list[ParsedEntry] = []
        for idx, (start, info) in enumerate(entry_indices):
            end = entry_indices[idx + 1][0] if idx + 1 < len(entry_indices) else end_idx
            entries.append(self._parse_entry(paragraphs, info, idx + 1, start, end))
        return entries

    def _parse_chaptered_articles(
        self,
        paragraphs: list[OfficeXMLParagraph],
        chapter_indices: list[tuple[int, str, str]],
    ) -> list[ParsedPart]:
        parts: list[ParsedPart] = []
        for ch_idx, (ch_start, ch_code, ch_title) in enumerate(chapter_indices):
            ch_end = (
                chapter_indices[ch_idx + 1][0]
                if ch_idx + 1 < len(chapter_indices)
                else len(paragraphs)
            )

            section_boundaries = self._find_section_boundaries(paragraphs, ch_start, ch_end)
            subparts: list[ParsedSubpart] = []

            if section_boundaries:
                for sec_idx, (sec_start, sec_title) in enumerate(section_boundaries):
                    sec_end = (
                        section_boundaries[sec_idx + 1][0]
                        if sec_idx + 1 < len(section_boundaries)
                        else ch_end
                    )
                    entries = self._collect_article_entries(paragraphs, sec_start, sec_end)
                    if entries:
                        section = ParsedSection(
                            title=sec_title, sort_order=sec_idx + 1, entries=entries,
                        )
                        sec_code = re.sub(r'SECTION\s+', 'SEC-', sec_title.split('–')[0].strip())
                        subparts.append(ParsedSubpart(
                            code=sec_code.strip(),
                            title=sec_title,
                            sort_order=sec_idx + 1,
                            sections=[section],
                        ))
            else:
                entries = self._collect_article_entries(paragraphs, ch_start, ch_end)
                if entries:
                    section = ParsedSection(title=ch_title, sort_order=1, entries=entries)
                    subparts.append(ParsedSubpart(
                        code='GENERAL', title=ch_title,
                        sort_order=1, sections=[section],
                    ))

            if subparts:
                parts.append(ParsedPart(
                    code=f'CH-{ch_code}',
                    title=f'CHAPTER {ch_code} – {ch_title}',
                    annex='',
                    sort_order=ch_idx + 1,
                    subparts=subparts,
                ))

        return parts

    def _parse_flat_articles(
        self, paragraphs: list[OfficeXMLParagraph],
    ) -> list[ParsedPart]:
        """Fallback: flat article parsing when no chapters are detected."""
        entries = self._collect_article_entries(paragraphs, 0, len(paragraphs))
        if not entries:
            return []
        section = ParsedSection(title='Articles', sort_order=1, entries=entries)
        subpart = ParsedSubpart(
            code='ARTICLES', title='Articles', sort_order=1, sections=[section],
        )
        return [ParsedPart(
            code='ARTICLES', title='Articles', annex='', sort_order=1, subparts=[subpart],
        )]

    _TOC_STYLES = frozenset(('TOC1', 'TOC2', 'TOC3', 'TOC4', 'TOC5'))

    def _identify_article_entry(self, para: OfficeXMLParagraph) -> dict[str, str] | None:
        if para.style in self._TOC_STYLES:
            return None

        info = self._identify_entry(para)
        if info:
            return info

        if para.level >= 2:
            match = self.BASIC_ARTICLE_PATTERN.match(para.text.strip())
            if match:
                return {
                    'entry_type': 'IR',
                    'entry_ref': f'Article {match.group(1)}',
                    'title': match.group(2).strip() or para.text.strip(),
                }
        return None

    # ── Formatting ──────────────────────────────────────────────────────

    def _format_paragraph(self, para: OfficeXMLParagraph) -> str | None:
        text = para.text.strip()
        if not text:
            return None

        if para.is_list and para.list_level is not None:
            indent = '  ' * para.list_level
            if text[0].isdigit():
                return f'{indent}1. {text}'
            return f'{indent}- {text}'

        return text
