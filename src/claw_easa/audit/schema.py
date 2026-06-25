from __future__ import annotations

import json
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

REQUIRED_REPORT_FIELDS = (
    "schema_version",
    "report_id",
    "report_name",
    "manual_name",
    "manual_version_date",
    "entity_scope",
    "findings",
)

REQUIRED_FINDING_FIELDS = (
    "finding_id",
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
)

LIST_FINDING_FIELDS = {
    "applicable_easa_references",
    "source_hierarchy_notes",
    "easa_excerpts",
    "gap_types",
}

SCALAR_STRING_REPORT_FIELDS = (
    "schema_version",
    "report_id",
    "report_name",
    "manual_name",
    "manual_version_date",
    "entity_scope",
)


class AuditSchemaError(ValueError):
    pass


def _now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _default_report_id() -> str:
    return f"AUD-{datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S')}"


def _as_str(value: Any, field: str) -> str:
    if isinstance(value, str):
        stripped = value.strip()
        if not stripped:
            raise AuditSchemaError(f"{field} must not be empty")
        return stripped
    raise AuditSchemaError(f"{field} must be a string")


def _as_int(value: Any, field: str) -> int:
    if isinstance(value, bool):
        raise AuditSchemaError(f"{field} must be an integer from 0 to 5")
    if isinstance(value, int):
        return value
    raise AuditSchemaError(f"{field} must be an integer from 0 to 5")


def _as_str_list(value: Any, field: str) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        items = [part.strip() for part in value.split("\n") if part.strip()]
        return items
    if not isinstance(value, list):
        raise AuditSchemaError(f"{field} must be a list of strings")
    out: list[str] = []
    for item in value:
        if not isinstance(item, str):
            raise AuditSchemaError(f"{field} must contain only strings")
        stripped = item.strip()
        if stripped:
            out.append(stripped)
    return out


def canonicalize_finding(raw: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise AuditSchemaError("Each finding must be an object")

    missing = [field for field in REQUIRED_FINDING_FIELDS if field not in raw]
    if missing:
        raise AuditSchemaError(f"Finding is missing required field(s): {', '.join(missing)}")

    finding: dict[str, Any] = {}
    for field in REQUIRED_FINDING_FIELDS:
        if field in LIST_FINDING_FIELDS:
            finding[field] = _as_str_list(raw[field], field)
        elif field == "compliance_score":
            score = _as_int(raw[field], field)
            if score < 0 or score > 5:
                raise AuditSchemaError("compliance_score must be between 0 and 5")
            finding[field] = score
        else:
            finding[field] = _as_str(raw[field], field)

    return finding


def validate_finding(raw: dict[str, Any]) -> dict[str, Any]:
    return canonicalize_finding(raw)


def canonicalize_report(raw: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise AuditSchemaError("Report must be an object")

    data = deepcopy(raw)
    findings_raw = data.get("findings")
    if findings_raw is None:
        raise AuditSchemaError("Report is missing required field(s): findings")
    if not isinstance(findings_raw, list):
        raise AuditSchemaError("findings must be a list")

    report: dict[str, Any] = {}
    report["schema_version"] = _as_str(data.get("schema_version", "1.0"), "schema_version")
    report["report_id"] = _as_str(data.get("report_id") or _default_report_id(), "report_id")
    report["report_name"] = _as_str(data.get("report_name"), "report_name")
    report["manual_name"] = _as_str(data.get("manual_name"), "manual_name")
    report["manual_version_date"] = _as_str(data.get("manual_version_date"), "manual_version_date")
    report["entity_scope"] = _as_str(data.get("entity_scope"), "entity_scope")
    report["created_at"] = _as_str(data.get("created_at") or _now_iso(), "created_at")
    report["findings"] = [canonicalize_finding(item) for item in findings_raw]

    return report


def validate_report(raw: dict[str, Any]) -> dict[str, Any]:
    return canonicalize_report(raw)


def load_report(path: str | Path) -> dict[str, Any]:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    return validate_report(data)


def dump_report(report: dict[str, Any], path: str | Path) -> Path:
    canonical = validate_report(report)
    out_path = Path(path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(canonical, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return out_path
