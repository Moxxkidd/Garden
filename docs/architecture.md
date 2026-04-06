# Architecture

## Overview

Garden is a CLI-first, minimal-Web-UI-second workflow system for authorized authenticated application surface verification.

The product shape is intentionally narrow:

- CLI is the operational control plane
- Web UI is a small review and triage surface
- business logic lives in services
- adapter/plugin seams exist for login, checks, evidence capture, and exporters
- outputs are structured and linked instead of being stored as raw traffic piles

## Why CLI-First

Garden is built for AppSec, security engineering, and internal testing teams. Those users need:

- shell-friendly commands
- repeatable workflows
- CI or scheduled execution potential
- composable output
- low operational overhead

That is why the core workflow is exposed first through `gardenctl`, and why every major phase was implemented through service layers that both CLI and Web routes reuse.

## Why the Web UI Is Minimal

The Web UI exists to support:

- browsing inventory and findings
- reviewing evidence
- triaging lifecycle state
- driving a short live demo

It is intentionally not a SPA and not a heavy management console. This keeps:

- startup simple
- mental overhead low
- architecture focused on workflow output rather than frontend complexity

## Major Components

### `app/core/`

- settings loading
- logging
- exception helpers
- target guardrails

### `app/db/`

- SQLAlchemy bootstrap
- session management
- base model setup

### `app/models/`

Persistent workflow entities:

- `Target`
- `CredentialProfile`
- `ScanJob`
- `AuthSession`
- `InventoryRun`
- `InventoryPage`
- `InventoryEndpoint`
- `InventoryParameter`
- `InventoryAnnotation`
- `Finding`
- `FindingRetestRun`
- `Evidence`
- `AuditEvent`

### `app/services/`

All workflow logic lives here:

- target CRUD/import
- credential CRUD
- session/login orchestration
- inventory build and export
- checks and finding lifecycle
- evidence capture and export
- retest orchestration
- report generation
- dashboard summaries

### `app/integrations/`

External execution seams:

- HTTP login adapter
- Playwright login adapter
- Playwright inventory gateway
- Playwright evidence capture gateway

### `app/security_checks/`

Plugin-style low-risk checks with explicit metadata and bounded trigger logic.

### `app/redaction/`

Central redaction logic used by evidence storage, display, and export flows.

### `app/cli/`

Thin Typer command groups over services.

### `app/api/` + `app/templates/`

Thin FastAPI routes and server-rendered templates for browse/review flows.

## End-to-End Component Relationships

The main workflow is:

1. `TargetService` manages targets.
2. `CredentialProfileService` manages credential profiles.
3. `AuthSessionService` resolves login config + secret references and invokes the selected auth adapter.
4. Successful logins persist `AuthSession` metadata plus a `storage_ref` to raw payload storage.
5. `InventoryBuildService` either reuses an existing session or performs a fresh login, then creates a `ScanJob` and `InventoryRun`.
6. `InventoryCollectionService` restores browser/session state and invokes the Playwright collector.
7. Structured inventory is persisted as pages, endpoints, parameters, and annotations.
8. `CheckRunService` executes registered low-risk checks against structured inventory.
9. `FindingService` upserts deduplicated findings and tracks lifecycle state.
10. `EvidenceService` captures reviewable, redacted evidence for linked findings.
11. `RetestService` reruns a bounded portion of the workflow for eligible findings.
12. `FindingExportService` and `ReportService` generate redacted outputs for review and handoff.

## Data Relationships

- A `Target` can have many `CredentialProfile`, `ScanJob`, `AuthSession`, `InventoryRun`, `Finding`, `Evidence`, and `AuditEvent` records.
- A `CredentialProfile` belongs to one target and can be reused across many jobs, sessions, inventory runs, findings, and evidence items.
- A `ScanJob` groups a discrete execution step such as inventory, checks, or retest.
- An `AuthSession` belongs to one target and one credential profile and can be reused by inventory and retest flows.
- An `InventoryRun` belongs to one target, one credential profile, one session, and one job.
- A `Finding` belongs to one target, one credential profile, one session, and one job, and stores explicit inventory references.
- A `FindingRetestRun` records retest outcome and context separately from the main finding row.
- An `Evidence` record links back to target, profile, session, job, inventory, and optionally finding.
- `AuditEvent` records login, validation, refresh, lifecycle update, evidence export, retest, and report export activity.

## Login and Session Architecture

Garden uses an adapter strategy for login:

- config parsing is handled separately from execution
- HTTP flows and Playwright UI flows implement the same conceptual contract
- login results are normalized before persistence
- raw sensitive session material is stored behind a pointer rather than inline in display models

This keeps login orchestration reusable and testable instead of collapsing into a single hardcoded browser script.

## Inventory Architecture

Inventory is structured on purpose. Garden does not treat authenticated activity as an opaque blob.

It records:

- visited page URLs and titles
- timestamps
- API endpoints and methods
- status codes
- parameter names
- sensitivity annotations

Inventory collection is bounded by:

- `max_pages`
- `max_depth`
- `max_requests`
- `delay_ms`
- same-origin rules
- include/exclude path prefixes

## Checks Pipeline

Checks are implemented as separable plugins. Each plugin exposes metadata for:

- name
- purpose
- category
- severity
- confidence
- trigger explanation
- false-positive boundaries
- remediation notes

Garden's checks remain low-risk and explainable. They do not perform destructive behavior or exploit attempts.

## Evidence and Redaction Pipeline

Evidence is structured, linked, and redacted by default.

Garden captures:

- request metadata
- response metadata
- bounded request preview
- bounded response preview
- page title
- URL
- method
- status
- screenshot metadata and file location

Redaction is centralized so CLI, UI, and export paths do not drift apart.

## Lifecycle, Dedup, and Retest

Phase 7 turned Garden into a workflow product:

- findings have explicit lifecycle state
- status transitions are validated centrally
- dedup preserves `first_seen` and updates `last_seen`
- previously fixed findings can reopen when the same issue reappears
- retest reuses session context when possible and falls back to fresh login when necessary
- retest results are recorded as first-class workflow data

## Exporters

Export logic remains modular:

- findings export: Markdown, JSON, CSV
- inventory export: JSON, CSV
- evidence export: Markdown, JSON
- reports: Markdown

This keeps output generation understandable and reusable instead of spreading file-writing logic across command handlers.

## Minimal UI Flow

The UI is intentionally simple:

- dashboard
- targets
- credentials
- jobs
- sessions
- inventory
- findings
- evidence

That is enough to make the workflow demo-ready and reviewable without changing the product identity.
