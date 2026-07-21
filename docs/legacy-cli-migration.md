# Legacy CLI Migration

## What changed

Before this refactor, `gardenctl scan` required a username/password and created a
target, credential profile, login session, inventory job, checks job, and report
inside the command handler. The command was the business orchestrator.

`gardenctl scan` now accepts one URL and calls `ScanApplicationService.start_scan`.
It automatically waits for the persisted six-stage run and prints the final
report location. Removed scan-specific authentication/selector flags are not
required for the anonymous asset-report product flow.

```bash
gardenctl scan --url http://127.0.0.1:8080/
```

Use `--max-pages`, `--max-depth`, `--request-timeout`, `--overall-timeout`, and
`--retries` to adjust safe bounds. `--retries` means retries after the first
request and is capped at two.

## Advanced authenticated workflow

Existing target, credential, login, session, inventory, checks, findings,
evidence, retest, and legacy job-report commands remain available. Use those
explicit commands only when authenticated exploration or lifecycle triage is the
actual task. They do not need to be run before or after `gardenctl scan`.

No old data tables are removed. `ScanJob` remains the advanced workflow step
record; `ScanRun` is the new URL-to-report parent task.
