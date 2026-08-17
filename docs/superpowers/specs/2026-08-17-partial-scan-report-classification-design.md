# Partial Scan Report Classification Design

**Date:** 2026-08-17<br>
**Status:** Approved design, pending implementation plan<br>
**Scope:** Quick URL scan collection lifecycle and structured report classification

## Problem

Garden scan 14 collected 183 assets and 201 evidence records before its 90-second deadline, but the run was marked `failed`. The report grouped these three records together as request or stage failures:

- two `collect/cross_origin_redirect_blocked` records produced by the configured same-origin boundary;
- one `collect/overall_timeout` record produced by the configured collection time budget.

The first two records prove that an intended safety boundary worked. The timeout means coverage is partial, not that the already collected evidence is invalid. Treating all three as fatal failures also prevents the local `normalize` and `analyze` stages from processing the partial collection.

## Goals

- Distinguish coverage warnings from genuine request or stage failures.
- Preserve and analyze evidence collected before the network deadline.
- Produce a complete structured report for a partial but usable scan.
- Keep genuine validation, policy, connection, decoding, persistence, and reporting failures fatal.
- Avoid a database migration for this correction.

## Non-goals

- Following redirects outside the configured same-origin boundary.
- Increasing page, resource, depth, response-size, or time budgets automatically.
- Retrying non-retryable boundary decisions.
- Claiming complete coverage after a deadline or boundary skip.
- Redesigning the authenticated multi-context assessment lifecycle.

## Classification

Classification is derived centrally from both stage and code. The existing `scan_failures` table remains unchanged for compatibility and audit history.

The following records are coverage warnings only when they occur during `collect`:

- `coverage_limit_reached`
- `cross_origin_redirect_blocked`
- `overall_timeout`

The stage condition is load-bearing. For example, an entry URL that cannot pass validation must not become a successful scan merely because a lower-level error code resembles a collection warning.

All other records remain request or stage failures. The report service, terminal run status calculation, and quick-context failure count must use the same classifier so the API and Markdown report cannot disagree.

## Collection Deadline Behavior

The configured overall timeout remains a hard deadline for new network activity. When it expires during collection:

1. Garden stops scheduling or issuing additional requests.
2. Garden records one `collect/overall_timeout` coverage warning.
3. The collect stage becomes `completed_with_warnings` and reports the partial request counts.
4. Already persisted assets and evidence remain authoritative within their recorded bounds.
5. Local-only finalization continues through `normalize`, `analyze`, and `report` without further target requests.

This finalization may finish after the network deadline. The user-facing copy must make clear that the deadline bounds collection, while local finalization preserves the usable partial result.

## Terminal State and Completeness

A quick scan with only coverage warnings ends as `completed_with_warnings`, with `current_stage=finished` and a generated report. It must not set a fatal run-level error code.

The anonymous context remains incomplete when collection stopped because of `overall_timeout`; this prevents downstream consumers from treating the collected surface as exhaustive. Boundary skips alone also prevent a claim of exhaustive coverage, but do not increment the context's genuine request-failure count.

A fatal request or stage error continues to produce `failed` and preserves the current diagnostic failure-report path.

## Report Output

The execution summary reports separate counts:

- coverage warnings;
- request or stage failures.

For the scan-14 pattern, the desired summary is three coverage warnings and zero request or stage failures. The detailed coverage section lists the two blocked cross-origin redirects and the overall timeout. The request-failure section states that none were recorded.

The coverage narrative explicitly says that same-origin enforcement and budget exhaustion reduced coverage. It must not imply that the blocked destinations were assessed or authorized.

## Implementation Boundaries

- Add one small shared classifier for persisted scan records, based on `(stage, code)`.
- Make collection convert `OverallScanTimeout` into a partial-collection outcome rather than allowing it to escape to the fatal pipeline handler.
- Ensure post-collection finalization performs no network I/O.
- Reuse the classifier in reporting and quick-context finalization.
- Do not add columns, tables, or migrations.

## Test Strategy

1. A report-level regression fixture containing the scan-14 pattern must fail under the old implementation and then assert:
   - three coverage warnings;
   - zero request or stage failures;
   - all three records appear only in the coverage section.
2. The cross-origin redirect test must confirm that the redirect target is never fetched and the run completes with warnings.
3. A deterministic advancing-clock test must confirm that timeout:
   - stops additional requests;
   - retains partial assets and evidence;
   - completes normalize, analyze, and report;
   - ends `completed_with_warnings`.
4. A fatal-error control test must confirm a genuine request or stage error still ends `failed` and remains in the request-failure section.
5. Context failure counts must exclude coverage warnings.
6. The complete test suite, wheel test, installer tests, and formal CLI smoke test must remain green before delivery.

## Acceptance Criteria

- The scan-14 failure pattern is rendered as coverage warnings rather than request failures.
- Partial evidence is normalized and analyzed after collection timeout.
- No request occurs after the collection deadline is observed.
- Fatal errors retain their current semantics.
- No database migration is introduced.
- The report accurately communicates that coverage is incomplete.
