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

from claw_easa.references import REFERENCE_CORE, REFERENCE_PREFIX

logger = logging.getLogger(__name__)

#: ``parser_mode`` of a document whose layout none of the parsing modes
#: recognised.  Distinct from a mode that ran and legitimately found nothing.
UNRECOGNISED_MODE = 'unrecognised'

#: Which reading of a source document the entries currently held came from,
#: recorded against every source this parser persists.  Bumped when a change
#: here alters the entries extracted from an unchanged document, so a corpus
#: can be told apart from one built by an earlier reading of the same file.
#: A version axis of its own: the tool, the schema and the EASA edition held
#: all move independently of it.
PARSER_VERSION = 'easa-office-xml/1'

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
class ParsedCoverRegulation:
    """The cover-regulation articles a document states for one regulation.

    A consolidated rulebook publishes a cover regulation per regulation it
    holds — 'Article 1 Subject matter', 'Article 2 Scope' — and states them
    wherever its layout puts them, including after the annexes of the
    regulation before.  They are not an annex and EASA gives them no part
    code, so they are held here rather than forced into the part hierarchy.

    ``regulation`` is the identifier the *document* names for the block, read
    off the heading that introduces it ('Cover Regulation to Implementing
    Regulation (EU) 2023/203').  ``None`` where no heading names one: a block
    is never attributed by proximity.
    """
    regulation: str | None
    title: str
    entries: list[ParsedEntry] = field(default_factory=list)


@dataclass
class ParsedDocument:
    """Result of parsing an EASA Easy Access Rules document."""
    title: str = ''
    paragraph_count: int = 0
    parser_mode: str = ''
    parts: list[ParsedPart] = field(default_factory=list)
    style_counts: dict[str, int] = field(default_factory=dict)
    #: Cover-regulation blocks the document states, in document order.  Kept
    #: beside the parts rather than among them: a cover regulation is not an
    #: annex, and inventing a part for it would put a code in the corpus that
    #: no regulation states.  In ``hybrid`` mode the same articles are also
    #: filed as a 'REGULATION' part, which is the shape that mode has always
    #: had and what persistence reads.
    cover_regulations: list[ParsedCoverRegulation] = field(default_factory=list)


# ── Parser ──────────────────────────────────────────────────────────────────


