# Authenticated Coverage Platform Design

**Date:** 2026-08-11
**Status:** Approved design
**Product decision:** Authenticated application-surface verification is Garden's core capability. One-URL passive scanning remains the low-friction entry. The next product expansion is a fixed three-context coverage-difference platform with opt-in active authorization replay.

## 1. Purpose

Garden will answer three related questions for an authorized application:

1. What can an anonymous visitor, a normal user, and an administrator observe?
2. How do the discovered pages, endpoints, parameters, and passive response properties differ?
3. Can a lower-privilege context replay a request observed in a higher-privilege context and obtain equivalent access?

The product must keep the current one-URL workflow, but the primary value proposition becomes authenticated coverage, explainable differences, redacted evidence, triage, and retest rather than generic vulnerability scanning.

## 2. Scope

### 2.1 MVP scope

- One unified persisted run model for quick and authenticated assessments.
- A fixed context set: `anonymous`, `user`, and `admin`.
- Passive comparison of pages, endpoints, parameters, status codes, redirects, content types, titles, and redacted response signatures.
- Opt-in cross-role replay from higher privilege to lower privilege.
- Replay of captured `GET`, `HEAD`, and `OPTIONS` requests only.
- Structured, redacted evidence for every comparison and replay verdict.
- Findings lifecycle, manual triage, retest, and report export.
- Web, HTTP API, and CLI entry points over the same application service.
- Backward-compatible one-URL quick scan entry points during migration.

### 2.2 Out of scope for the MVP

- Automatic identifier substitution or ID enumeration.
- Replay of request bodies or `POST`, `PUT`, `PATCH`, `DELETE`, or other state-changing methods.
- Destructive verification, exploit payloads, brute force, or broad fuzzing.
- Arbitrary N-role comparison in the UI or public contract.
- Automatic declaration of a confirmed vulnerability without explicit access policy or human triage.
- Replacing Burp, ZAP, Nuclei, or other execution engines.

The internal context model must not prevent a later N-role extension, but the first user-facing contract remains fixed to three contexts.

## 3. Product experience

### 3.1 Quick scan

A user submits one authorized HTTP or HTTPS URL. Garden creates a `quick` run containing only the anonymous context and executes bounded passive collection, analysis, and reporting.

The existing `gardenctl scan --url URL` and `/api/scans` contract remain compatibility aliases for this mode during migration.

### 3.2 Authenticated coverage

A user submits:

- the authorized entry URL;
- one normal-user credential profile;
- one administrator credential profile;
- bounded collection controls; and
- an optional active-replay switch.

Garden creates one run with anonymous, user, and admin contexts. It validates both authenticated sessions, collects each context under equivalent limits, calculates coverage differences, optionally performs bounded replay, creates review candidates, and generates one report.

### 3.3 Quick-to-coverage transition

Completed runs are immutable. Starting authenticated coverage from a quick result creates a new run with `source_run_id` pointing to the quick run. Garden may reuse target configuration, but it does not silently reuse old observations as if they were collected in the new three-context snapshot.

## 4. Unified architecture

`ScanRun` becomes the single product-level assessment record. UI and documentation call it an assessment; the first migration retains the existing class and table name to reduce migration risk.

```text
Web / API / CLI
       |
       v
ScanApplicationService.start_assessment
       |
       v
ScanRun (quick | authenticated_coverage)
       |
       +-- ScanContext: anonymous
       +-- ScanContext: user
       +-- ScanContext: admin
       |
       +-- context-scoped assets and captured requests
       +-- coverage differences
       +-- replay executions
       +-- evidence and findings
       +-- one unified report
```

The unified pipeline is:

```text
validate
-> establish_contexts
-> collect
-> normalize
-> compare_coverage
-> replay_authorization
-> analyze
-> report
```

`replay_authorization` is persisted as `skipped` when active replay is not enabled. It must never disappear silently from progress or reports.

The current `ScanJob` model becomes a legacy execution record. Its inventory, check, and retest responsibilities move into `ScanRunStage`, `ScanContext`, and dedicated replay/retest records. The old table remains readable during the migration window and is removed only after data migration and adapter compatibility are verified.

## 5. Domain model

### 5.1 ScanRun

The unified parent record adds or formalizes:

- `mode`: `quick` or `authenticated_coverage`;
- `source_run_id`: optional reference to the quick run that led to this assessment;
- `active_checks_enabled`;
- authorization-confirmation metadata;
- immutable execution-option snapshot;
- overall status, completeness, stage, progress, timing, and report metadata.

