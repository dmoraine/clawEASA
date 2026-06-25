from __future__ import annotations

from pathlib import Path
from typing import Any

from claw_easa.audit.export import export_report
from claw_easa.audit.schema import load_report
from claw_easa.audit.storage import (
    fetch_finding,
    fetch_report,
    fetch_report_finding_meta,
    import_report,
    list_finding_revisions,
)
from claw_easa.db import Database


def validate_report_file(path: str | Path) -> dict[str, Any]:
    return load_report(path)


def import_report_file(path: str | Path) -> dict[str, Any]:
    report = load_report(path)
    db = Database()
    db.open()
    try:
        return import_report(db, report, source_path=str(path))
    finally:
        db.close()


def export_report_by_id(report_id: str, output_path: str | Path, format: str) -> Path:
    db = Database()
    db.open()
    try:
        report = fetch_report(db, report_id)
        meta = fetch_report_finding_meta(db, report_id)
        return export_report(report, output_path, format, finding_meta=meta)
    finally:
        db.close()


def fetch_finding_by_id(finding_id: str) -> dict[str, Any]:
    db = Database()
    db.open()
    try:
        return fetch_finding(db, finding_id)
    finally:
        db.close()


def list_finding_history(finding_id: str) -> list[dict[str, Any]]:
    db = Database()
    db.open()
    try:
        return list_finding_revisions(db, finding_id)
    finally:
        db.close()
