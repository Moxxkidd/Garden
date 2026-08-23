# Garden v0.2.0 Trusted Report Design

**Date:** 2026-08-23<br>
**Status:** Approved design, pending written-spec review<br>
**Scope:** Quick URL scan resource version hints, repeated-finding projection, compatibility contracts, and v0.2.0 release metadata

## Context

The NJAU report exposed two report-trust problems:

- ordinary decimal values in CSS or SVG content could be presented as software versions;
- page-level security-header observations could dominate the report even though they represented only two repeated control gaps.

The current `origin/main` baseline at `af0d2d4` already contains tactical corrections from the URL report-quality and installer-hardening work:

- version extraction requires more context than a bare decimal in CSS or JavaScript content;
- the Markdown report groups equivalent findings while retaining the raw observation count;
- installer discovery, legacy public-policy migration, and repository-shadowing fixes are already present.

Version 0.2.0 will turn those tactical corrections into an explicit, testable report-quality boundary. It will improve report accuracy without changing how users submit scans, navigate the Web UI, call the API, invoke the CLI, or read the report structure.

## Product Decision

Garden v0.2.0 is the **trusted report release**.

The release prioritizes explainable evidence and conservative claims over finding more possible version strings. An omitted or unverified version is preferable to a plausible-looking false version. Raw assets, evidence, and findings remain available for audit even when the report projects them into a smaller set of user-facing groups.

## Compatibility Baseline

The compatibility baseline is `origin/main` at `af0d2d4`, not the older NJAU report text generated before the report-quality fixes were merged.

The following user-visible contracts remain unchanged:

| Surface | Frozen contract |
|---|---|
| CLI | Commands, aliases, flags, defaults, foreground/detached behavior, and exit semantics |
| Web | Routes, scan form fields, navigation flow, and primary result layout |
| HTTP API | Existing routes, request fields, response field names and types, and status meanings |
| Report | Section names, section order, asset/evidence references, coverage-warning separation, and Markdown delivery path |
| Collection | Passive GET behavior, same-origin boundary, address-policy revalidation, budgets, retries, and timeout semantics |
| Persistence | Existing tables and columns; raw assets, evidence, findings, and failures remain readable |

The expected version string changes from `0.1.0` to `0.2.0`. Report content may become more conservative or less repetitive, but the report structure and interaction model do not change.

## Goals

- Make every displayed resource-version hint traceable to a strong context.
- Prevent CSS values, SVG coordinates, dimensions, dates, and unrelated decimals from becoming version claims.
- Preserve current grouped-finding output while moving grouping policy out of Markdown rendering details.
- Retain raw finding rows and raw counts for audit and API compatibility.
- Regenerate reports from older scan records conservatively when version provenance is unavailable.
- Freeze the current CLI, Web, API, report, and scan-status experience with compatibility tests.
- Release the result as Garden `0.2.0` without a database migration.

## Non-goals

- Increasing page, resource, depth, response-size, retry, or timeout budgets.
- Adding JavaScript browser rendering or active vulnerability probing.
- Adding authenticated-context comparison or changing advanced workflows.
- Adding new Web controls, report filters, CLI options, or API routes.
- Mapping detected component versions to CVEs or asserting vulnerability from a version string.
- Deleting or rewriting stored raw findings and evidence.
- Reimplementing the three installer fixes already merged before this branch.

## Considered Approaches

### A. Internal report-quality boundary — selected

Keep collection and analysis records intact, but route version extraction and finding grouping through a small, pure quality module. Persist optional provenance in the existing evidence JSON and project raw findings into typed report groups.

This approach preserves auditability and public contracts, supports focused tests, and avoids a migration. It also prevents the Markdown renderer from becoming the owner of domain classification rules.

### B. Delete noise during collection and analysis

Reject questionable metadata and merge findings before persistence. This reduces stored rows, but changes API counts, damages traceability, makes old and new runs semantically different, and prevents reviewers from reaching the raw observations. It is incompatible with the experience freeze.

### C. Post-process generated Markdown