### 5.2 ScanContext

Each context belongs to one run and stores:

- `kind`: `anonymous`, `user`, or `admin`;
- optional credential-profile and auth-session references;
- login and session-validation status;
- collection status, timing, error code, and error message;
- context-specific collection counts and completeness.

A quick run has exactly one anonymous context. An authenticated-coverage run has exactly one context of each fixed kind.

### 5.3 ScanAsset

`ScanAsset` becomes the context-scoped representation of pages, endpoints, and parameters. It stores:

- `scan_run_id` and `context_id`;
- asset type;
- canonical identity key;
- URL, method, status, title, and structured redacted attributes;
- discovery source and timestamp.

Existing inventory page, endpoint, and parameter data migrate into this structure. Until migration completes, old inventory models are read-only compatibility sources.

### 5.4 ScanRequest

A captured request stores:

- source context and asset;
- method, normalized URL, header-name metadata, and request fingerprint;
- replay eligibility and the reason for inclusion or exclusion;
- a protected-storage reference for sensitive exact values;
- a redacted display form for UI, logs, and reports.

Database fields and ordinary exports must not contain raw cookies, authorization headers, or other reusable session material.

### 5.5 CoverageDifference

A difference record contains:

- canonical asset identity;
- presence in anonymous, user, and admin contexts;
- per-context status, redirect, content-type, title, and redacted-content signatures;
- a classification such as shared, user-only, admin-only, unexpectedly anonymous, or inconsistent;
- comparison confidence and explanatory diagnostics.

### 5.6 ReplayExecution

A replay record contains:

- source request and source context;
- target context;
- exact replay-policy snapshot;
- status, timing, redirect chain, response signature, and redacted evidence;
- verdict: `blocked`, `equivalent_access`, `changed_response`, or `inconclusive`;
- an explanation of the evidence used for the verdict.

### 5.7 Finding and evidence consolidation

One finding model links to passive differences, replay executions, assets, and evidence. Findings retain severity, confidence, status, deduplication, first/last seen, retest, and export behavior.

One evidence model stores structured redacted previews and protected-storage references. Parallel quick-scan and authenticated-workflow evidence/finding/report implementations are removed after migration.

## 6. Canonical identity and passive comparison

The passive identity key uses:

```text
HTTP method + normalized path + sorted parameter-name set
```

Parameter values do not participate in passive identity. Normalization must remove fragments, normalize default ports, preserve meaningful path structure, and apply an explicit policy for trailing slashes and repeated query keys.

For each identity, Garden compares:

- context presence;
- HTTP status;
- final URL and redirect class;
- content type;
- page title;
- stable redacted-content signature;
- discovered parameter names and response markers.

Dynamic values such as timestamps, nonces, request identifiers, and session-specific text must be normalized or excluded from the stable signature. A missing or failed context is recorded as unknown, not absent.

## 7. Active authorization replay protocol

### 7.1 Activation

Active replay is disabled by default. A run must persist an explicit opt-in and authorization confirmation. Candidate selection, skips, executions, and verdicts are audited.

Until Garden has application-level operator authentication and an active-replay permission, network-exposed Web/API surfaces must not enable active replay. Local CLI execution may enable it only with an explicit confirmation flag.

### 7.2 Candidate directions

The MVP generates lower-privilege replay candidates for:

- `user -> anonymous`;
- `admin -> user`;
- `admin -> anonymous`.

Anonymous-to-authenticated and user-to-admin requests are covered by passive comparison and are not active authorization candidates.

### 7.3 Eligibility

A replay candidate must:

- originate from the current run;
- use `GET`, `HEAD`, or `OPTIONS`;
- target the admitted origin;
- have passed URL, DNS, redirect, and target-policy checks;
- remain within configured candidate, request, concurrency, response-size, and time budgets;
- contain no request body;
- require no identifier mutation.

Garden does not accept an arbitrary user-supplied replay URL through this protocol.

### 7.4 Request construction

Garden preserves the captured method, path, and query values. It discards source-context cookies, authorization headers, proxy credentials, and hop-by-hop headers, then applies the target context's validated session. Safe representation headers may be copied through an explicit allowlist.

Redirects are followed manually. Every destination is revalidated, and a cross-origin or disallowed redirect terminates the replay as blocked or inconclusive according to the observed response.

