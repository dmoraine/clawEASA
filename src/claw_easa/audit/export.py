from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Any

from openpyxl import Workbook
from openpyxl.styles import Alignment, Font

from claw_easa.audit.schema import validate_report

EXPORT_COLUMNS = [
    "finding_id",
    "revision_number",
    "revision_count",
    "manual_name",
    "manual_section_paragraph",
    "manual_version_date",
    "entity_scope",
    "applicable_easa_references",
    "source_hierarchy_notes",
    "manual_excerpt",
    "easa_excerpts",
    "assessment",
    "compliance_score",
    "severity",
    "confidence",
    "gap_types",
    "recommendation",
    "review_status",
]

SUMMARY_COLUMNS = [
    "report_id",
    "report_name",
    "schema_version",
    "manual_name",
    "manual_version_date",
    "entity_scope",
    "created_at",
    "finding_count",
]


def _join_list(value: Any) -> str:
    if not value:
        return ""
    if isinstance(value, list):
        return " | ".join(str(item) for item in value)
    return str(value)


def _finding_rows(
    report: dict[str, Any],
    finding_meta: dict[str, dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    rows = []
    finding_meta = finding_meta or {}
    for finding in report["findings"]:
        meta = finding_meta.get(finding["finding_id"], {})
        rows.append(
            {
                "finding_id": finding["finding_id"],
                "revision_number": meta.get("revision_number", 1),
                "revision_count": meta.get("revision_count", 1),
                "manual_name": finding["manual_name"],
                "manual_section_paragraph": finding["manual_section_paragraph"],
                "manual_version_date": finding["manual_version_date"],
                "entity_scope": finding["entity_scope"],
                "applicable_easa_references": _join_list(finding["applicable_easa_references"]),
                "source_hierarchy_notes": _join_list(finding["source_hierarchy_notes"]),
                "manual_excerpt": finding["manual_excerpt"],
                "easa_excerpts": _join_list(finding["easa_excerpts"]),
                "assessment": finding["assessment"],
                "compliance_score": finding["compliance_score"],
                "severity": finding["severity"],
                "confidence": finding["confidence"],
                "gap_types": _join_list(finding["gap_types"]),
                "recommendation": finding["recommendation"],
                "review_status": meta.get("review_status", finding["review_status"]),
            }
        )
    return rows


def export_report_json(report: dict[str, Any], output_path: str | Path) -> Path:
    canonical = validate_report(report)
    out_path = Path(output_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(canonical, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return out_path


def export_report_csv(
    report: dict[str, Any],
    output_path: str | Path,
    finding_meta: dict[str, dict[str, Any]] | None = None,
) -> Path:
    canonical = validate_report(report)
    out_path = Path(output_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    rows = _finding_rows(canonical, finding_meta=finding_meta)
    with out_path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=EXPORT_COLUMNS)
        writer.writeheader()
        writer.writerows(rows)
    return out_path


def export_report_xlsx(
    report: dict[str, Any],
    output_path: str | Path,
    finding_meta: dict[str, dict[str, Any]] | None = None,
) -> Path:
    canonical = validate_report(report)
    out_path = Path(output_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    wb = Workbook()
    ws_summary = wb.active
    ws_summary.title = "Summary"

    summary = {
        "report_id": canonical["report_id"],
        "report_name": canonical["report_name"],
        "schema_version": canonical["schema_version"],
        "manual_name": canonical["manual_name"],
        "manual_version_date": canonical["manual_version_date"],
        "entity_scope": canonical["entity_scope"],
        "created_at": canonical["created_at"],
        "finding_count": len(canonical["findings"]),
    }

    ws_summary.append(["Field", "Value"])
    for cell in ws_summary[1]:
        cell.font = Font(bold=True)
    for key in SUMMARY_COLUMNS:
        ws_summary.append([key, summary[key]])

    ws_findings = wb.create_sheet("Findings")
    ws_findings.append(EXPORT_COLUMNS)
    for cell in ws_findings[1]:
        cell.font = Font(bold=True)
        cell.alignment = Alignment(vertical="top", wrap_text=True)

    for row in _finding_rows(canonical, finding_meta=finding_meta):
        ws_findings.append([row[col] for col in EXPORT_COLUMNS])

    for ws in (ws_summary, ws_findings):
        for column_cells in ws.columns:
            max_len = 0
            column_letter = column_cells[0].column_letter
            for cell in column_cells:
                value = "" if cell.value is None else str(cell.value)
                max_len = max(max_len, max((len(line) for line in value.splitlines()), default=0))
                cell.alignment = Alignment(vertical="top", wrap_text=True)
            ws.column_dimensions[column_letter].width = min(max_len + 2, 80)
        ws.freeze_panes = "A2"

    wb.save(out_path)
    return out_path


def export_report(
    report: dict[str, Any],
    output_path: str | Path,
    format: str,
    finding_meta: dict[str, dict[str, Any]] | None = None,
) -> Path:
    fmt = format.lower()
    if fmt == "json":
        return export_report_json(report, output_path)
    if fmt == "csv":
        return export_report_csv(report, output_path, finding_meta=finding_meta)
    if fmt == "xlsx":
        return export_report_xlsx(report, output_path, finding_meta=finding_meta)
    raise ValueError(f"Unsupported export format: {format}")
