# URL Scan Core Refactor Checklist

This document is the living audit and acceptance checklist for the URL-to-report
execution model. It must be updated whenever an implementation or verification
stage changes.

## Audited legacy execution chain

```text
gardenctl scan
  -> parse URL and prompt for username/password in app/cli/scan.py
  -> create/reuse Target
  -> create CredentialProfile with an environment-secret reference
  -> InventoryBuildService (login/session + inventory ScanJob)
  -> CheckRunService (a second ScanJob + findings/evidence)
  -> ReportService(checks job id)
  -> print several IDs and the report path
```

The Web surface only browses legacy records. There is no HTTP or Web start
endpoint. `ScanJob` represents an inventory/check/retest step, not the complete
user request, and has no stage/progress/retry/report fields. The one-command
orchestration and transient credential setup are duplicated in the CLI adapter.

## Coupling and hidden-step findings

- [x] CLI owns end-to-end orchestration and transient state transfer.
- [x] Inventory and checks create separate jobs; no persisted parent run exists.
- [x] Report generation receives a checks job ID and cannot represent the full run.
- [x] Web/API cannot start the workflow and cannot show true pipeline progress.
- [x] Entry requires credentials even when an anonymous URL scan is sufficient.
- [x] Network policy is host-string based and does not validate DNS answers or redirects.
- [x] Proxy abstraction is a placeholder and network preflight is implicit.
- [x] Partial coverage and individual request failures are not report sections.

## Target execution model

```text
CLI / Web form / HTTP API
          |
          v
ScanApplicationService.start_scan(url, options)
          |
          v
persistent ScanRun + bounded dispatcher
          |
          v
validate/network preflight -> discover -> collect -> normalize -> analyze -> report
          |
          v
assets + evidence + findings + failures + stages + final report
```

## Implementation and acceptance

- [x] Core application service is independent of CLI, Web, and HTTP.
- [x] Persistent parent task records status, progress, stage, errors, retries, and report.
- [x] Assets, evidence, findings, failures, and stage outcomes use normalized structures.
- [x] Network preflight runs before collection; retries are bounded and diagnostic.
- [x] URL scheme, credentials, DNS answers, redirects, SSRF policy, timeout, and concurrency are bounded.
- [x] Duplicate active submissions are idempotent.
- [x] A failed stage is explicit; partial results never look complete.
- [x] Report uses persisted structured data and contains every required section.
- [x] CLI is a thin adapter over the core service.
- [x] HTTP API can start once, inspect progress/failure, and read/download the report.
- [x] Web UI can submit one URL, follow progress, and read/download the report.
- [x] Invalid URL, duplicate, timeout, partial failure, and no-result behavior are tested.
- [x] Unit tests cover network policy, retry rules, analysis, and report structure.
- [x] Integration tests cover the complete persisted pipeline.
- [x] End-to-end test covers user entry through final report.
- [x] All legacy tests pass after migration.
- [x] A real local fixture run has been executed and its report inspected.
- [x] README, architecture, operation, and legacy CLI migration docs are current.
- [x] Package wheel builds, declared dependencies are consistent, and CLI/database startup passes.

## Final verification evidence (2026-07-21)

- `pytest -q`: 85 passed, including all pre-refactor tests and 11 URL-pipeline tests.
- `ruff check .`, `ruff format --check .`, and `git diff --check`: passed.
- `pip check`: no broken requirements.
- Wheel build: `garden-0.1.0-py3-none-any.whl` built without dependency resolution.
- Fresh-install smoke: the wheel plus declared runtime dependencies installed into
  an isolated venv; `gardenctl healthcheck` passed with a new SQLite file.
- CLI smoke: `gardenctl --help` exposed the one-URL scan entry and advanced commands.
- Real TCP run: one `POST /api/scans` against the bundled loopback fixture reached
  100%, all six stages completed, and persisted 3 assets, 3 evidence records,
  and 7 passive findings.
- Final inspected report: `exports/scan-reports/scan-1.md`; every required section,
  linked evidence, fixture asset, coverage line, and generation time was present.