class EASAOfficeXMLParser:
    """
    Production parser for EASA Easy Access Rules Office XML documents.

    Stateless: each call to parse_file() is self-contained.

    Supports three parsing modes:
    - ``part``: documents organised by ANNEX / Part-XXX (e.g. air-ops annexes)
    - ``article-structured``: documents organised by Articles (e.g. basic-regulation)
    - ``cs-structured``: certification specification EARs organised by CS headings
      rather than ANNEX / Part-XXX headings (e.g. CS-MMEL, CS-GEN-MMEL)
    - ``hybrid``: documents with both cover-regulation articles *and* Part annexes.
      A consolidated rulebook states one cover regulation per regulation it
      publishes, and states them wherever its own layout puts them — before
      the annexes, and again after them.
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

    # Regulation (EU) No 1321/2014 letters its later annexes — ANNEX Vb
    # (Part-ML), ANNEX Vc (Part-CAMO), ANNEX Vd (Part-CAO) — numbers three of
    # them — ANNEX II (Part-145), ANNEX III (Part-66), ANNEX IV (Part-147) —
    # and Regulation (EU) 2023/203 uses a dotted part code, ANNEX I
    # (Part-IS.I.OR).  A part code that must start with a letter drops the
    # numeric annexes onto whichever part precedes them.
    #
    # The annex label is optional.  A regulation stating a single annex does
    # not number it: Delegated Regulation (EU) 2022/1645 heads its one annex
    # 'ANNEX — INFORMATION SECURITY — ORGANISATION REQUIREMENTS
    # [PART-IS.D.OR]'.  Requiring a numeral reads no part out of it.
    ANNEX_HEADING_PATTERN = re.compile(
        r'^ANNEX(?:\s+([IVX]+[a-z]?))?\b',
        re.IGNORECASE,
    )
    # How an annex heading names the part it states.  Air Ops parenthesises
    # the marker directly after the numeral — 'ANNEX III (Part-ORO)' — while
    # the Information Security rulebook brackets it after the annex title, and
    # spells 'Part' in upper case: 'ANNEX I — INFORMATION SECURITY —
    # AUTHORITY REQUIREMENTS [PART-IS.AR]'.  A part code may carry dots
    # (IS.I.OR) or a slash (ATM/ANS.AR).
    #
    # The marker has to *close* the heading.  That is what separates an annex
    # a document states from one it only amends: 'ANNEX III — INFORMATION
    # SECURITY — ANNEXES VI (Part-ARA) and VII (Part-ORA) to Regulation (EU)
    # No 1178/2011' names two parts mid-heading and states neither of them —
    # reading a part out of it would invent an ARA and an ORA annex that the
    # Information Security regulations do not contain.
    PART_MARKER_PATTERN = re.compile(
        r'[(\[]\s*Part[-\s]\s*([A-Z0-9][A-Z0-9./]*?)\s*[)\]][\s.;:–—-]*$',
        re.IGNORECASE,
    )
    SUBPART_PATTERN = re.compile(
        r'SUBPART\s+([A-Z]+)[\s:–-]+(.*)',
        re.IGNORECASE,
    )
    # Part-ORA numbers its sections in Roman — 'SECTION I – General',
    # 'SECTION II – Management' — where Air Ops numbers them in Arabic.
    # Reading digits alone recognises no section in Part-ORA, collapsing a
    # subpart into one 'General' section that states its management rules.
    SECTION_PATTERN = re.compile(
        r'SECTION\s+(\d+|[IVX]+)\s*[–-]\s*(.*)',
        re.IGNORECASE,
    )
    # Single-letter (M.A.201) and numeric (145.A.30, 21.A.139) part codes are
    # references too, so the reference shape is shared with retrieval.
    ARTICLE_IR_PATTERN = re.compile(
        f'^({REFERENCE_PREFIX}{REFERENCE_CORE})' + r'\s*(.*)',
    )
    ARTICLE_AMC_PATTERN = re.compile(
        r'^(AMC\d+\s*.+)',
        re.IGNORECASE,
    )
    ARTICLE_GM_PATTERN = re.compile(
        r'^(GM\d+\s*.+)',
        re.IGNORECASE,
    )
    # An entry heading sits at whatever depth its subdivision needs: a rule of
    # a plain section is a 'Heading4IR', the same rule under a chapter of that
    # section is a 'Heading5IR', and the AMC of that chapter rule is a
    # 'Heading6AMC'.  The depth says nothing about what the heading is, so an
    # entry is recognised by the IR/AMC/GM suffix at any of them — listing
    # levels leaves the deeper soft law unread and appended to the body of the
    # implementing rule above it, presenting guidance as binding text.
    ENTRY_STYLE_PATTERN = re.compile(r'^Heading[2-7](IR|AMC|GM)$')
    BASIC_ARTICLE_PATTERN = re.compile(
        r'^Article\s+(\d+[A-Z]*)\s*[–-]?\s*(.*)',
        re.IGNORECASE,
    )
    # The Basic Regulation states its soft law as 'AMC1 Article 3 – Common
    # requirements', repeating the title of the article it qualifies.  An
    # article reference carries no dotted code, so canonicalisation cannot
    # strip that title back off at lookup time the way it does for 'AMC1
    # M.A.201 Responsibilities': the entry is stored under the whole heading
    # and a lookup of 'AMC1 Article 3' — the reference EASA cites it by —
    # answers nothing.  A paragraph selector stays in, or the several AMCs of
    # one article would collide under a single reference.
    ARTICLE_SOFT_LAW_PATTERN = re.compile(
        r'^((?:AMC|GM)\d+\s+Article\s+\d+[A-Z]*(?:;?\([^)]*\))*)',
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
    # A cover-regulation article sits at whatever depth its document needs.
    # Air Ops heads one at 'Heading2CR'; a rulebook consolidating two
    # regulations spends a level on naming each of them and heads its cover
    # articles one level down, at 'Heading3CR'.  A style fixed at one level
    # reads no article out of the other, and every article of that cover
    # regulation is either dropped or appended to the body of whichever annex
    # rule precedes it — guidance of one regulation presented as the text of
    # another.
    COVER_ARTICLE_STYLE_PATTERN = re.compile(r'^Heading[1-7]CR$')
    # How a heading names the regulation the material under it belongs to:
    # 'Implementing Regulation (EU) 2023/203', 'Cover Regulation to Delegated
    # Regulation (EU) 2022/1645'.  'No' is kept because EASA cites the older
    # acts with it — '(EU) No 1178/2011' — and an identifier that drops it
    # matches no declaration.
    REGULATION_IDENTIFIER_PATTERN = re.compile(
        r'\((EU|EC)\)\s*(No\s*)?(\d{4}/\d+)',
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
    CS_ENTRY_STYLES = frozenset((
        'Heading2CS', 'Heading2GM',
        'Heading3CS', 'Heading3GM',
        'Heading4CS', 'Heading4GM',
        'Heading5CS', 'Heading5GM',
    ))
    CS_ENTRY_PATTERN = re.compile(
        r'^(CS\s+[A-Z][A-Z0-9]*(?:\.[A-Z0-9-]+)+)\b\s*(.*)',
        re.IGNORECASE,
    )

    # ── Public API ──────────────────────────────────────────────────────

    def parse_file(self, xml_path: Path, document_title: str | None = None) -> ParsedDocument:
        root = self._load_root(xml_path)
        paragraphs = self._extract_paragraphs(root)
        title = document_title or xml_path.stem
        # Read in every mode, and read before the annexes are cut: a cover
        # regulation the document states between or after its annexes is
        # otherwise appended to the body of whichever annex rule precedes it,
        # which files the text of one regulation under another.
        cover_regulations = self._parse_cover_regulations(paragraphs)
        if self._looks_like_article_structured(paragraphs, title):
            parts = self._parse_article_structured(paragraphs)
            parser_mode = 'article-structured'
        elif self._looks_like_cs_structured(paragraphs):
            parts = self._parse_cs_structured(paragraphs, title)
            parser_mode = 'cs-structured'
        else:
            annex_parts = self._parse_parts(paragraphs, self._cover_stops(paragraphs))
            if self._has_cover_regulation(paragraphs):
                parts = self._parse_cover_regulation(paragraphs) + annex_parts
                parser_mode = 'hybrid'
            else:
                parts = annex_parts
                parser_mode = 'part'
        if not parts:
            # No mode found any structure: the layout is not one this parser
            # understands.  Saying 'part' here would report a clean parse of a
            # document that was never read.
            logger.warning(
                "No structure extracted from %s (%d paragraphs) — "
                "the document layout was not recognised",
                xml_path.name, len(paragraphs),
            )
            parser_mode = UNRECOGNISED_MODE
        return ParsedDocument(
            title=title,
            parts=parts,
            paragraph_count=len(paragraphs),
            parser_mode=parser_mode,
            style_counts=dict(Counter(p.style for p in paragraphs)),
            cover_regulations=cover_regulations,
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
        if self._part_headings(paragraphs):
            return False
        article_hits = sum(
            1 for p in paragraphs
            if p.level >= 2 and self.BASIC_ARTICLE_PATTERN.match(p.text)
        )
        return article_hits >= 3

    def _looks_like_cs_structured(self, paragraphs: list[OfficeXMLParagraph]) -> bool:
        """Detect certification specification EARs without ANNEX / Part headings.

        CS-MMEL and CS-GEN-MMEL are Easy Access Rules, but their XML uses
        ``Heading2CS`` / ``Heading3GM`` entries directly under top-level
        headings instead of the ANNEX -> SUBPART -> SECTION hierarchy used by
        Air Ops and similar rulebooks.
        """
        if self._part_headings(paragraphs):
            return False

        cs_hits = sum(1 for p in paragraphs if self._identify_cs_entry(p))
        return cs_hits >= 3

    # ── Cover regulation (hybrid mode) ──────────────────────────────────

    def _first_part_annex_idx(self, paragraphs: list[OfficeXMLParagraph]) -> int:
        headings = self._part_headings(paragraphs)
        return headings[0][0] if headings else len(paragraphs)

    def _has_cover_regulation(self, paragraphs: list[OfficeXMLParagraph]) -> bool:
        """Whether hybrid mode applies: a cover regulation before the annexes.

        Deliberately the layout hybrid mode was written for — ``Heading2CR``
        articles ahead of the first annex — so that a document already parsed
        as hybrid keeps exactly the parts it had.  Cover material this does not
        cover is not dropped: it is read into ``cover_regulations`` instead of
        being turned into a part code no regulation states.
        """
        first_part_idx = self._first_part_annex_idx(paragraphs)
        cr_count = sum(
            1 for p in paragraphs[:first_part_idx]
            if p.style == 'Heading2CR' and self.BASIC_ARTICLE_PATTERN.match(p.text)
        )
        return cr_count >= 3

    def _cover_article(self, para: OfficeXMLParagraph) -> dict[str, str] | None:
        """The cover-regulation article a ``Heading<n>CR`` paragraph states."""
        if not self.COVER_ARTICLE_STYLE_PATTERN.match(para.style):
            return None
        match = self.BASIC_ARTICLE_PATTERN.match(para.text.strip())
        if not match:
            return None
        return {
            'entry_type': 'IR',
            'entry_ref': f'Article {match.group(1)}',
            'title': match.group(2).strip() or para.text.strip(),
        }

    def _identify_cover_entry(self, para: OfficeXMLParagraph) -> dict[str, str] | None:
        # An annex heading states a part, never a cover article: reading one as
        # cover material would file a whole annex under the cover regulation
        # that precedes it.
        if self._part_heading(para) is not None:
            return None

        article = self._cover_article(para)
        if article:
            return article

        # The soft law of a cover article sits one level below whatever level
        # the article itself is at, so these styles are matched by shape rather
        # than listed: 'GM1 Article 4' is guidance on a cover article wherever
        # the document puts it.
        if self.ENTRY_STYLE_PATTERN.match(para.style):
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

    def _regulation_heading(self, para: OfficeXMLParagraph) -> str | None:
        """The regulation identifier a heading names, or ``None``.

        Only a heading that is not itself an annex heading counts.  'ANNEX III
        \u2014 INFORMATION SECURITY \u2014 ANNEXES VI (Part-ARA) and VII (Part-ORA) to
        Regulation (EU) No 1178/2011' names the regulation this document
        *amends*; taking that as the regulation stating what follows would
        attribute material to a regulation this document does not publish.
        """
        if not para.style.startswith('Heading'):
            return None
        text = re.sub(r'\s+', ' ', para.text.replace('\xa0', ' ')).strip()
        if text.upper().startswith('ANNEX'):
            return None
        match = self.REGULATION_IDENTIFIER_PATTERN.search(text)
        if not match:
            return None
        number = 'No ' if match.group(2) else ''
        return f'({match.group(1).upper()}) {number}{match.group(3)}'

    def _regulation_boundaries(
        self, paragraphs: list[OfficeXMLParagraph],
    ) -> list[tuple[int, str, str]]:
        """``(index, identifier, heading text)`` per regulation-naming heading."""
        boundaries: list[tuple[int, str, str]] = []
        for idx, para in enumerate(paragraphs):
            identifier = self._regulation_heading(para)
            if identifier is None:
                continue
            text = re.sub(r'\s+', ' ', para.text.replace('\xa0', ' ')).strip()
            boundaries.append((idx, identifier, text))
        return boundaries

    def _cover_entry_positions(
        self, paragraphs: list[OfficeXMLParagraph],
    ) -> list[tuple[int, dict[str, str]]]:
        """``(index, entry)`` per cover-regulation heading, document-wide."""
        positions: list[tuple[int, dict[str, str]]] = []
        for idx, para in enumerate(paragraphs):
            info = self._identify_cover_entry(para)
            if info:
                positions.append((idx, info))
        return positions

    def _governing_regulation(
        self, boundaries: list[tuple[int, str, str]], idx: int,
    ) -> tuple[str | None, str | None]:
        """The regulation the document states the paragraph at *idx* under."""
        governing: tuple[str | None, str | None] = (None, None)
        for boundary_idx, identifier, text in boundaries:
            if boundary_idx > idx:
                break
            governing = (identifier, text)
        return governing

    def _cover_blocks(
        self, paragraphs: list[OfficeXMLParagraph],
    ) -> list[tuple[int, int, str | None, str | None]]:
        """``(start, end, identifier, heading)`` per cover-regulation block.

        A block is opened by a ``Heading<n>CR`` article \u2014 the style EASA
        reserves for cover-regulation text \u2014 and runs to the next annex
        heading or the next block.  Only that style opens one: a 'GM1 Article
        4' heading is collected as cover guidance where it falls inside a
        block, and left to the annex that states it where it does not, so an
        annex is never cut short by a heading that merely cites an article.

        The start is pulled back to the heading that names the regulation
        ('Delegated Regulation (EU) 2022/1645') where one stands between the
        previous annex and the article, so that heading does not stay behind
        in the body of the annex before it.
        """
        article_indices = [
            idx for idx, para in enumerate(paragraphs) if self._cover_article(para)
        ]
        if not article_indices:
            return []

        part_indices = [idx for idx, _annex, _code, _text in self._part_headings(paragraphs)]
        boundaries = self._regulation_boundaries(paragraphs)

        opens: list[tuple[int, int, str | None, str | None]] = []
        for idx in article_indices:
            identifier, heading = self._governing_regulation(boundaries, idx)
            previous_part = max((pos for pos in part_indices if pos < idx), default=-1)
            if opens:
                _start, last_article, open_identifier, _heading = opens[-1]
                same_block = (
                    open_identifier == identifier
                    and not any(last_article < pos < idx for pos in part_indices)
                )
                if same_block:
                    opens[-1] = (_start, idx, open_identifier, _heading)
                    continue
            introduction = next(
                (pos for pos, _id, _text in boundaries if previous_part < pos < idx), idx,
            )
            opens.append((introduction, idx, identifier, heading))

        blocks: list[tuple[int, int, str | None, str | None]] = []
        for order, (start, last_article, identifier, heading) in enumerate(opens):
            next_open = opens[order + 1][0] if order + 1 < len(opens) else len(paragraphs)
            next_part = next(
                (pos for pos in part_indices if pos > last_article), len(paragraphs),
            )
            blocks.append((start, min(next_open, next_part), identifier, heading))
        return blocks

    def _cover_stops(self, paragraphs: list[OfficeXMLParagraph]) -> list[int]:
        """Indices at which an annex gives way to a cover regulation.

        An annex otherwise runs to the next annex heading, which in a
        consolidated rulebook swallows the cover regulation of the *next*
        regulation whole: its articles are appended to the body of the last
        rule of the previous regulation's annex, presenting the text of one
        regulation as part of another.
        """
        return [start for start, _end, _identifier, _heading in self._cover_blocks(paragraphs)]

    def _parse_cover_regulations(
        self, paragraphs: list[OfficeXMLParagraph],
    ) -> list[ParsedCoverRegulation]:
        """The cover regulations the document states, in document order.

        Blocks the same regulation heading governs are one cover regulation,
        however far apart the document states them.  Blocks no heading names a
        regulation for are kept together as one unnamed cover regulation and
        attributed by nothing: proximity is not provenance.
        """
        covers: dict[str | None, ParsedCoverRegulation] = {}
        for start, end, identifier, heading in self._cover_blocks(paragraphs):
            cover = covers.get(identifier)
            if cover is None:
                cover = ParsedCoverRegulation(
                    regulation=identifier,
                    title=heading or 'Cover Regulation',
                )
                covers[identifier] = cover
            for entry in self._collect_cover_entries(paragraphs, start, end):
                entry.sort_order = len(cover.entries) + 1
                cover.entries.append(entry)
        return [cover for cover in covers.values() if cover.entries]

    def _collect_cover_entries(
        self, paragraphs: list[OfficeXMLParagraph], start_idx: int, end_idx: int,
    ) -> list[ParsedEntry]:
        positions: list[tuple[int, dict[str, str]]] = [
            (idx, info)
            for idx, info in self._cover_entry_positions(paragraphs)
            if start_idx <= idx < end_idx
        ]
        entries: list[ParsedEntry] = []
        for order, (start, info) in enumerate(positions):
            end = positions[order + 1][0] if order + 1 < len(positions) else end_idx
            entries.append(self._parse_cover_entry(paragraphs, info, order + 1, start, end))
        return entries

    def _parse_cover_regulation(self, paragraphs: list[OfficeXMLParagraph]) -> list[ParsedPart]:
        """The cover regulation as parts \u2014 the shape hybrid mode has always had.

        Confined to the cover regulation stated before the first annex, which
        is the material this mode has always turned into a 'REGULATION' part.
        Everything else the document states is read into ``cover_regulations``.
        """
        first_part_idx = self._first_part_annex_idx(paragraphs)
        entries = self._collect_cover_entries(paragraphs, 0, first_part_idx)
        if not entries:
            return []

        reg_entries: list[ParsedEntry] = []
        annex_entries: list[ParsedEntry] = []
        for entry in entries:
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

    def _part_heading(self, para: OfficeXMLParagraph) -> tuple[str, str] | None:
        """The ``(annex_label, part_code)`` an annex heading states, or ``None``.

        The annex label is ``''`` where the regulation states a single,
        unnumbered annex.
        """
        if not para.style.startswith('Heading'):
            return None
        text = re.sub(r'\s+', ' ', para.text.replace('\xa0', ' ')).strip()
        if 'APPENDIX' in text.upper():
            return None
        annex_match = self.ANNEX_HEADING_PATTERN.match(text)
        if not annex_match:
            return None
        marker = self.PART_MARKER_PATTERN.search(text)
        if not marker:
            return None
        return annex_match.group(1) or '', marker.group(1)

    def _part_heading_style(self, paragraphs: list[OfficeXMLParagraph]) -> str | None:
        """The heading style at which this document states its annex parts.

        Air Ops, Aircrew and the continuing-airworthiness rulebook head an
        annex at ``Heading1``.  The Information Security rulebook consolidates
        two regulations, spends ``Heading1`` on naming each of them, and heads
        its annexes one level down at ``Heading2`` — so a level fixed at
        ``Heading1`` reads no part at all out of it, and the document falls
        through to article parsing as one undifferentiated 'ARTICLES' part
        that no regulation can be said to state.

        The level is therefore read off the document: the shallowest heading
        style that carries an annex heading naming a part.  Deeper headings
        that also name one are left to the subpart and entry rules, which is
        where a part's own subdivisions belong.
        """
        levels: dict[str, int] = {}
        for para in paragraphs:
            if self._part_heading(para) is None:
                continue
            levels.setdefault(para.style, para.level or 99)
        if not levels:
            return None
        return min(levels, key=lambda style: (levels[style], style))

    def _part_headings(
        self, paragraphs: list[OfficeXMLParagraph],
    ) -> list[tuple[int, str, str, str]]:
        """``(index, annex_label, part_code, heading_text)`` per annex part."""
        style = self._part_heading_style(paragraphs)
        if style is None:
            return []
        headings: list[tuple[int, str, str, str]] = []
        for idx, para in enumerate(paragraphs):
            if para.style != style:
                continue
            heading = self._part_heading(para)
            if heading is None:
                continue
            annex, code = heading
            headings.append((idx, annex, code, para.text))
        return headings

    def _parse_parts(
        self, paragraphs: list[OfficeXMLParagraph], stops: list[int] | None = None,
    ) -> list[ParsedPart]:
        """A part per annex the document states.

        *stops* are indices at which an annex ends early — where the document
        turns from the annexes of one regulation to the cover regulation of
        the next.  Without them the annex runs to the next annex heading and
        takes that cover regulation into the body of its last rule.
        """
        part_indices = self._part_headings(paragraphs)
        stops = stops or []

        parts: list[ParsedPart] = []
        for idx, (start, annex, code, title) in enumerate(part_indices):
            end = part_indices[idx + 1][0] if idx + 1 < len(part_indices) else len(paragraphs)
            end = min(end, next((stop for stop in stops if stop > start), end))
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

        # Air Ops scopes an annex before it subdivides it: 'ORO.GEN.005
        # Scope' is stated under ANNEX III (Part-ORO) itself, above SUBPART
        # GEN.  Reading only from the first SUBPART heading onwards drops it,
        # and drops every rule of a part that has no subparts at all.
        lead_sections = self._parse_sections(
            paragraphs, start_idx,
            subpart_indices[0][0] if subpart_indices else end_idx,
        )
        if lead_sections:
            subparts.append(ParsedSubpart(
                code='GENERAL', title='General', sort_order=1, sections=lead_sections,
            ))

        for idx, (sp_start, code, title) in enumerate(subpart_indices):
            sp_end = subpart_indices[idx + 1][0] if idx + 1 < len(subpart_indices) else end_idx
            sections = self._parse_sections(paragraphs, sp_start, sp_end)
            subparts.append(ParsedSubpart(
                code=code, title=title, sort_order=len(subparts) + 1, sections=sections,
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

        # Part-CAT states 'CAT.GEN.100 Competent authority' under SUBPART A,
        # above its first SECTION — the same lead-in shape a subpart has.
        lead_entries = self._parse_entries(
            paragraphs, start_idx,
            section_indices[0][0] if section_indices else end_idx,
        )
        if lead_entries:
            sections.append(ParsedSection(title='General', sort_order=1, entries=lead_entries))

        for idx, (sec_start, title) in enumerate(section_indices):
            sec_end = section_indices[idx + 1][0] if idx + 1 < len(section_indices) else end_idx
            entries = self._parse_entries(paragraphs, sec_start, sec_end)
            sections.append(ParsedSection(
                title=title, sort_order=len(sections) + 1, entries=entries,
            ))
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

    #: Air Ops uses level-2 headings for both a SUBPART title and a rule
    #: stated above it ('ORO.GEN.005 Scope').  Only a heading carrying a rule
    #: reference is an entry here, so these styles get no INFO fallback.
    _SUBPART_LEVEL_STYLES = frozenset(('Heading2IR', 'Heading2AMC', 'Heading2GM'))

    def _identify_entry(self, para: OfficeXMLParagraph) -> dict[str, str] | None:
        if not self.ENTRY_STYLE_PATTERN.match(para.style):
            return None
        text = para.text.strip()

        # Part-M and Part-145 state their soft law un-numbered — 'AMC
        # M.A.201(e)', 'GM M.A.302(b)' — where Part-CAMO numbers it, 'AMC1
        # CAMO.A.200'.  Only the numbered form matches ARTICLE_AMC_PATTERN, so
        # the un-numbered one is read for its reference instead; the style
        # still decides the type, or guidance would be typed as binding law.
        if 'AMC' in para.style:
            match = self.ARTICLE_AMC_PATTERN.match(text) or self.ARTICLE_IR_PATTERN.match(text)
            if match:
                ref = match.group(1)
                if ' Article ' in ref and ' – ' in ref:
                    ref = ref.split(' – ', 1)[0].strip()
                return {'entry_type': 'AMC', 'entry_ref': ref, 'title': text}

        if 'GM' in para.style:
            match = self.ARTICLE_GM_PATTERN.match(text) or self.ARTICLE_IR_PATTERN.match(text)
            if match:
                ref = match.group(1)
                if ' Article ' in ref and ' – ' in ref:
                    ref = ref.split(' – ', 1)[0].strip()
                return {'entry_type': 'GM', 'entry_ref': ref, 'title': text}

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

        if para.style in self._SUBPART_LEVEL_STYLES:
            return None

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

    # ── Certification-specification structured parsing ──────────────────

    def _parse_cs_structured(
        self, paragraphs: list[OfficeXMLParagraph], document_title: str,
    ) -> list[ParsedPart]:
        first_entry_idx = self._first_cs_entry_idx(paragraphs)
        if first_entry_idx is None:
            return []

        subpart_boundaries = self._find_cs_subpart_boundaries(paragraphs, first_entry_idx)
        if not subpart_boundaries:
            subpart_boundaries = [(0, 'GENERAL', document_title)]

        source_code = self._derive_cs_document_code(paragraphs, document_title)
        subparts: list[ParsedSubpart] = []
        for idx, (sp_start, code, title) in enumerate(subpart_boundaries):
            sp_end = (
                subpart_boundaries[idx + 1][0]
                if idx + 1 < len(subpart_boundaries)
                else len(paragraphs)
            )
            entries = self._collect_cs_entries(paragraphs, sp_start, sp_end)
            if not entries:
                continue
            section = ParsedSection(title=title, sort_order=1, entries=entries)
            subparts.append(ParsedSubpart(
                code=code,
                title=title,
                sort_order=idx + 1,
                sections=[section],
            ))

        if not subparts:
            return []

        return [ParsedPart(
            code=source_code,
            title=document_title,
            annex='',
            sort_order=1,
            subparts=subparts,
        )]

    def _first_cs_entry_idx(self, paragraphs: list[OfficeXMLParagraph]) -> int | None:
        for idx, para in enumerate(paragraphs):
            if self._identify_cs_entry(para):
                return idx
        return None

    def _find_cs_subpart_boundaries(
        self, paragraphs: list[OfficeXMLParagraph], first_entry_idx: int,
    ) -> list[tuple[int, str, str]]:
        boundaries: list[tuple[int, str, str]] = []
        for idx in range(first_entry_idx, -1, -1):
            para = paragraphs[idx]
            if para.style == 'Heading1':
                text = para.text.strip()
                if self._heading1_starts_cs_content(text, paragraphs, idx):
                    boundaries.append((idx, self._cs_subpart_code(text), text))
                    break

        for idx in range(first_entry_idx + 1, len(paragraphs)):
            para = paragraphs[idx]
            if para.style != 'Heading1':
                continue
            text = para.text.strip()
            if self._heading1_starts_cs_content(text, paragraphs, idx):
                boundaries.append((idx, self._cs_subpart_code(text), text))

        return boundaries

    def _heading1_starts_cs_content(
        self, text: str, paragraphs: list[OfficeXMLParagraph], idx: int,
    ) -> bool:
        text_upper = text.upper()
        if text_upper.startswith('SUBPART ') or text_upper.startswith('CS AND GM'):
            return True
        lookahead = paragraphs[idx + 1:idx + 12]
        return any(self._identify_cs_entry(p) for p in lookahead)

    def _cs_subpart_code(self, title: str) -> str:
        match = self.SUBPART_PATTERN.match(title)
        if match:
            return f'SUBPART-{match.group(1).upper()}'
        normalized = re.sub(r'[^A-Z0-9]+', '-', title.upper()).strip('-')
        return normalized[:40] or 'GENERAL'

    def _derive_cs_document_code(
        self, paragraphs: list[OfficeXMLParagraph], document_title: str,
    ) -> str:
        for para in paragraphs:
            info = self._identify_cs_entry(para)
            if info and info['entry_ref'].upper().startswith('CS '):
                ref = info['entry_ref'][3:].split('.')[0]
                return f'CS-{ref}'
        match = re.search(r'CS[-\s]+([A-Z][A-Z0-9-]+)', document_title, re.IGNORECASE)
        if match:
            return f"CS-{match.group(1).upper()}"
        return 'CS'

    def _collect_cs_entries(
        self, paragraphs: list[OfficeXMLParagraph], start_idx: int, end_idx: int,
    ) -> list[ParsedEntry]:
        entry_indices: list[tuple[int, dict[str, str]]] = []
        for idx in range(start_idx, end_idx):
            info = self._identify_cs_entry(paragraphs[idx])
            if info:
                entry_indices.append((idx, info))

        entries: list[ParsedEntry] = []
        for order, (start, info) in enumerate(entry_indices):
            end = entry_indices[order + 1][0] if order + 1 < len(entry_indices) else end_idx
            entries.append(self._parse_entry(paragraphs, info, order + 1, start, end))
        return entries

    def _identify_cs_entry(self, para: OfficeXMLParagraph) -> dict[str, str] | None:
        if para.style in self._TOC_STYLES or para.style not in self.CS_ENTRY_STYLES:
            return None

        text = para.text.strip()
        normalized_text = re.sub(r'\s+', ' ', text.replace('\xa0', ' ')).strip()
        cs_match = self.CS_ENTRY_PATTERN.match(normalized_text)
        if cs_match:
            return {'entry_type': 'CS', 'entry_ref': cs_match.group(1), 'title': text}

        gm_match = self.ARTICLE_GM_PATTERN.match(normalized_text)
        if gm_match:
            return {'entry_type': 'GM', 'entry_ref': gm_match.group(1), 'title': text}

        amc_match = self.ARTICLE_AMC_PATTERN.match(normalized_text)
        if amc_match:
            return {'entry_type': 'AMC', 'entry_ref': amc_match.group(1), 'title': text}

        # Appendices and ATA chapters in CS-MMEL/CS-GEN-MMEL are guidance
        # material sections, not standalone certification specifications.
        if normalized_text.upper().startswith(('APPENDIX ', 'ATA ')):
            return {'entry_type': 'GM', 'entry_ref': normalized_text, 'title': text}

        return None

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

    def _find_section_boundaries(
        self, paragraphs: list[OfficeXMLParagraph], start_idx: int, end_idx: int,
    ) -> list[tuple[int, str]]:
        """Find SECTION headings within a chapter range."""
        sections: list[tuple[int, str]] = []
        for i in range(start_idx, end_idx):
            para = paragraphs[i]
            if para.style == 'Heading2':
                text = para.text.strip()
                if self.SECTION_PATTERN.match(text):
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
