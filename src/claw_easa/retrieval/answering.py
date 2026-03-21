from __future__ import annotations

from claw_easa.retrieval.formatting import compact_snippet


def _format_source_line(row: dict) -> str:
    return f"- {row['entry_ref']} ({row['entry_type']}) — {row['title']} [{row['slug']}]"


def _lead_from_rows(rows: list[dict], rewritten_query: str, broad: bool = False) -> str:
    if not rows:
        return f"I couldn't find good matches for: {rewritten_query}"
    top = rows[0]
    if broad:
        return f"Here are the most likely core references for: {rewritten_query}"
    return (
        f"The strongest match for '{rewritten_query}' is "
        f"{top['entry_ref']} ({top['entry_type']}) — {top['title']}."
    )


def format_exact_answer(rows: list[dict], normalized_query: str, rewritten_query: str) -> str:
    lines = [f"Exact match for: {normalized_query}", "Core sources:"]
    for row in rows[:3]:
        lines.append(_format_source_line(row))
        snippet = compact_snippet(row.get('body_text', ''), max_lines=3, max_chars=260)
        if snippet:
            lines.append(f"  {snippet}")
    return '\n'.join(lines)


def format_refs_answer(rows: list[dict], rewritten_query: str) -> str:
    lines = [f"References for: {rewritten_query}", "Core sources:"]
    for row in rows[:8]:
        lines.append(_format_source_line(row))
    return '\n'.join(lines)


def format_snippets_answer(rows: list[dict], rewritten_query: str) -> str:
    lines = [f"Relevant extracts for: {rewritten_query}", "Core sources:"]
    for row in rows[:5]:
        lines.append(_format_source_line(row))
        snippet = compact_snippet(row.get('body_text', ''), max_lines=3, max_chars=280)
        if snippet:
            lines.append(f"  {snippet}")
    return '\n'.join(lines)


def format_survey_answer(shaped: dict, rewritten_query: str) -> str:
    core = shaped.get('core_refs', [])
    supporting = shaped.get('supporting_refs', [])
    lines = [
        _lead_from_rows(core or supporting, rewritten_query, broad=True),
        "This is a broad scan, not a guaranteed exhaustive list.",
        "Core sources:",
    ]
    for row in core:
        lines.append(_format_source_line(row))
        snippet = compact_snippet(
            row.get('body_text') or row.get('chunk_text', ''),
            max_lines=2, max_chars=220,
        )
        if snippet:
            lines.append(f"  {snippet}")
    if supporting:
        lines.append("Supporting sources:")
        for row in supporting:
            lines.append(_format_source_line(row))
    return '\n'.join(lines)


def format_answer_answer(rows: list[dict], rewritten_query: str) -> str:
    core_rows = [row for row in rows if row.get('entry_type') != 'FAQ']
    faq_rows = [row for row in rows if row.get('entry_type') == 'FAQ']

    lead_rows = list(core_rows[:3])
    promoted_faqs: list[dict] = []
    if faq_rows:
        top_faq = faq_rows[0]
        top_faq_score = float(top_faq.get('hybrid_score') or 0.0)
        top_core_score = float(core_rows[0].get('hybrid_score') or 0.0) if core_rows else 0.0
        if not core_rows or top_faq_score >= (top_core_score - 0.35):
            promoted_faqs.append(top_faq)

    if promoted_faqs:
        lead_rows = promoted_faqs + [
            row for row in lead_rows if row['entry_ref'] != promoted_faqs[0]['entry_ref']
        ]
        lines = [
            f"The most relevant interpretive source for '{rewritten_query}' is "
            f"{promoted_faqs[0]['entry_ref']} ({promoted_faqs[0]['entry_type']}) "
            f"— {promoted_faqs[0]['title']}.",
            'Core sources:',
        ]
    else:
        lead_rows = lead_rows or rows
        lines = [_lead_from_rows(lead_rows, rewritten_query), 'Core sources:']

    shown_refs: set[str] = set()
    for row in lead_rows[:3]:
        shown_refs.add(row['entry_ref'])
        lines.append(_format_source_line(row))
        snippet = compact_snippet(
            row.get('body_text') or row.get('chunk_text', ''),
            max_lines=2, max_chars=220,
        )
        if snippet:
            lines.append(f"  {snippet}")

    remaining_faqs = [row for row in faq_rows if row['entry_ref'] not in shown_refs]
    if remaining_faqs:
        lines.append('Related FAQs:')
        for row in remaining_faqs[:2]:
            lines.append(f"- {row['title']} [{row['slug']}]")
    return '\n'.join(lines)
