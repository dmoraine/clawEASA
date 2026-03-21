from __future__ import annotations

import logging
from dataclasses import dataclass

log = logging.getLogger(__name__)


@dataclass
class Anomaly:
    severity: str
    category: str
    message: str
    entry_ref: str | None = None


def detect_anomalies(diagnostics: dict) -> list[Anomaly]:
    anomalies: list[Anomaly] = []

    if diagnostics.get("empty_body_count", 0) > diagnostics.get("nonempty_body_count", 0):
        anomalies.append(Anomaly(
            severity="warning",
            category="content",
            message=(
                f"More empty entries ({diagnostics['empty_body_count']}) "
                f"than non-empty ({diagnostics['nonempty_body_count']})"
            ),
        ))

    if diagnostics.get("duplicate_ref_count", 0) > 0:
        anomalies.append(Anomaly(
            severity="info",
            category="duplicates",
            message=f"{diagnostics['duplicate_ref_count']} duplicate entry references found",
        ))

    if diagnostics.get("empty_section_count", 0) > 0:
        anomalies.append(Anomaly(
            severity="info",
            category="structure",
            message=f"{diagnostics['empty_section_count']} empty sections found",
        ))

    return anomalies