The default is one replay per source-target pair. Automatic retries are disabled unless a future policy explicitly limits retries to transport failures and records every attempt.

### 7.5 Verdicts

- `blocked`: 401/403, a recognized login redirect, an authentication-failure marker, or another explicit denial.
- `equivalent_access`: the lower-privilege context receives a successful response with high similarity to the higher-privilege response.
- `changed_response`: the request succeeds, but authorization-relevant response properties differ materially.
- `inconclusive`: session loss, timeout, unstable content, policy rejection, or insufficient evidence prevents a reliable comparison.

`equivalent_access` creates a suspected authorization finding. It is not a confirmed vulnerability until a human triages it or an explicit access policy establishes that the target context should be denied.

## 8. Safety, storage, and audit controls

- Existing HTTP/HTTPS, DNS, redirect, proxy, target-admission, timeout, concurrency, page, depth, and response-size guardrails apply to every context and replay.
- Active replay never widens the target scope established at run creation.
- Session material and exact replay values use protected storage with restrictive permissions, atomic writes, expiration, deletion, and an encryption-ready interface.
- UI, CLI, logs, audit events, evidence previews, and reports use centralized redaction.
- The run stores authorization confirmation, operator identity when available, configuration snapshot, replay counts, and all skip reasons.
- Demo routes and demo credentials must be disabled outside an explicit demo environment before authenticated coverage is exposed beyond loopback.
- Durable execution and restart recovery are required before recommending multi-user or multi-instance deployment.

## 9. Failure and completeness semantics

- A user or admin login failure makes an authenticated-coverage run `incomplete`.
- Unknown context data cannot be treated as an asset absence or authorization difference.
- A single collection failure produces a context warning and preserves other results.
- A replay-stage failure does not discard passive coverage results, but the report states that active verification is incomplete.
- If a target context session is invalid before replay, all affected candidates are skipped and recorded; Garden does not continue with stale session state.
- A stage failure is authoritative even if diagnostic report generation also fails.
- A run can be `completed`, `completed_with_warnings`, `incomplete`, or `failed`; each status has an explicit completeness explanation.

Queued and running work must either resume after a worker restart or transition to a terminal interrupted state with a retry path. It must not remain permanently active because of a stale idempotency key.

## 10. User interfaces

### 10.1 Web

The home page presents two entry cards:

- Quick scan: URL and bounded scan controls.
- Authenticated coverage: URL, user profile, admin profile, bounded controls, active-replay opt-in, and authorization confirmation.

The assessment page shows:

- overall progress and completeness;
- three context cards with login, collection, and asset counts;
- a three-column coverage matrix;
- replay candidates and verdicts;
- findings, evidence, failures, and uncovered reasons;
- the unified report.

The server-rendered UI remains intentionally light; it does not require a heavy SPA.

### 10.2 HTTP API

The canonical API is:

```text
POST /api/assessments
GET  /api/assessments/{id}
GET  /api/assessments/{id}/contexts
GET  /api/assessments/{id}/differences
GET  /api/assessments/{id}/replays
GET  /api/assessments/{id}/report
```

`POST /api/scans` maps to a quick assessment during the compatibility window. Compatibility responses include the canonical assessment identifier and a deprecation notice without breaking the current response contract.

### 10.3 CLI

Canonical commands are:

```bash
gardenctl assess start --url URL --mode quick

gardenctl assess start \
  --url URL \
  --mode authenticated-coverage \
  --user-profile USER \
  --admin-profile ADMIN \
  --enable-active-replay \
  --confirm-authorized
```

`gardenctl scan --url URL` remains a quick-mode alias during migration.

## 11. Reports and findings

The unified report contains:

1. execution summary and authorized scope;
2. run mode, option snapshot, and completeness;
3. anonymous, user, and admin session validity;
4. three-context coverage matrix and important differences;
5. active-replay scope, verdicts, skips, and evidence;
6. suspected authorization findings and triage state;
7. passive findings;
8. failures, limits, and uncovered reasons;
9. retest history where applicable;
10. generation and traceability metadata.

Reports distinguish observed facts, automated inference, and human-confirmed conclusions. Binary response bodies and reusable session material are never embedded.

## 12. Migration strategy

