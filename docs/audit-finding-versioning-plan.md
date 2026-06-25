# Audit finding versioning and discussion layer — planning document

## Purpose

This document defines the next step of the audit workflow: adding a SQLite-backed layer for finding traceability, revision tracking, and re-discussion of findings by ID.

The goal is not to replace the current canonical JSON report format. The goal is to complement it with a structured persistence layer that makes findings:
- easy to retrieve by stable ID,
- version-aware,
- comparable across revisions,
- and auditable over time.

## Context

The current audit workflow already has:
- a canonical JSON report as the source of truth,
- SQLite persistence for stored reports,
- CSV/XLSX export for human review,
- a fixed finding schema.

What is still missing is a proper finding-centric model that can answer questions such as:
- “Show me finding AUD-001 with all stored evidence.”
- “What changed between revision 1 and revision 3?”
- “Which manual version was this finding based on?”
- “Has this finding been superseded or reopened?”

## Problem statement

At the moment, a report can be stored and exported, but the workflow is still report-centric rather than finding-centric.

That creates four limitations:
1. Revision history is not explicit.
2. Manual version linkage is too coarse.
3. Re-discussion of a finding is not modeled as a first-class workflow step.
4. A finding ID is visible in the report, but it is not yet backed by a dedicated traceability structure.

## Design principles

### 1) Keep the JSON report as the source of truth
The canonical JSON report remains the immutable artifact produced by the Analyst.

SQLite should store:
- the report snapshot,
- parsed finding records,
- and the lifecycle history.

### 2) Separate object identity from revision history
A finding should have a stable identity.
Each change should create a new revision instead of overwriting the previous state.

### 3) Preserve traceability
Every finding revision should remain linked to:
- the report it came from,
- the manual version/date,
- the applicable EASA references,
- and the evidence excerpt(s).

### 4) Make review and re-review explicit
If a finding is challenged, reopened, or refined, that decision should be stored as a new revision or review event.

### 5) Avoid over-modeling in the first iteration
Start with the minimum set of tables and workflows that solve the traceability problem cleanly.

## Functional goals

The database should support these use cases:

### A. Retrieve a finding by ID
Input:
- finding ID

Output:
- the latest revision,
- all previous revisions,
- the parent report metadata,
- the manual version/date,
- the EASA references,
- the evidence,
- the current lifecycle status.

### B. Compare revisions
The user should be able to see what changed between revisions, for example:
- score changed from 3 to 4,
- wording was clarified,
- gap type was narrowed,
- review status moved from Proposed to Approved.

### C. Keep superseded history
Older revisions must remain available.
Nothing should be destroyed just because a later revision exists.

### D. Track discussion
A finding should be discussable by ID, with comments or notes linked to the specific finding or revision.

### E. Reflect manual versioning
Each finding must be associated with the manual version/date it was assessed against.
If the manual changes, the finding should be re-evaluated or marked as superseded.

## Proposed data model

This is a conceptual model, not an implementation requirement.

### 1. audit_reports
Stores the report-level snapshot.
Suggested contents:
- report_id
- report_name
- schema_version
- manual_name
- manual_version_date
- entity_scope
- created_at
- source_path
- report_json

### 2. audit_findings
Stores the stable finding identity.
Suggested contents:
- finding_id
- report_db_id
- manual_section_paragraph
- lifecycle_status
- current_revision_id
- created_at

### 3. audit_finding_revisions
Stores the history of changes for a finding.
Suggested contents:
- revision_id
- finding_id
- revision_number
- manual_version_date
- assessment
- compliance_score
- severity
- confidence
- gap_types
- recommendation
- review_status
- created_at
- changed_by
- change_reason
- revision_json

### 4. audit_finding_evidence
Stores evidence linked to a revision.
Suggested contents:
- evidence_id
- revision_id
- manual_excerpt
- easa_excerpt
- applicable_easa_references
- source_hierarchy_notes

### 5. audit_finding_discussions
Stores comments and discussion events.
Suggested contents:
- discussion_id
- finding_id
- revision_id (optional)
- author
- message
- created_at
- discussion_type

### 6. audit_manual_versions
Optional but recommended once versioning becomes central.
Suggested contents:
- manual_version_id
- manual_name
- manual_version_date
- source_hash
- source_path
- imported_at

## ID strategy

The finding ID must be:
- unique,
- stable over time,
- human-readable,
- and independent from row order in exports.

Recommended properties:
- do not reuse spreadsheet row numbers as IDs,
- do not derive the ID from sort order,
- keep the ID visible in XLSX exports,
- keep the ID stable across revisions.

A revision may have its own revision number or revision ID, but the parent finding ID remains the anchor.

## Lifecycle model

Suggested statuses:
- Proposed
- Under review
- Approved
- Needs revision
- Superseded
- Closed
- Escalated

Suggested rules:
- Proposed: first-pass Analyst result.
- Under review: quality review in progress.
- Approved: accepted without substantive change.
- Needs revision: finding must be updated before acceptance.
- Superseded: replaced by a newer revision or manual version.
- Closed: no further action required.
- Escalated: requires human compliance decision.

## Versioning rules

### When a finding changes
Create a new revision if any of the following changes:
- score,
- severity,
- confidence,
- assessment text,
- recommendation,
- gap types,
- evidence set,
- review status.

### When the manual changes
If the manual version/date changes, the linked findings should be reviewed again.
The old finding revision should remain preserved and clearly linked to the earlier manual version.

### When a finding is only reformatted
If the substance does not change, it may still be useful to record a revision if the visible wording changes materially for review purposes.

## Export implications

The XLSX export should visibly expose at least:
- finding ID,
- revision number or latest revision marker,
- review status,
- manual version/date,
- compliance score,
- severity,
- confidence.

The export should be readable for reviewers, but the database remains the traceability source.

## Recommended first implementation phase

### Phase 1 — foundation
Deliver:
- stable finding IDs in exported reports,
- a finding-centric lookup path in SQLite,
- a revision table,
- preservation of old revisions,
- report-to-finding linkage.

### Phase 2 — review loop
Deliver:
- finding re-discussion by ID,
- comments/notes linked to a finding,
- revision change reasons,
- status transitions.

### Phase 3 — manual versioning
Deliver:
- explicit manual version records,
- automatic supersession detection when the manual changes,
- revision history by manual version.

### Phase 4 — review tooling
Deliver:
- diff views between revisions,
- filters by status/severity/manual section,
- report regeneration from stored data.

## Acceptance criteria

The step is complete when the workflow can do all of the following:
1. store a report and its findings in SQLite,
2. retrieve a finding by ID,
3. show all revisions for that finding,
4. preserve previous revisions after updates,
5. link findings to a manual version/date,
6. export the ID clearly into XLSX,
7. support discussion notes without overwriting the audit trail.

## Open questions

These should be resolved before implementation:
1. Should revision history be stored as normalized rows only, or also as JSON snapshots?
2. Should comments be free-text only, or structured with author/role/status?
3. Should a new manual version automatically create a new report, or update an existing report family?
4. Should finding IDs be generated at report creation time or during import?
5. Should supersession happen automatically, or remain a manual reviewer action?

## V1 implementation contract

The V1 implementation contract is documented in:
- `docs/audit-finding-versioning-contract-v1.md`

That contract freezes the minimum schema, lifecycle statuses, ID format, and revision rules before coding.
