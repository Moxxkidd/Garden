# Architecture

## Product execution model

Garden's primary execution model is one submitted URL to one persisted report:

```text
Web form              HTTP API              gardenctl scan
   \                      |                      /
    +---------------------+---------------------+
                          |
             ScanApplicationService.start_scan
                          |
              persistent ScanRun + stages
                          |
     validate -> discover -> collect -> normalize -> analyze -> report
                          |
       assets + evidence + findings + failures + Markdown report
```

No adapter owns or duplicates the workflow. `app/services/scan_application.py`
is the use-case boundary; `app/services/scan_pipeline.py` is the persisted stage
runner. CLI uses an inline dispatcher so a shell invocation waits for the report.
The Web and HTTP surfaces use a bounded in-process dispatcher and return a task
that can be polled.

## Persistent parent task

`ScanRun` represents the user's complete request, unlike the legacy `ScanJob`
which represents an authenticated inventory/check/retest step. It stores:

- original and normalized URL
- status, current stage, percentage progress, and timestamps
- bounded options and an active-submission idempotency key
- network retry count
- error code/message and final report path/time
- six `ScanRunStage` rows with attempt, status, summary, and diagnostics

Normalized child tables are `ScanAsset`, `ScanEvidence`, `ScanFinding`, and
`ScanFailure`. The report renderer reads these structures directly. It never
parses console output or temporary CLI text.

## Pipeline stages and failure semantics

1. **validate**: normalize URL, resolve DNS, classify every address, and decide
   whether an explicitly configured proxy applies before collection starts.
2. **discover**: perform the first passive GET and extract bounded HTML metadata
   and links.
3. **collect**: follow same-origin links within maximum page/depth limits.
4. **normalize**: verify persisted normalized assets/evidence and record no-result state.
5. **analyze**: run passive, explainable checks over structured records.
6. **report**: render the complete Markdown report from persisted domain data.

An entry/preflight failure marks the run and current stage failed, persists the
failure, and attempts a diagnostic report. A single secondary-page failure is a
partial failure: collection continues and the final status is
`completed_with_warnings`. Coverage-limit and request failures are visible in
both the task API/UI and report.

## Network and SSRF boundary

Only HTTP and HTTPS are accepted. Embedded credentials are rejected. Garden
resolves and checks every requested or redirected hostname before connecting,
disallows implicit environment proxies, follows redirects itself, and validates
each redirect destination. Link-local, multicast, unspecified, and non-global
special-purpose addresses are blocked. Loopback is allowed for safe local tests.

Authorized remote public targets and loopback targets are enabled by default.
Operators can restore local-only mode with `GARDEN_ALLOW_NON_LOCAL_TARGETS=false`.
RFC1918/ULA targets still require `GARDEN_ALLOW_PRIVATE_TARGETS=true`. DNS answers
containing any disallowed address fail closed.

Requests have a per-request timeout, an overall task deadline, a response-size
cap, redirect/page/depth caps, and a configurable task concurrency cap. The
default network behavior is one initial request plus at most one retry, and only
connect/read transient failures or HTTP 502/503/504 retry.

## User adapters

- `POST /scans` accepts the Web form and redirects directly to its progress page.
- `POST /api/scans` accepts `{url, options}` and returns the parent task.
- `GET /api/scans/{id}` returns current stage, progress, failures, and counts.
- `GET /api/scans/{id}/report` reads or downloads the generated Markdown.
- `gardenctl scan --url ...` calls the same application service and prints the
  terminal task result only after the pipeline finishes.

## Legacy authenticated workflow

The credential/session/inventory/check/finding/evidence/retest services remain
available for advanced authenticated verification. Their adapters remain thin.
They are not hidden prerequisites for URL scans and their IDs are never required
to complete the URL-to-report workflow. See `docs/legacy-cli-migration.md`.

## Deployment model

SQLite and the bounded in-process dispatcher are appropriate for the current
single-process deployment. Run one Uvicorn worker. For horizontal execution,
replace `ScanDispatcher` with a durable queue implementation while retaining the
same application-service and pipeline boundaries; the persisted domain model and
adapters do not need to change.