1. Introduce Alembic before changing the persisted model.
2. Add unified-run fields and new context, request, difference, and replay tables without removing legacy tables.
3. Route new quick runs through the unified model while keeping current adapters compatible.
4. Add three-context collection and passive comparison.
5. Add active replay behind local CLI opt-in and feature gating.
6. Add canonical assessment API, UI, CLI, and report paths.
7. Migrate legacy inventory, evidence, finding, and job references with repeatable migration tests.
8. Switch reads to unified records, retain legacy read-only access for one compatibility window, then remove obsolete paths.

Completed historical runs remain immutable. Migrations must never reinterpret incomplete legacy data as complete three-context coverage.

## 13. Implementation work packages

### WP1: Product and protocol alignment

- Align README, PLANS, architecture, threat model, deployment, demo, and migration documents.
- Define canonical terms, statuses, API schemas, and compatibility policy.

### WP2: Migration and unified model foundation

- Add Alembic.
- Extend `ScanRun` and add `ScanContext`, `ScanRequest`, `CoverageDifference`, and `ReplayExecution`.
- Add protected storage and unified evidence/finding references.

### WP3: Unified orchestration and quick compatibility

- Implement the new stage graph.
- Keep existing quick Web/API/CLI behavior working through adapters.
- Add interrupted-run handling and durable-dispatcher contract tests.

### WP4: Three-context collection and passive differences

- Validate user and admin sessions.
- Collect all three contexts under equivalent limits.
- Normalize assets and calculate the coverage matrix.

### WP5: Active replay engine

- Select eligible candidates.
- Replace source authentication with the target context.
- Apply network and execution budgets.
- Produce verdicts, evidence, audit events, and suspected findings.

### WP6: Product surfaces and lifecycle

- Add assessment Web, API, and CLI surfaces.
- Add unified report, triage, retest, and export paths.
- Add operator authorization before enabling active replay over Web/API.

### WP7: Legacy migration and removal

- Migrate historical references.
- Remove obsolete `ScanJob`, parallel inventory, evidence, finding, and report paths after compatibility verification.
- Refresh internal package READMEs and generated documentation.

### WP8: Operational hardening

- Add durable execution and restart recovery.
- Add data-retention controls, secure session/replay storage, observability, and non-root deployment.
- Gate demo routes and credentials by environment.

## 14. Test strategy

The implementation requires:

- schema-upgrade, rollback, and historical-data migration tests;
- current quick API and CLI compatibility tests;
- unit tests for identity normalization and dynamic-content filtering;
- unit tests for coverage classifications and every replay verdict;
- tests that reject unsafe methods, request bodies, cross-origin targets, disallowed redirects, arbitrary replay URLs, and source credential leakage;
- tests for login failure, session expiry, partial collection, partial replay, and incomplete-run reporting;
- integration tests for all three replay directions;
- a real Chromium login, inventory, and evidence path for user and admin contexts;
- an end-to-end local fixture with deliberately allowed and denied resources;
- restart tests proving queued/running work resumes or terminates explicitly;
- redaction tests across database views, UI, CLI, audit events, and exports;
- regression tests for finding deduplication, triage, retest, and report generation.

CI must keep Ruff, formatting, Python-version coverage, Docker build, and CLI smoke checks. A real-browser job must be added instead of relying exclusively on mock or contract tests.

## 15. Definition of done

The first authenticated-coverage release is complete when:

- one-URL quick scanning remains usable through existing entry points;
- one Web form, API request, or CLI command can start a three-context assessment;
- user and admin sessions are validated and all three contexts are collected under equivalent bounds;
- the report clearly states what each context observed and which assets differ;
- opt-in replay executes the three approved higher-to-lower privilege directions;
- every replay is bounded, same-origin, non-mutating, redacted, and audited;
- incomplete collection cannot be reported as an authorization difference;
- suspected authorization findings link to reproducible redacted evidence and support triage and retest;
- application access control gates Web/API active replay, while local CLI use requires explicit authorization confirmation;
- database migrations, backward compatibility, real-browser E2E, restart recovery, lint, formatting, and the full test suite pass;
- README, architecture, threat model, deployment, demo, and migration documents consistently describe authenticated verification as the core and quick scan as the entry;
- no raw reusable session material appears in ordinary database views, logs, UI, CLI, or reports.

## 16. Future extensions

After the MVP is validated, Garden may add arbitrary role matrices, explicit route-access policies, carefully approved state-changing request templates, continuous scheduled assessments, baseline drift, external scanner execution adapters, and ticketing or notification integrations. These extensions must reuse the unified run, context, evidence, and finding contracts rather than reintroduce parallel workflows.
