# Partial Scan Report Classification Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox syntax for tracking.

**Goal:** Preserve, analyze, and accurately report partial quick-scan results when same-origin enforcement or the collection deadline limits coverage.

**Architecture:** Add a small shared stage-and-code classifier while retaining the existing persistence model. Convert collection-time deadline exhaustion into a persisted coverage warning, stop target I/O, and allow only local normalization, analysis, and reporting to finish. Reuse the classifier in lifecycle finalization and Markdown rendering so status, counts, and sections agree.

**Tech Stack:** Python 3.10+, SQLAlchemy 2, Pydantic 2, pytest, httpx MockTransport

## Global Constraints

- **Human ruling (Task 4 fix round 1/5):** retain this work combined with the existing work in PR #17 (`fix/installer-report-hardening`); do not split it into a separate branch or pull request. Whole-branch review and delivery remain the controller's responsibility.
- Do not follow redirects outside the configured same-origin boundary.
- Do not increase page, resource, depth, response-size, or time budgets automatically.
- Do not add a database column, table, or migration.
- Only collect/coverage_limit_reached, collect/cross_origin_redirect_blocked, and collect/overall_timeout are coverage warnings.
- No target request may start after the collection deadline is observed.
- Genuine validation, policy, connection, decoding, persistence, and reporting errors remain fatal.
- Partial coverage must never be described as exhaustive.

## File Structure

- Create app/services/scan_failure_classification.py for the shared warning policy.
- Modify app/services/scan_pipeline.py for graceful timeout, stage status, local finalization, and context counts.
- Modify app/services/scan_reporting.py for partitioning, warning details, and completed-stage accounting.
- Modify tests/test_url_scan_pipeline.py for all behavioral regressions and fatal controls.
- Modify README.md to explain target-collection timeout semantics.

---

### Task 1: Shared Coverage-Warning Classifier

**Files:**
- Create: app/services/scan_failure_classification.py
- Test: tests/test_url_scan_pipeline.py

**Interfaces:**
- Consumes: stage: str and code: str values.
- Produces: is_coverage_warning(stage: str, code: str) -> bool.

- [ ] **Step 1: Write the failing classifier test**

Add this import and test:

~~~python
from app.services.scan_failure_classification import is_coverage_warning


def test_only_collect_boundary_and_budget_codes_are_coverage_warnings() -> None:
    assert is_coverage_warning("collect", "coverage_limit_reached")
    assert is_coverage_warning("collect", "cross_origin_redirect_blocked")
    assert is_coverage_warning("collect", "overall_timeout")
    assert not is_coverage_warning("validate", "cross_origin_redirect_blocked")
    assert not is_coverage_warning("collect", "connect_error")
    assert not is_coverage_warning("report", "stage_execution_failed")
~~~

- [ ] **Step 2: Run the classifier test and verify RED**

Run:

~~~bash
.venv/bin/pytest tests/test_url_scan_pipeline.py::test_only_collect_boundary_and_budget_codes_are_coverage_warnings -q
~~~

Expected: collection fails because app.services.scan_failure_classification does not exist.

- [ ] **Step 3: Implement the minimal classifier**

Create app/services/scan_failure_classification.py:

~~~python
"""Shared classification for persisted quick-scan diagnostic records."""

from __future__ import annotations


_COVERAGE_WARNING_KEYS = frozenset(
    {
        ("collect", "coverage_limit_reached"),
        ("collect", "cross_origin_redirect_blocked"),
        ("collect", "overall_timeout"),
    }
)


def is_coverage_warning(stage: str, code: str) -> bool:
    """Return whether a diagnostic record describes bounded partial coverage."""

    return (stage, code) in _COVERAGE_WARNING_KEYS
~~~

- [ ] **Step 4: Run the classifier test and verify GREEN**

Run the Step 2 command. Expected: 1 passed.

- [ ] **Step 5: Commit the classifier**

~~~bash
git add app/services/scan_failure_classification.py tests/test_url_scan_pipeline.py
git commit -m "分类扫描覆盖告警"
~~~

### Task 2: Graceful Partial Completion at the Collection Deadline

**Files:**
- Modify: app/services/scan_pipeline.py around execute, _run_stage, and _discover_and_collect
- Modify: tests/test_url_scan_pipeline.py timeout regression

**Interfaces:**
- Consumes: is_coverage_warning(stage, code) and the monotonic collection deadline.
- Produces: a collect-stage completed_with_warnings outcome with no further target I/O and local finalization enabled.

- [ ] **Step 1: Replace the fatal-timeout test with a partial-completion test**

Use a controlled clock advanced by the first child request:

