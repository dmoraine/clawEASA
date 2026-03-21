from __future__ import annotations

from dataclasses import dataclass
import re

from claw_easa.db import Database
from claw_easa.ingest.parser import ParsedDocument


@dataclass(frozen=True)
class PersistSummary:
    document_id: int
    parts: int
    subparts: int
    sections: int
    entries: int
    duplicate_entries_skipped: int = 0
    empty_entries_skipped: int = 0


def normalize_title(value: str) -> str:
    value = value.replace('\xa0', ' ')
    value = re.sub(r'\s+', ' ', value).strip()
    return value


def derive_display_title(entry_ref: str, raw_title: str) -> str:
    raw_title = normalize_title(raw_title)
    entry_ref = normalize_title(entry_ref)
    if raw_title.startswith(entry_ref):
        remainder = raw_title[len(entry_ref):].strip(' -\u2013\u2014\u00a0')
        if remainder:
            return remainder
    return raw_title


def normalize_entry_text(lines: list[str]) -> str:
    normalized_lines: list[str] = []
    for line in lines:
        cleaned = line.replace('\xa0', ' ')
        cleaned = re.sub(r'[ \t]+', ' ', cleaned).strip()
        normalized_lines.append(cleaned)

    while normalized_lines and normalized_lines[0] == '':
        normalized_lines.pop(0)
    while normalized_lines and normalized_lines[-1] == '':
        normalized_lines.pop()

    collapsed: list[str] = []
    previous_blank = False
    for line in normalized_lines:
        is_blank = line == ''
        if is_blank and previous_blank:
            continue
        collapsed.append(line)
        previous_blank = is_blank

    return '\n'.join(collapsed)


class CanonicalPersister:
    def __init__(self, db: Database) -> None:
        self.db = db

    def persist_document(self, document_id: int, parsed: ParsedDocument) -> PersistSummary:
        with self.db.connection() as conn:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM regulation_entries WHERE document_id = ?", (document_id,))
                cur.execute(
                    "DELETE FROM regulation_sections WHERE subpart_id IN "
                    "(SELECT id FROM regulation_subparts WHERE part_id IN "
                    " (SELECT id FROM regulation_parts WHERE document_id = ?))",
                    (document_id,),
                )
                cur.execute(
                    "DELETE FROM regulation_subparts WHERE part_id IN "
                    "(SELECT id FROM regulation_parts WHERE document_id = ?)",
                    (document_id,),
                )
                cur.execute("DELETE FROM regulation_parts WHERE document_id = ?", (document_id,))

                parts_count = subparts_count = sections_count = entries_count = 0
                duplicate_entries_skipped = 0
                empty_entries_skipped = 0

                seen_entries: set[tuple[int, int, int, str, str, str, str]] = set()
                seen_entry_refs_global: dict[str, int] = {}

                for part in parsed.parts:
                    cur.execute(
                        "INSERT INTO regulation_parts "
                        "(document_id, part_code, annex, title, sort_order) "
                        "VALUES (?, ?, ?, ?, ?)",
                        (document_id, normalize_title(part.code),
                         normalize_title(part.annex), normalize_title(part.title),
                         part.sort_order),
                    )
                    part_id = cur.lastrowid
                    parts_count += 1

                    for subpart in part.subparts:
                        cur.execute(
                            "INSERT INTO regulation_subparts "
                            "(part_id, subpart_code, title, sort_order) "
                            "VALUES (?, ?, ?, ?)",
                            (part_id, normalize_title(subpart.code),
                             normalize_title(subpart.title), subpart.sort_order),
                        )
                        subpart_id = cur.lastrowid
                        subparts_count += 1

                        for section in subpart.sections:
                            cur.execute(
                                "INSERT INTO regulation_sections "
                                "(subpart_id, section_code, title, sort_order) "
                                "VALUES (?, ?, ?, ?)",
                                (subpart_id, None,
                                 normalize_title(section.title), section.sort_order),
                            )
                            section_id = cur.lastrowid
                            sections_count += 1

                            for entry in section.entries:
                                entry_ref = normalize_title(entry.entry_ref)
                                title = derive_display_title(entry.entry_ref, entry.title)
                                body_text = normalize_entry_text(entry.body_lines)
                                body_markdown = body_text

                                if not entry_ref or not title:
                                    empty_entries_skipped += 1
                                    continue

                                seen_entry_refs_global[entry_ref] = seen_entry_refs_global.get(entry_ref, 0) + 1
                                if seen_entry_refs_global[entry_ref] > 1:
                                    entry_ref = f"{entry_ref}#{seen_entry_refs_global[entry_ref]}"

                                dedupe_key = (
                                    document_id,
                                    subpart_id,
                                    section_id,
                                    entry_ref,
                                    title,
                                    entry.source_locator or '',
                                    body_text,
                                )
                                if dedupe_key in seen_entries:
                                    duplicate_entries_skipped += 1
                                    continue
                                seen_entries.add(dedupe_key)

                                cur.execute(
                                    "INSERT INTO regulation_entries "
                                    "(document_id, part_id, subpart_id, section_id, "
                                    " entry_ref, entry_type, title, "
                                    " body_markdown, body_text, source_locator, source_url, sort_order) "
                                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, ?)",
                                    (
                                        document_id,
                                        part_id,
                                        subpart_id,
                                        section_id,
                                        entry_ref,
                                        entry.entry_type,
                                        title,
                                        body_markdown,
                                        body_text,
                                        entry.source_locator,
                                        entry.sort_order,
                                    ),
                                )
                                entries_count += 1

                cur.execute(
                    "UPDATE source_documents "
                    "SET status = 'parsed', parsed_at = datetime('now'), "
                    "    updated_at = datetime('now') "
                    "WHERE id = ?",
                    (document_id,),
                )
            conn.commit()

        return PersistSummary(
            document_id=document_id,
            parts=parts_count,
            subparts=subparts_count,
            sections=sections_count,
            entries=entries_count,
            duplicate_entries_skipped=duplicate_entries_skipped,
            empty_entries_skipped=empty_entries_skipped,
        )
