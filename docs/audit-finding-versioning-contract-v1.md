# Audit finding versioning contract — V1

## Purpose

This document freezes the minimum design for the first implementation of the audit finding versioning layer.
It is the implementation contract for the next development step.

## Scope

V1 covers:
- stable finding identity,
- report persistence,
- finding revisions,
- evidence storage,
- XLSX visibility of finding IDs,
- re-discussion readiness by ID.

V1 does not cover:
- full discussion threads,
- collaboration workflows,
- advanced diff UI,
- multi-user permissions,
- PostgreSQL migration.

## Agreed V1 decisions

### 1) Tables
V1 will include these tables:
- reports
- findings
- revisions
- evidence

### 2) Discussions
Discussions are deferred to V2.
They are out of scope for the first implementation.

### 3) Finding ID
Finding IDs will be stable and opaque, using the format:
- `AUD-YYYYMMDD-XXXX`

Rules:
- the ID must not depend on spreadsheet row order,
- the ID must remain stable across exports,
- the ID must remain stable across revisions.

### 4) Finding identity and revisions
A finding is a stable identity.
A finding may have multiple revisions.

### 5) Revision trigger
Create a new revision only when the substance changes.
Examples of substantive changes:
- score
- assessment
- gap types
- recommendation
- severity when analytically justified
- confidence when analytically justified

### 6) Wording-only changes
Pure wording changes do not automatically require a new revision.
If the wording change alters meaning or decision, it does require a new revision.

### 7) Manual version changes
When the manual version/date changes:
- preserve the old revision,
- re-evaluate the finding,
- mark the old result as superseded or pending review as appropriate.

### 8) Lifecycle statuses
V1 will use these statuses:
- Proposed
- Under review
- Approved
- Needs revision
- Superseded
- Escalated

### 9) History preservation
The system must never overwrite historical revisions.
Old revisions remain accessible.

### 10) XLSX export
The XLSX export must visibly include at least:
- Finding ID
- revision number or latest revision marker
- lifecycle status
- manual version/date
- compliance score
- severity
- confidence

## Minimal acceptance criteria

The V1 implementation is acceptable when it can:
1. store a report and its findings,
2. assign and persist stable Finding IDs,
3. store multiple revisions for the same finding,
4. retrieve a finding by ID,
5. keep old revisions after changes,
6. link findings to a manual version/date,
7. expose the Finding ID in the XLSX export.

## Implementation note

This contract deliberately keeps the first version small.
If a change does not improve traceability, it should be deferred until V2.