Apply regex replacements and heading deduplication after report generation. This is inexpensive, but leaves persisted data, CLI summaries, Web views, and Markdown inconsistent. It is fragile and cannot explain why a value was accepted. It is rejected.

## Architecture

Add an internal module named `app/services/scan_report_quality.py`. It owns two pure, independently testable operations:

1. extracting trusted version hints from a resource;
2. projecting raw `ScanFinding` rows into stable report groups.

The module does not perform network access, database writes, Markdown rendering, or UI formatting.

```text
FetchResult
    |
    v
trusted version extraction
    |
    v
existing ScanEvidence.data.resource_summary

persisted ScanFinding rows
    |
    v
stable finding projection
    |
    v
existing ScanReportService renderer
```

`ScanPipeline` remains responsible for collection and persistence. It calls the quality module when building the existing `resource_summary`. `ScanReportService` remains responsible for Markdown, but consumes typed finding groups rather than building ad hoc dictionaries.

## Trusted Version Hints

### Candidate model

Internally, extraction uses an immutable candidate with:

- normalized version value;
- provenance kind: `query`, `path`, or `body_marker`;
- a short source label suitable for tests and future diagnostics.

The existing `resource_summary.version_hints` remains a list of strings. No existing field changes type.

New evidence may add an optional `version_hint_details` member inside the existing extensible JSON object. It records the accepted value and provenance but is not rendered as a new report section. Top-level API schemas and all existing JSON members retain their names and types; the provenance member is additive, optional metadata. Consumers that ignore unknown members continue to work.

### Accepted contexts

A candidate is accepted only when it is associated with one of these contexts:

- a query value whose key is `v`, `ver`, or `version`;
- a version joined to a named file, library, or path segment, such as `jquery-ui-1.12.1` or `swiper@11.0.3`;
- an explicit body marker such as `@version 3.4.2` or `Version: 6.8`;
- a known library or component name immediately associated with the candidate in the bounded resource prefix already inspected by Garden.

Candidates are normalized, deduplicated in encounter order, and capped at the existing maximum of five displayed values.

### Rejected contexts

The extractor rejects numbers that are only:

- CSS property values, opacity values, transforms, dimensions, or selectors;
- SVG coordinates, path data, view boxes, or drawing metadata;
- timestamps, HTTP dates, article dates, UUID fragments, hashes, or byte counts;
- bare numeric path segments without a named component relationship;
- numbers found beyond the existing bounded text prefix;
- malformed, excessively long, or ambiguous values.

The extractor identifies possible component metadata only. It does not assert that the version is vulnerable, current, or the version actually executing on the server.

### Legacy evidence

Older evidence records may contain `version_hints` without provenance. During report regeneration:

- hints that can be revalidated from a strong query or path context remain visible;
- unverifiable legacy body-only hints are omitted from the regenerated report;
- the underlying stored JSON is not mutated.

This conservative fallback intentionally prefers omission to repeating an untraceable claim.

## Finding Projection

Analysis continues to create one raw observation per affected asset. Existing `finding_count` values in the API, Web detail page, and foreground CLI remain raw counts.

The report-quality module returns typed groups with:

- the representative finding;
- observation count;
- sorted unique asset IDs;
- sorted unique evidence IDs.

The stable grouping key preserves the current main-branch behavior:

- title;
- category;
- severity;
- confidence;
- summary;
- remediation.

Groups are ordered by the smallest finding ID in each group. Reference samples retain the current limits and formatting. The Markdown report continues to show the number of grouped classes and the number of raw observations.

No grouping is applied to failures. Coverage warnings and genuine request or stage failures continue to use the shared failure classifier introduced before v0.2.0.

## Data Flow and Compatibility

For new scans:

1. The network gateway returns the same bounded `FetchResult`.
2. The pipeline derives the same asset type and security signals.
3. The quality extractor returns trusted version candidates.
4. The pipeline stores the existing string list and optional provenance in the existing evidence JSON.
5. Passive analysis stores the same raw finding rows.
6. The report service loads persisted rows, obtains quality projections, and renders the current section structure.

