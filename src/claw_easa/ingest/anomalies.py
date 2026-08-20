from __future__ import annotations

import logging
import re
from dataclasses import dataclass

log = logging.getLogger(__name__)

_MONTHS = {
    name.lower(): number
    for number, name in enumerate(
        (
            "January", "February", "March", "April", "May", "June",
            "July", "August", "September", "October", "November", "December",
        ),
        start=1,
    )
}

_REVISION_RE = re.compile(r'([A-Za-z]+)\s+(\d{4})')


@dataclass
class Anomaly:
    severity: str
    category: str
    message: str
    entry_ref: str | None = None


def revision_key(revision: str) -> tuple[int, int] | None:
    """Order a 'March 2026' style revision label, or ``None`` if unparseable."""
    match = _REVISION_RE.search(revision or "")
    if not match:
        return None
    month = _MONTHS.get(match.group(1).lower())
    if month is None:
        return None
    return int(match.group(2)), month


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

    anomalies.extend(_parse_anomalies(diagnostics))
    anomalies.extend(_coverage_anomalies(diagnostics))
    anomalies.extend(_provenance_anomalies(diagnostics))
    anomalies.extend(_freshness_anomalies(diagnostics))

    return anomalies


def _parse_anomalies(diagnostics: dict) -> list[Anomaly]:
    """A document full of text that yielded no entries was not understood."""
    if "entry_count" not in diagnostics:
        return []
    if diagnostics["entry_count"] > 0:
        return []

    paragraphs = diagnostics.get("paragraph_count", 0)
    parser_mode = diagnostics.get("parser_mode") or "unknown"
    slug = diagnostics.get("slug")
    subject = f"{slug}: " if slug else ""
    return [Anomaly(
        severity="error",
        category="parse",
        message=(
            f"{subject}no entries extracted from {paragraphs} paragraphs "
            f"(parser mode '{parser_mode}') — the document structure was "
            f"not recognised"
        ),
    )]


def _coverage_anomalies(diagnostics: dict) -> list[Anomaly]:
    """Sources the corpus is expected to hold but does not."""
    missing = diagnostics.get("missing_sources")
    if not missing:
        return []
    return [
        Anomaly(
            severity="error",
            category="coverage",
            message=f"source '{slug}' is missing from the corpus",
        )
        for slug in missing
    ]


def _provenance_anomalies(diagnostics: dict) -> list[Anomaly]:
    """Parts no declared regulation states.

    An error, not a warning: the part parsed cleanly, so nothing else reports
    it, and an answer citing it cannot name the regulation that states the
    requirement.  Attributing it by proximity would be worse than saying
    nothing, so it is surfaced here for a human to map.
    """
    unattributed = diagnostics.get("unattributed_parts")
    if not unattributed:
        return []

    slug = diagnostics.get("slug")
    subject = f"{slug}: " if slug else ""
    return [
        Anomaly(
            severity="error",
            category="provenance",
            message=(
                f"{subject}part '{part_code}' is stated by none of the "
                f"regulations declared for the source — it is left "
                f"unattributed and must be mapped before it is cited"
            ),
        )
        for part_code in unattributed
    ]


def _freshness_anomalies(diagnostics: dict) -> list[Anomaly]:
    """A local copy that EASA has since superseded."""
    local = diagnostics.get("local_revision")
    catalog = diagnostics.get("catalog_revision")
    if not local or not catalog or local == catalog:
        return []

    slug = diagnostics.get("slug")
    subject = f"{slug}: " if slug else ""
    local_key = revision_key(local)
    catalog_key = revision_key(catalog)

    if local_key and catalog_key and local_key > catalog_key:
        return [Anomaly(
            severity="info",
            category="freshness",
            message=(
                f"{subject}local revision '{local}' is ahead of the published "
                f"revision '{catalog}'"
            ),
        )]

    return [Anomaly(
        severity="warning",
        category="freshness",
        message=(
            f"{subject}local revision '{local}' is superseded by the "
            f"published revision '{catalog}'"
        ),
    )]
