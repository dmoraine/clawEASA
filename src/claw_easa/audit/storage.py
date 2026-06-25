from __future__ import annotations

import json
from typing import Any

from claw_easa.audit.schema import AuditSchemaError, validate_report
from claw_easa.db import Database
from claw_easa.db.migrations import MigrationRunner


def _ensure_schema(db: Database) -> None:
    MigrationRunner(db).init_schema()


def _json_text(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def _revision_fingerprint(report: dict[str, Any], finding: dict[str, Any]) -> str:
    payload = {
        "finding_id": finding["finding_id"],
        "manual_name": finding["manual_name"],
        "manual_section_paragraph": finding["manual_section_paragraph"],
        "manual_version_date": finding["manual_version_date"],
        "entity_scope": finding["entity_scope"],
        "applicable_easa_references": finding["applicable_easa_references"],
        "source_hierarchy_notes": finding["source_hierarchy_notes"],
        "manual_excerpt": finding["manual_excerpt"],
        "easa_excerpts": finding["easa_excerpts"],
        "assessment": finding["assessment"],
        "compliance_score": finding["compliance_score"],
        "severity": finding["severity"],
        "confidence": finding["confidence"],
        "gap_types": finding["gap_types"],
        "recommendation": finding["recommendation"],
        "review_status": finding["review_status"],
        "report_manual_version_date": report["manual_version_date"],
    }
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _store_revision(
    cur: Any,
    report_db_id: int,
    report: dict[str, Any],
    finding: dict[str, Any],
    revision_number: int,
    fingerprint: str,
) -> int:
    cur.execute(
        "INSERT INTO audit_finding_revisions ("
        " finding_id, report_db_id, revision_number, revision_fingerprint,"
        " manual_name, manual_section_paragraph, manual_version_date, entity_scope,"
        " applicable_easa_references_json, source_hierarchy_notes_json,"
        " manual_excerpt, easa_excerpts_json, assessment, compliance_score, severity,"
        " confidence, gap_types_json, recommendation, review_status"
        ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            finding["finding_id"],
            report_db_id,
            revision_number,
            fingerprint,
            finding["manual_name"],
            finding["manual_section_paragraph"],
            finding["manual_version_date"],
            finding["entity_scope"],
            _json_text(finding["applicable_easa_references"]),
            _json_text(finding["source_hierarchy_notes"]),
            finding["manual_excerpt"],
            _json_text(finding["easa_excerpts"]),
            finding["assessment"],
            finding["compliance_score"],
            finding["severity"],
            finding["confidence"],
            _json_text(finding["gap_types"]),
            finding["recommendation"],
            finding["review_status"],
        ),
    )
    revision_db_id = cur.lastrowid

    cur.execute(
        "INSERT INTO audit_finding_evidence ("
        " revision_db_id, evidence_index, evidence_kind, evidence_text, reference_text"
        ") VALUES (?, ?, ?, ?, ?)",
        (
            revision_db_id,
            0,
            "manual",
            finding["manual_excerpt"],
            finding["manual_section_paragraph"],
        ),
    )

    for index, excerpt in enumerate(finding["easa_excerpts"]):
        cur.execute(
            "INSERT INTO audit_finding_evidence ("
            " revision_db_id, evidence_index, evidence_kind, evidence_text, reference_text"
            ") VALUES (?, ?, ?, ?, ?)",
            (
                revision_db_id,
                index,
                "easa",
                excerpt,
                "; ".join(finding["applicable_easa_references"]),
            ),
        )

    return revision_db_id


def _decode_json_list(row: dict[str, Any], key: str) -> list[str]:
    try:
        raw = row[key]
    except KeyError:
        return []
    if raw is None or raw == "":
        return []
    value = json.loads(raw)
    if not isinstance(value, list):
        return []
    return [str(item) for item in value]


def _decorate_revision(row: dict[str, Any]) -> dict[str, Any]:
    revision = dict(row)
    revision["applicable_easa_references"] = _decode_json_list(row, "applicable_easa_references_json")
    revision["source_hierarchy_notes"] = _decode_json_list(row, "source_hierarchy_notes_json")
    revision["easa_excerpts"] = _decode_json_list(row, "easa_excerpts_json")
    revision["gap_types"] = _decode_json_list(row, "gap_types_json")
    revision["revision_fingerprint"] = row.get("revision_fingerprint")
    return revision


def _fetch_latest_revision_row(db: Database, finding_id: str) -> dict[str, Any] | None:
    with db.connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT * FROM audit_finding_revisions "
                "WHERE finding_id = ? ORDER BY revision_number DESC LIMIT 1",
                (finding_id,),
            )
            return cur.fetchone()


def _fetch_revision_history(db: Database, finding_id: str) -> list[dict[str, Any]]:
    with db.connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT * FROM audit_finding_revisions "
                "WHERE finding_id = ? ORDER BY revision_number ASC",
                (finding_id,),
            )
            return cur.fetchall()