For old scans, missing optional provenance is handled by the legacy fallback. No migration or data backfill is required.

## Error Handling

Report-quality operations are deterministic and fail-soft:

- unrecognized content produces no version hint;
- malformed optional legacy metadata is ignored;
- an empty finding list produces an empty projection;
- quality classification never triggers another target request;
- quality classification never converts a completed scan into a failed scan.

Programming errors still fail tests and normal report-stage error handling. The implementation must not catch broad exceptions merely to hide defects.

## Experience-Compatibility Tests

Add targeted contract tests that record the current main-branch behavior without relying on fragile full-output snapshots:

- the registered CLI commands, compatibility alias, scan flags, defaults, and version command shape;
- the existing scan API request and response field names and types;
- the existing Web scan route and form field names;
- the report section headings and order;
- raw API/Web/CLI finding counts versus grouped report counts;
- completed, completed-with-warnings, failed, and cancelled status meanings;
- the current report output path and coverage-warning/request-failure separation.

The tests permit the expected `0.2.0` version value and more accurate report content. They reject new required input, removed fields, renamed sections, or changed workflow semantics.

## NJAU Regression Fixture

Use a local, deterministic fixture derived from the observed NJAU patterns. Tests must not access the live university site.

The fixture contains:

- CSS and SVG-like decimal values that previously looked like versions;
- explicit version query strings and named component paths that should remain visible;
- explicit library version markers in bounded script or stylesheet content;
- 52 HTML assets missing the same two response headers, producing 104 raw observations;
- representative coverage warnings separated from genuine failures.

Acceptance for this fixture:

- unrelated CSS/SVG decimals never appear as version hints;
- accepted hints have an asserted provenance;
- 104 raw findings remain persisted and visible through raw counts;
- the report renders two finding classes with 104 raw observations;
- report heading names and order match the compatibility baseline;
- the fixture performs no external DNS resolution or network request.

## Release Work

After report-quality and compatibility tests pass:

- change package and default application metadata from `0.1.0` to `0.2.0`;
- update version assertions and installer smoke fixtures without changing their invocation flow;
- add a concise v0.2.0 development log describing trusted-version provenance and compatibility guarantees;
- build and install a wheel in an isolated temporary home;
- verify `garden --version`, foreground scan help, the compatibility alias, database initialization, and report generation from outside and inside a repository directory;
- run the complete test, Ruff, formatting, and diff checks before delivery.

Local installed Garden is updated only after the branch is reviewed and the release artifact passes these gates.

## Implementation Boundaries

Expected production changes are limited to:

- `app/services/scan_report_quality.py` for pure quality policy;
- `app/services/scan_pipeline.py` for extraction integration;
- `app/services/scan_reporting.py` for typed projection consumption and legacy hint filtering;
- version metadata in `pyproject.toml` and `app/core/settings.py`;
- focused tests, fixture helpers, and release documentation.

No models, migrations, API routes, templates, CLI option declarations, network policy, or scan-budget defaults should change. If implementation reveals a need to modify one of those areas, work stops for a design amendment instead of expanding scope implicitly.

## Verification Gates

Implementation is releasable only when all of the following pass:

1. focused version-extraction tests;
2. focused finding-projection tests;
3. NJAU regression fixture;
4. CLI, Web, API, report, and lifecycle compatibility tests;
5. old-record report-regeneration tests;
6. complete `pytest` suite;
7. Ruff check and formatting check;
8. wheel build and clean temporary installation;
9. formal CLI smoke from both a neutral directory and a Garden repository directory;
10. `git diff --check` and final code review.

## Acceptance Criteria

- Garden displays a version only when the extractor can identify an accepted context.
- NJAU-style CSS and SVG decimals are absent from version hints.
- Equivalent findings remain raw in persistence and grouped in the report.
- Existing CLI, Web, API, report structure, network behavior, and status semantics remain unchanged.
- Old scan records remain readable and regenerate conservatively.
- No database migration is introduced.
- `garden --version` reports `Garden 0.2.0` after the release artifact is installed.
- All verification gates pass before local installation or release delivery.