~~~python
def test_overall_timeout_preserves_partial_collection_and_finishes_report(tmp_path) -> None:
    class ControlledClock:
        value = 0.0

        def __call__(self) -> float:
            return self.value

        def advance(self, seconds: float) -> None:
            self.value += seconds

    clock = ControlledClock()
    calls: list[str] = []

    def handler(request):
        calls.append(request.url.path)
        if request.url.path == "/":
            return httpx.Response(
                200,
                headers={"content-type": "text/html"},
                text='<a href="/first">First</a><a href="/never">Never</a>',
            )
        if request.url.path == "/first":
            clock.advance(2.0)
            return httpx.Response(
                200,
                headers={"content-type": "text/html"},
                text="<title>First</title>",
            )
        raise AssertionError("No request may start after the deadline")

    service = _service(tmp_path, handler, clock=clock)
    scan = service.start_scan(
        "http://127.0.0.1/",
        ScanOptions(max_pages=3, overall_timeout_seconds=1, retry_attempts=0),
    )

    assert calls == ["/", "/first"]
    assert scan.status == "completed_with_warnings"
    assert scan.error_code is None
    assert scan.asset_count == 2
    assert scan.evidence_count == 2
    selected = [
        stage.status
        for stage in scan.stages
        if stage.name in {"collect", "normalize", "analyze", "report"}
    ]
    assert selected == ["completed_with_warnings", "completed", "completed", "completed"]
    assert [failure.code for failure in scan.failures].count("overall_timeout") == 1
    assert "First" in service.read_report(scan.id)
~~~

- [ ] **Step 2: Run the timeout test and verify RED**

~~~bash
.venv/bin/pytest tests/test_url_scan_pipeline.py::test_overall_timeout_preserves_partial_collection_and_finishes_report -q
~~~

Expected: FAIL because the current pipeline marks the run failed and skips normalize/analyze.

- [ ] **Step 3: Convert only collection timeout to a warning**

In _discover_and_collect, explicitly check the deadline after entry discovery, let deadline exceptions from _collect reach the same handler, and catch only OverallScanTimeout. Record exactly one collect/overall_timeout row, commit it, and return a summary stating that partial assets and evidence will be finalized locally.

Do not catch FetchError, InputValidationError, TargetPolicyError, persistence errors, analyzer errors, or report errors.

- [ ] **Step 4: Restrict deadline enforcement to target-I/O stages**

Add enforce_deadline: bool = True to _run_stage and guard its before/after checks. Validate retains deadline enforcement. Collect performs a pre-call check and then relies on checks inside its own network loop. Normalize, analyze, and report pass enforce_deadline=False because they perform local finalization only.

After each operation, refresh the run and use is_coverage_warning for that stage. Set the stage to completed_with_warnings when it contains coverage warnings; otherwise set completed.

- [ ] **Step 5: Run timeout and complete-pipeline tests**

~~~bash
.venv/bin/pytest   tests/test_url_scan_pipeline.py::test_overall_timeout_preserves_partial_collection_and_finishes_report   tests/test_url_scan_pipeline.py::test_complete_pipeline_persists_structured_data_and_report   -q
~~~

Expected: 2 passed.

- [ ] **Step 6: Commit graceful timeout handling**

~~~bash
git add app/services/scan_pipeline.py tests/test_url_scan_pipeline.py
git commit -m "保留超时扫描的部分结果"
~~~

### Task 3: Consistent Report and Context Classification

**Files:**
- Modify: app/services/scan_pipeline.py in _finalize_quick_context
- Modify: app/services/scan_reporting.py in _render
- Modify: tests/test_url_scan_pipeline.py cross-origin, timeout, and fatal cases

**Interfaces:**
- Consumes: is_coverage_warning(stage, code) and persisted ScanFailure rows.
- Produces: consistent warning/failure counts, sections, stage counts, and context failure counts.

- [ ] **Step 1: Extend the cross-origin regression**

Add assertions to test_collection_blocks_cross_origin_redirect_before_following_it:

~~~python
assert scan.error_code is None
assert scan.contexts[0].collection_status == "completed_with_warnings"
assert scan.contexts[0].completeness == "incomplete"
assert scan.contexts[0].failure_count == 0
report = service.read_report(scan.id)
assert "- 覆盖告警：1" in report
assert "- 请求或阶段失败：0" in report
coverage = report.split("## 覆盖告警", 1)[1].split("## 请求失败", 1)[0]
request_failures = report.split("## 请求失败", 1)[1]
assert "cross_origin_redirect_blocked" in coverage
assert "cross_origin_redirect_blocked" not in request_failures
~~~

Extend the timeout regression with equivalent assertions for overall_timeout. Add one combined fixture producing two blocked redirects before timeout and assert three coverage warnings and zero request failures.

- [ ] **Step 2: Add a fatal request control**

~~~python
def test_entry_connection_error_remains_a_fatal_request_failure(tmp_path) -> None:
    def handler(request):
        raise httpx.ConnectError("fixture connect failure", request=request)

    service = _service(tmp_path, handler)
    scan = service.start_scan("http://127.0.0.1/", ScanOptions(retry_attempts=0))

    assert scan.status == "failed"
    assert scan.error_code == "network_retry_exhausted"
    report = service.read_report(scan.id)
    assert "- 覆盖告警：0" in report
    assert "- 请求或阶段失败：1" in report
    assert "network_retry_exhausted" in report.split("## 请求失败", 1)[1]
~~~

- [ ] **Step 3: Run both focused tests and verify RED**