def import_report(db: Database, report: dict[str, Any], source_path: str | None = None) -> dict[str, Any]:
    canonical = validate_report(report)
    _ensure_schema(db)

    with db.connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id, report_json FROM audit_reports WHERE report_id = ?",
                (canonical["report_id"],),
            )
            existing = cur.fetchone()
            if existing:
                if existing["report_json"] != json.dumps(canonical, ensure_ascii=False):
                    raise AuditSchemaError(
                        f"Audit report already exists with different content: {canonical['report_id']}"
                    )
                return canonical

            cur.execute(
                "INSERT INTO audit_reports ("
                " report_id, report_name, schema_version, manual_name, manual_version_date,"
                " entity_scope, created_at, source_path, report_json"
                ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    canonical["report_id"],
                    canonical["report_name"],
                    canonical["schema_version"],
                    canonical["manual_name"],
                    canonical["manual_version_date"],
                    canonical["entity_scope"],
                    canonical["created_at"],
                    source_path,
                    json.dumps(canonical, ensure_ascii=False),
                ),
            )
            report_db_id = cur.lastrowid

            for index, finding in enumerate(canonical["findings"]):
                cur.execute(
                    "INSERT INTO audit_findings ("
                    " report_db_id, finding_index, finding_id, finding_json"
                    ") VALUES (?, ?, ?, ?)",
                    (
                        report_db_id,
                        index,
                        finding["finding_id"],
                        json.dumps(finding, ensure_ascii=False),
                    ),
                )

                fingerprint = _revision_fingerprint(canonical, finding)
                latest = _fetch_latest_revision_row(db, finding["finding_id"])
                if latest and latest["revision_fingerprint"] == fingerprint:
                    continue
                revision_number = 1 if latest is None else int(latest["revision_number"]) + 1
                _store_revision(cur, report_db_id, canonical, finding, revision_number, fingerprint)
        conn.commit()

    return canonical


def fetch_report(db: Database, report_id: str) -> dict[str, Any]:
    _ensure_schema(db)
    with db.connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT report_json FROM audit_reports WHERE report_id = ?",
                (report_id,),
            )
            row = cur.fetchone()
            if not row:
                raise AuditSchemaError(f"Audit report not found: {report_id}")
            return validate_report(json.loads(row["report_json"]))


def list_reports(db: Database) -> list[dict[str, Any]]:
    _ensure_schema(db)
    with db.connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT report_id, report_name, schema_version, manual_name, manual_version_date,"
                " entity_scope, created_at, source_path,"
                " (SELECT COUNT(*) FROM audit_findings af WHERE af.report_db_id = ar.id) AS finding_count"
                " FROM audit_reports ar ORDER BY created_at DESC, report_id DESC"
            )
            return cur.fetchall()


def fetch_finding(db: Database, finding_id: str) -> dict[str, Any]:
    _ensure_schema(db)
    history_rows = _fetch_revision_history(db, finding_id)
    if not history_rows:
        raise AuditSchemaError(f"Audit finding not found: {finding_id}")

    revisions: list[dict[str, Any]] = []
    with db.connection() as conn:
        with conn.cursor() as cur:
            for row in history_rows:
                revision = _decorate_revision(row)
                cur.execute(
                    "SELECT evidence_index, evidence_kind, evidence_text, reference_text "
                    "FROM audit_finding_evidence WHERE revision_db_id = ? "
                    "ORDER BY CASE evidence_kind WHEN 'manual' THEN 0 ELSE 1 END, evidence_index",
                    (row["id"],),
                )
                revision["evidence"] = cur.fetchall()
                revisions.append(revision)

    latest = revisions[-1]
    return {
        "finding_id": finding_id,
        "latest_revision_number": latest["revision_number"],
        "latest_revision": latest,
        "revisions": revisions,
    }


def list_finding_revisions(db: Database, finding_id: str) -> list[dict[str, Any]]:
    return fetch_finding(db, finding_id)["revisions"]


def fetch_report_finding_meta(db: Database, report_id: str) -> dict[str, dict[str, Any]]:
    report = fetch_report(db, report_id)
    meta: dict[str, dict[str, Any]] = {}
    with db.connection() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT id FROM audit_reports WHERE report_id = ?", (report_id,))
            report_row = cur.fetchone()
            report_db_id = report_row["id"] if report_row else None

            for finding in report["findings"]:
                cur.execute(
                    "SELECT * FROM audit_finding_revisions "
                    "WHERE finding_id = ? AND report_db_id = ? LIMIT 1",
                    (finding["finding_id"], report_db_id),
                )
                row = cur.fetchone()
                if row is None:
                    row = _fetch_latest_revision_row(db, finding["finding_id"])
                if row is None:
                    meta[finding["finding_id"]] = {"revision_number": 1, "revision_count": 1}
                    continue

                cur.execute(
                    "SELECT COUNT(*) AS cnt FROM audit_finding_revisions WHERE finding_id = ?",
                    (finding["finding_id"],),
                )
                count_row = cur.fetchone() or {"cnt": 1}
                meta[finding["finding_id"]] = {
                    "revision_number": row["revision_number"],
                    "revision_count": int(count_row["cnt"]),
                    "review_status": row["review_status"],
                }
    return meta
