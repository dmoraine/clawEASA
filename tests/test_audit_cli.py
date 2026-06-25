from __future__ import annotations

import json

from claw_easa.audit.storage import fetch_finding
from claw_easa.config import Settings, reset_settings
from claw_easa.db import Database
from claw_easa.cli import main as cli_main


def sample_report_v1() -> dict:
    return {
        "schema_version": "1.0",
        "report_id": "AUD-20260422-0002",
        "report_name": "OM-A pilot audit",
        "manual_name": "OM-A",
        "manual_version_date": "Rev. 12 / 2026-03-01",
        "entity_scope": "ASLB",
        "created_at": "2026-04-22T12:00:00Z",
        "findings": [
            {
                "finding_id": "AUD-20260422-0201",
                "manual_name": "OM-A",
                "manual_section_paragraph": "4.2.1",
                "manual_version_date": "Rev. 12 / 2026-03-01",
                "entity_scope": "ASLB",
                "applicable_easa_references": ["ORO.FTL.110(a)"],
                "source_hierarchy_notes": ["IR is binding"],
                "manual_excerpt": "The company publishes duty rosters in advance.",
                "easa_excerpts": [
                    "An operator shall publish duty rosters sufficiently in advance to provide the opportunity for crew members to plan adequate rest."
                ],
                "assessment": "The manual covers the core obligation.",
                "compliance_score": 4,
                "severity": "Low",
                "confidence": "High",
                "gap_types": ["wording / editorial weakness"],
                "recommendation": "Specify the minimum publication lead time.",
                "review_status": "Proposed",
            }
        ],
    }


def sample_report_v2() -> dict:
    return {
        "schema_version": "1.0",
        "report_id": "AUD-20260423-0002",
        "report_name": "OM-A pilot audit — updated",
        "manual_name": "OM-A",
        "manual_version_date": "Rev. 13 / 2026-04-15",
        "entity_scope": "ASLB",
        "created_at": "2026-04-23T12:00:00Z",
        "findings": [
            {
                "finding_id": "AUD-20260422-0201",
                "manual_name": "OM-A",
                "manual_section_paragraph": "4.2.1",
                "manual_version_date": "Rev. 13 / 2026-04-15",
                "entity_scope": "ASLB",
                "applicable_easa_references": ["ORO.FTL.110(a)"],
                "source_hierarchy_notes": ["IR is binding"],
                "manual_excerpt": "The company publishes duty rosters at least 14 days in advance.",
                "easa_excerpts": [
                    "An operator shall publish duty rosters sufficiently in advance to provide the opportunity for crew members to plan adequate rest."
                ],
                "assessment": "The manual now defines an explicit lead time.",
                "compliance_score": 5,
                "severity": "Low",
                "confidence": "High",
                "gap_types": [],
                "recommendation": "Keep the lead time controlled in the next manual revision.",
                "review_status": "Approved",
            }
        ],
    }


def test_audit_validate_import_export_and_finding_commands(tmp_path, monkeypatch):
    reset_settings()
    settings = Settings(data_dir=str(tmp_path), db_file="audit.db")
    monkeypatch.setattr("claw_easa.config._settings", settings)

    input_path = tmp_path / "input-v1.json"
    input_path.write_text(json.dumps(sample_report_v1()), encoding="utf-8")
    input_path_2 = tmp_path / "input-v2.json"
    input_path_2.write_text(json.dumps(sample_report_v2()), encoding="utf-8")

    from click.testing import CliRunner

    runner = CliRunner()
    result = runner.invoke(cli_main, ["audit", "validate", str(input_path)])
    assert result.exit_code == 0
    assert "OK" in result.output

    result = runner.invoke(cli_main, ["audit", "import", str(input_path)])
    assert result.exit_code == 0
    assert "Imported" in result.output

    result = runner.invoke(cli_main, ["audit", "import", str(input_path_2)])
    assert result.exit_code == 0
    assert "Imported" in result.output

    db = Database(settings=settings)
    db.open()
    try:
        fetched = fetch_finding(db, "AUD-20260422-0201")
        assert fetched["latest_revision_number"] == 2
    finally:
        db.close()

    result = runner.invoke(cli_main, ["audit", "finding", "get", "AUD-20260422-0201"])
    assert result.exit_code == 0
    assert "Latest revision: 2" in result.output
    assert "Finding ID: AUD-20260422-0201" in result.output

    result = runner.invoke(cli_main, ["audit", "finding", "history", "AUD-20260422-0201"])
    assert result.exit_code == 0
    assert "Rev 1" in result.output
    assert "Rev 2" in result.output

    xlsx_path = tmp_path / "export.xlsx"
    result = runner.invoke(
        cli_main,
        ["audit", "export", "--report-id", "AUD-20260423-0002", "--format", "xlsx", "--output", str(xlsx_path)],
    )
    assert result.exit_code == 0
    assert xlsx_path.exists()