~~~bash
.venv/bin/pytest   tests/test_url_scan_pipeline.py::test_collection_blocks_cross_origin_redirect_before_following_it   tests/test_url_scan_pipeline.py::test_entry_connection_error_remains_a_fatal_request_failure   -q
~~~

Expected: cross-origin assertions fail under the old classification; the fatal control confirms the stable fatal semantics.

- [ ] **Step 4: Reuse the classifier in reporting**

Import is_coverage_warning and partition the persisted rows with it. Warning details include URL, attempt, and retryability. Change the empty warning copy to cover same-origin, page/resource/depth, and collection-time budgets. Count both completed and completed_with_warnings stages as completed in the stage summary.

- [ ] **Step 5: Reuse the classifier in quick-context finalization**

Partition run.failures. If coverage warnings exist, set collection_status to completed_with_warnings and completeness to CompletenessStatus.INCOMPLETE.value. Always set failure_count to the genuine request-failure count. With no warnings, preserve completed plus LEGACY_SINGLE_CONTEXT for backward compatibility.

- [ ] **Step 6: Run the URL pipeline suite and verify GREEN**

~~~bash
.venv/bin/pytest tests/test_url_scan_pipeline.py -q
~~~

Expected: all URL scan pipeline tests pass.

- [ ] **Step 7: Commit consistent reporting**

~~~bash
git add app/services/scan_pipeline.py app/services/scan_reporting.py tests/test_url_scan_pipeline.py
git commit -m "区分覆盖告警与请求失败"
~~~

### Task 4: Documentation and Release Verification

**Files:**
- Modify: README.md
- Modify: app/services/scan_failure_classification.py (Ruff import-order gate)
- Modify: docs/superpowers/specs/2026-08-17-partial-scan-report-classification-design.md (diff whitespace gate)
- Track: docs/superpowers/plans/2026-08-17-partial-scan-report-classification.md

**Interfaces:**
- Consumes: implemented collection deadline behavior.
- Produces: exact user documentation and release-level verification evidence.

- [x] **Step 1: Document the timeout behavior**

Add this statement to the URL scan CLI options section:

~~~markdown
--overall-timeout bounds target network collection. When it expires, Garden stops new requests, marks coverage incomplete, and finishes local normalization, analysis, and report generation for evidence already collected.
~~~

- [x] **Step 2: Run formatting, lint, and the complete suite**

~~~bash
/Users/an/Documents/Garden/.venv/bin/python -m ruff format --check .
/Users/an/Documents/Garden/.venv/bin/python -m ruff check .
/Users/an/Documents/Garden/.venv/bin/python -m pytest -q
~~~

Expected: all commands exit 0 with zero test failures.

- [x] **Step 3: Run package, wheel, installer, and CLI smoke checks**

~~~bash
/Users/an/Documents/Garden/.venv/bin/python -m pytest tests/test_migrations.py::test_built_wheel_installs_with_loadable_migration_assets -q
/Users/an/Documents/Garden/.venv/bin/python -m pytest tests/test_install_script.py -q
/Users/an/Documents/Garden/.venv/bin/python -m app.cli.main --help
/Users/an/Documents/Garden/.venv/bin/python -m app.cli.main healthcheck
~~~

`Makefile` has no `test-wheel`, `test-install`, or `test-cli-smoke` target. The first command above is the repository test that builds a wheel, installs it into an isolated target, and verifies that `app` imports from that target; the installer test and the two CLI commands are the repository's corresponding checks.

- [x] **Step 4: Confirm combined-branch integrity, Task 4 delta, and absence of migrations**

~~~bash
git diff --check origin/main...HEAD
git diff --name-only origin/main...HEAD
migration_paths="$(git diff --name-only origin/main...HEAD)" || exit $?
if printf '%s\n' "$migration_paths" | rg -q '(^|/)migrations/|(^|/)alembic'; then
  echo "Unexpected migration or Alembic path in combined branch diff" >&2
  exit 1
fi
git diff --check 46784e6..HEAD
git diff --name-only 46784e6..HEAD
~~~

Expected: under the human ruling, do not assert that the combined `origin/main...HEAD` diff contains only this feature's files. Instead, the explicit assertion exits 1 only when a migration or Alembic path is present and exits 0 when none is present; separately assert that the Task 4 delta from base `46784e6` contains only intended Task 4 documentation and release-gate files.

- [x] **Step 5: Commit documentation and release-gate fixes**

~~~bash
git add README.md \
  app/services/scan_failure_classification.py \
  docs/superpowers/plans/2026-08-17-partial-scan-report-classification.md \
  docs/superpowers/specs/2026-08-17-partial-scan-report-classification-design.md
git commit -m "修复发布验证门禁"
~~~

- [x] **Step 6: Handoff to controller final whole-branch review**

The implementation subagent records the completed Task 4 evidence and hands the combined PR #17 branch to the controller for final whole-branch review. Per the SDD workflow, only after that review may the controller push, update the draft PR, or install the reviewed commit into the formal local Garden runtime. This task performs none of those delivery actions.
