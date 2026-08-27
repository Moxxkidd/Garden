# Garden v0.3.0 Passive Authenticated Coverage Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 保持 quick scan 终端、HTTP、Web 和报告体验不变，新增由 `garden coverage URL` 引导的安全凭据向导、三上下文被动覆盖比较和认证覆盖报告。

**Architecture:** `garden coverage` 使用独立向导选择或创建同源 Target 与 user/admin 凭据档案；原始秘密只进入短生命周期 `EphemeralSecretStore`，会话建立后清理。认证运行继续从 `ScanApplicationService.start_assessment(request)` 创建，由独立 `ScanPipeline.execute_authenticated(session, assessment_id)` 编排；`CoverageComparisonService.compare(contexts)` 集中处理三态覆盖语义和分类，CLI/API 只做适配。

**Tech Stack:** Python 3.10+、FastAPI、Typer、Rich、SQLAlchemy 2、Pydantic 2、Playwright、pytest、Ruff、Hatchling。

**Spec:** `docs/superpowers/specs/2026-08-26-v0-3-passive-authenticated-coverage-design.md`

## Global Constraints

- `garden scan`、`garden scan --url`、`POST /api/scans`、首页单 URL 表单和 quick 报告结构不变。
- `ScanOptions.user_agent` 保持 `Garden-Authorized-Asset-Scanner/0.2`。
- coverage 固定为 `anonymous`、`user`、`admin`；不公开 N 角色和主动重放。
- 已观察资产为 `present`；只有完整上下文才能把未观察资产判为 `absent`，否则为 `unknown`。
- 任一上下文为 `unknown` 时整体分类为 `unknown`。
- 原始密码、令牌、Cookie 和 Authorization 不进入命令参数、普通数据库、日志、HTTP、报告或异常消息。
- 每个功能切片执行一条失败测试、最小实现、同一测试转绿，再进入下一切片。
- 不修改用户已有 `.gitignore`、`.coverage` 和 `interview.md`。

---

### Task 1: Freeze quick scan UX before feature work

**Files:**
- Create: `tests/test_v030_quick_compatibility.py`
- Read only: `app/cli/scan.py`
- Read only: `app/api/routes/scans.py`
- Read only: `app/templates/index.html`
- Read only: `app/services/scan_reporting.py`

**Interfaces:**
- Consumes: existing `garden scan`, `/api/scans`, `/`, and quick report generation.
- Produces: compatibility gates every later task must keep green.

- [ ] **Step 1: Characterize the quick CLI seam**

```python
def test_v030_keeps_quick_scan_command_contract() -> None:
    result = CliRunner().invoke(app, ["scan", "--help"])
    assert result.exit_code == 0
    for token in (
        "ENTRY_URL", "--url", "--max-pages", "--max-resources", "--max-depth",
        "--request-timeout", "--overall-timeout", "--retries", "--detach", "--ui-port",
    ):
        assert token in result.stdout
    assert "--user-profile" not in result.stdout
    assert "--admin-profile" not in result.stdout
```

- [ ] **Step 2: Characterize quick HTTP, homepage and report headings**

Use a fake `app.state.scan_service.start_scan` returning a real `ScanRunView`; assert `POST /api/scans` remains 202 with mode `quick`, homepage form remains `action="/scans"`, and the current ordered quick report headings remain unchanged.

- [ ] **Step 3: Run the compatibility baseline**

Run: `.venv/bin/pytest tests/test_v030_quick_compatibility.py tests/test_quick_scan_cli.py tests/test_formal_scan_cli.py tests/test_api.py -v`

Expected: PASS before implementation. These are characterization tests, so they begin green.

- [ ] **Step 4: Commit**

```bash
git add tests/test_v030_quick_compatibility.py
git commit -m "test: freeze quick scan experience for v0.3"
```

### Task 2: Add short-lived secret storage and consume-after-use cleanup

**Files:**
- Create: `app/services/ephemeral_secret_store.py`
- Modify: `app/services/secret_resolver.py`
- Modify: `app/cli/paths.py`
- Modify: `app/services/context_establishment.py`
- Create: `tests/test_ephemeral_secret_store.py`
- Modify: `tests/test_context_establishment.py`

**Interfaces:**
- Produces: `EphemeralSecretStore.write/read/delete/purge_expired`.
- Consumes: only `ephemeral-file://` references rooted under the configured private directory.
- Preserves: existing `env://` and demo-only `literal://` resolver behavior.

- [ ] **Step 1: Write the failing owner-only atomic storage test**

```python
def test_ephemeral_secret_is_private_atomic_and_not_in_reference(tmp_path) -> None:
    store = EphemeralSecretStore(tmp_path / "secrets")
    secret = "coverage-user-secret"
    ref = store.write(secret)
    path = store.path_for(ref)
    assert ref.startswith("ephemeral-file://")
    assert secret not in ref
    assert stat.S_IMODE(path.parent.stat().st_mode) == 0o700
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert store.read(ref) == secret
    assert not list(path.parent.glob("*.tmp"))
```

Run: `.venv/bin/pytest tests/test_ephemeral_secret_store.py::test_ephemeral_secret_is_private_atomic_and_not_in_reference -v`

Expected red: module does not exist.

- [ ] **Step 2: Implement the minimal store**

Use `secrets.token_urlsafe`, exclusive file creation, `os.replace`, `chmod(0o700/0o600)`, and canonical resolved paths. `path_for` must reject non-matching schemes, symlinks, paths outside the root, non-regular files and group/world-readable permissions.

- [ ] **Step 3: Add red slices for deletion, expiry and path attacks**

```python
def test_delete_and_expiry_are_idempotent(tmp_path) -> None:
    store = EphemeralSecretStore(tmp_path / "secrets")
    ref = store.write("expired")
    path = store.path_for(ref)
    os.utime(path, (0, 0))

    assert store.purge_expired(max_age_seconds=900) == (ref,)
    store.delete(ref)
    assert not path.exists()


@pytest.mark.parametrize("ref", ["ephemeral-file:///etc/passwd", "env://SECRET"])
def test_store_rejects_outside_or_wrong_scheme(tmp_path, ref) -> None:
    store = EphemeralSecretStore(tmp_path / "secrets")
    with pytest.raises(InputValidationError):
        store.read(ref)


def test_store_rejects_symlink_even_when_target_is_inside_root(tmp_path) -> None:
    store = EphemeralSecretStore(tmp_path / "secrets")
    real_ref = store.write("hidden")
    real_path = store.path_for(real_ref)
    link = real_path.parent / "link.secret"
    link.symlink_to(real_path)

    with pytest.raises(InputValidationError):
        store.read(f"ephemeral-file://{link}")
```

Implement one failing slice at a time. `purge_expired(900)` returns sorted deleted refs and never follows symlinks.

- [ ] **Step 4: Extend SecretResolver through the store seam**

```python
def test_secret_resolver_reads_ephemeral_reference(tmp_path) -> None:
    store = EphemeralSecretStore(tmp_path)
    ref = store.write("hidden-value")
    assert SecretResolver(ephemeral_store=store).resolve(ref) == "hidden-value"
```

Keep current resolver messages for `env://` and `literal://` unchanged.

- [ ] **Step 5: Prove secrets are deleted after context establishment**

Add a context-establishment test with user/admin ephemeral refs. Assert both files are deleted after `establish(...)` succeeds and after one authentication adapter fails. Cleanup belongs in a `finally` path and only deletes `ephemeral-file://` refs created for those contexts.

- [ ] **Step 6: Run focused security tests and commit**

Run: `.venv/bin/pytest tests/test_ephemeral_secret_store.py tests/test_context_establishment.py tests/test_session_lifecycle.py -v`

```bash
git add app/services/ephemeral_secret_store.py app/services/secret_resolver.py app/cli/paths.py app/services/context_establishment.py tests/test_ephemeral_secret_store.py tests/test_context_establishment.py
git commit -m "feat: consume temporary coverage secrets safely"
```

### Task 3: Build the coverage credential wizard without touching `garden scan`

**Files:**
- Create: `app/cli/coverage_wizard.py`
- Modify: `app/services/credentials.py`
- Create: `tests/test_coverage_wizard.py`

**Interfaces:**
- Produces: `CoverageSetupWizard.run(entry_url, user_profile_id=None, admin_profile_id=None) -> PassiveCoverageStartRequest`.
- Consumes: `TargetService`, `CredentialProfileService`, login-config validation, `EphemeralSecretStore`, and an injected prompt adapter.
- Non-interactive callers bypass the wizard and must supply both profile IDs.

- [ ] **Step 1: Write the failing existing-profile selection test**

```python
def test_wizard_selects_same_origin_target_and_exact_roles(setup_records) -> None:
    prompts = ScriptedPrompts([str(setup_records.target_id), str(setup_records.user_id), str(setup_records.admin_id), "yes"])
    request = CoverageSetupWizard(prompts=prompts).run(setup_records.url)
    assert request.user_profile_id == setup_records.user_id
    assert request.admin_profile_id == setup_records.admin_id
    assert request.active_checks_enabled is False
    assert prompts.hidden_values == []
```

Run red, then implement only selection and summary confirmation. Filter targets by normalized origin and credentials by exact role.

- [ ] **Step 2: Add the create-missing-profile slice**

Script prompts for target name/owner, login URL, validation URL, username and a hidden secret. Assert the created profiles have exact `user`/`admin` roles, share one target, use generated secret-free login configs, and store only `ephemeral-file://` refs.

```python
assert created_user.secret_ref.startswith("ephemeral-file://")
assert "entered-password" not in json.dumps(created_user.__dict__, default=str)
assert "entered-password" not in prompts.rendered_text
```

- [ ] **Step 3: Add cancellation and rollback cleanup**

If the user declines the final summary or profile creation fails, delete every temporary secret created in this draft and do not submit an assessment. Existing selected profiles remain unchanged.

- [ ] **Step 4: Add strict non-interactive behavior**

```python
def test_noninteractive_coverage_requires_both_profiles(runner) -> None:
    result = runner.invoke(app, ["coverage", "https://app.test/", "--non-interactive"])
    assert result.exit_code == 2
    assert "--user-profile 和 --admin-profile" in result.stdout
    assert "Select target" not in result.stdout
```

- [ ] **Step 5: Run wizard tests and commit**

Run: `.venv/bin/pytest tests/test_coverage_wizard.py tests/test_cli.py tests/test_v030_quick_compatibility.py -v`

```bash
git add app/cli/coverage_wizard.py app/services/credentials.py tests/test_coverage_wizard.py
git commit -m "feat: guide coverage credential setup in terminal"
```

### Task 4: Implement tri-state coverage comparison

**Files:**
- Create: `app/services/coverage_comparison.py`
- Create: `tests/test_coverage_comparison.py`
- Modify: `tests/helpers/assessment.py`

**Interfaces:**
- Produces: `CoverageComparisonService.compare(session, run) -> CoverageComparisonSummary`.
- Persists: one `CoverageDifference` per `identity_key`, rebuilt idempotently for one run.

- [ ] **Step 1: Red/green admin-only**

```python
def test_complete_contexts_classify_admin_only(db_session, three_context_run) -> None:
    add_asset(db_session, three_context_run, kind="admin", identity="GET:/admin")
    summary = CoverageComparisonService().compare(db_session, three_context_run)
    item = summary.differences[0]
    assert item.classification == "admin_only"
    assert (item.anonymous_state, item.user_state, item.admin_state) == ("absent", "absent", "present")
```

- [ ] **Step 2: Red/green unknown-not-absent**

```python
def test_failed_user_context_is_unknown(db_session, three_context_run) -> None:
    fail_context(three_context_run, "user")
    add_asset(db_session, three_context_run, kind="admin", identity="GET:/admin")
    item = CoverageComparisonService().compare(db_session, three_context_run).differences[0]
    assert item.user_state == "unknown"
    assert item.user_present is None
    assert item.classification == "unknown"
```

- [ ] **Step 3: Complete the classification table one slice at a time**

Cover `shared`, `user_only`, `admin_only`, `unexpectedly_anonymous`, presence-order `inconsistent`, response-signature `inconsistent`, and `unknown`. Compare only status, redacted URL, title, content type and stable content signature.

- [ ] **Step 4: Prove idempotency and summary allowlisting**

Call `compare` twice and assert one row per identity. Put fake `authorization`, `cookie` and response text keys in asset attributes; assert none appear in `context_summaries` or diagnostics.

- [ ] **Step 5: Run and commit**

Run: `.venv/bin/pytest tests/test_coverage_comparison.py tests/test_coverage_identity.py tests/test_assessment_models.py -v`

```bash
git add app/services/coverage_comparison.py tests/test_coverage_comparison.py tests/helpers/assessment.py
git commit -m "feat: compare passive coverage across three contexts"
```

### Task 5: Orchestrate authenticated assessments and preserve partial truth

**Files:**
- Modify: `app/services/scan_pipeline.py`
- Modify: `app/services/scan_application.py`
- Create: `tests/test_authenticated_coverage_pipeline.py`
- Modify: `tests/test_assessment_application.py`

**Interfaces:**
- Produces: `ScanPipeline.execute_authenticated(session, scan_run_id)`.
- Preserves: `ScanPipeline.execute(session, scan_run_id)` quick path.
- Dispatches: quick and authenticated runs through a mode-aware application adapter.

- [ ] **Step 1: Red/green authenticated dispatch**

Start one authenticated and one quick run with the recording dispatcher. Expected submitted IDs are `[authenticated.id, quick.id]`; an active-key duplicate is not resubmitted.

- [ ] **Step 2: Red/green complete passive stage execution**

Assert persisted stages finish as:

```python
{
    "validate": "completed",
    "establish_contexts": "completed",
    "collect": "completed",
    "normalize": "completed",
    "compare_coverage": "completed",
    "replay_authorization": "skipped",
    "analyze": "completed",
    "report": "completed",
}
```

The skipped summary must state that v0.3.0 is passive. No `ReplayExecution` row may be created.

- [ ] **Step 3: Red/green partial context continuation**

Make user authentication fail while anonymous/admin succeed. The run must finish `incomplete`, comparison must contain `unknown`, report must still exist, and `active_key` must be cleared.

- [ ] **Step 4: Red/green fatal orchestration cleanup**

Force comparison to raise a safe test error. Assert current stage failed, later pending stages skipped, run failed, temporary secrets deleted, and diagnostic report attempted without raw exception secrets.

- [ ] **Step 5: Run and commit**

Run: `.venv/bin/pytest tests/test_authenticated_coverage_pipeline.py tests/test_assessment_application.py tests/test_context_establishment.py tests/test_context_collection.py tests/test_url_scan_pipeline.py tests/test_scan_cancellation.py -v`

```bash
git add app/services/scan_pipeline.py app/services/scan_application.py tests/test_authenticated_coverage_pipeline.py tests/test_assessment_application.py
git commit -m "feat: orchestrate passive authenticated assessments"
```

### Task 6: Expose coverage through separate HTTP and CLI adapters

**Files:**
- Modify: `app/schemas/assessment.py`
- Create: `app/api/routes/assessments.py`
- Modify: `app/api/router.py`
- Create: `app/cli/coverage.py`
- Modify: `app/cli/local_api.py`
- Modify: `app/cli/main.py`
- Create: `tests/test_assessments_api.py`
- Create: `tests/test_coverage_cli.py`

**Interfaces:**
- HTTP: start/read/differences/cancel/report under `/api/assessments`.
- CLI: `garden coverage URL`; interactive by default on TTY, explicit IDs required with `--non-interactive`.
- Public start schema omits active replay fields and uses `extra="forbid"`.

- [ ] **Step 1: Red/green passive HTTP start and difference read**

```python
response = client.post("/api/assessments", json={
    "url": "https://app.test/", "user_profile_id": 12, "admin_profile_id": 13,
})
assert response.status_code == 202
assert service.started.mode.value == "authenticated_coverage"
assert service.started.active_checks_enabled is False
```

Add GET status, differences, report and POST cancel using existing application interfaces.

- [ ] **Step 2: Red/green reject active fields**

Posting `active_checks_enabled` or `authorization_confirmed` returns 422 before the application interface is called.

- [ ] **Step 3: Red/green coverage CLI interactive path**

Inject a scripted wizard and fake loopback adapter. Assert `garden coverage URL` invokes the wizard, submits its `PassiveCoverageStartRequest`, and renders three context states, classification counts and report path.

- [ ] **Step 4: Prove quick command isolation**

Run `garden scan URL` with a wizard object that raises if called. Assert the quick command completes its existing fake flow without calling the wizard or secret store.

- [ ] **Step 5: Run and commit**

Run: `.venv/bin/pytest tests/test_assessments_api.py tests/test_coverage_cli.py tests/test_quick_scan_cli.py tests/test_formal_scan_cli.py tests/test_v030_quick_compatibility.py -v`

```bash
git add app/schemas/assessment.py app/api/routes/assessments.py app/api/router.py app/cli/coverage.py app/cli/local_api.py app/cli/main.py tests/test_assessments_api.py tests/test_coverage_cli.py
git commit -m "feat: expose guided passive coverage workflow"
```

### Task 7: Generate the authenticated report and prove localhost E2E

**Files:**
- Modify: `app/services/scan_reporting.py`
- Create: `tests/test_authenticated_coverage_reporting.py`
- Create: `tests/fixtures/auth_coverage_site/app.py`
- Create: `tests/fixtures/auth_coverage_site/login-config-user.yaml`
- Create: `tests/fixtures/auth_coverage_site/login-config-admin.yaml`
- Create: `tests/test_authenticated_coverage_e2e.py`
- Modify: `.github/workflows/ci.yml`

**Interfaces:**
- Preserves: `ScanReportService.generate(session, scan_run_id)` and quick renderer.
- Adds: authenticated renderer selected only when `run.mode=authenticated_coverage`.
- E2E: localhost FastAPI fixture plus real Playwright Chromium.

- [ ] **Step 1: Red/green report matrix**

```python
assert text.startswith(f"# Garden 认证覆盖报告 #{run.id}")
assert "## 三上下文健康与完整性" in text
assert "| 资产身份 | anonymous | user | admin | 分类 | 置信度 |" in text
assert "主动权限重放未执行" in text
assert "覆盖差异不是已确认漏洞" in text
```

Load contexts and differences in `generate`, branch internally by run mode, and keep the existing quick `_render` path byte-behaviorally unchanged.

- [ ] **Step 2: Red/green unknown and secret omission**

Render anonymous=`present`, user=`unknown`, admin=`present`; assert the literal matrix state is `unknown`, never blank/absent. Seed forbidden attribute keys and assert their values are absent from the report.

- [ ] **Step 3: Build the localhost fixture**

Expose `/`, `/login`, `/account`, `/admin/users`, `/anonymous-banner` and `/api/profile`. Use fixture-only HMAC-signed role cookies, hidden form passwords from environment variables, exact user/admin roles, and an ephemeral `127.0.0.1` port.

- [ ] **Step 4: Run the real E2E red, then close one seam at a time**

The E2E creates a target and two profiles through public services, calls `start_assessment`, waits through `get_assessment`, reads `list_coverage_differences`, and checks the report. It must observe `shared`, `user_only`, `admin_only`, `unexpectedly_anonymous` and role-specific response inconsistency without external network access.

Run: `.venv/bin/pytest tests/test_authenticated_coverage_e2e.py -v`

- [ ] **Step 5: Add the existing-browser CI job and commit**

Run: `.venv/bin/pytest tests/test_authenticated_coverage_reporting.py tests/test_authenticated_coverage_e2e.py tests/test_playwright_contract.py tests/test_scan_report_quality.py -v`

```bash
git add app/services/scan_reporting.py tests/test_authenticated_coverage_reporting.py tests/fixtures/auth_coverage_site tests/test_authenticated_coverage_e2e.py .github/workflows/ci.yml
git commit -m "test: verify authenticated coverage report end to end"
```

### Task 8: Release and full regression gate

**Files:**
- Modify: `pyproject.toml`
- Modify: `app/core/settings.py`
- Modify: `README.md`
- Modify: `docs/deployment.md`
- Modify: `tests/test_cli.py`
- Modify: `tests/test_install_script.py`
- Create: `tests/test_v030_release.py`

**Interfaces:**
- Produces: version `0.3.0` and guided coverage documentation.
- Preserves: installer Python discovery, public-policy migration and source-shadowing-safe launcher.

- [x] **Step 1: Red/green release metadata**

Assert `pyproject.toml` and `Settings.project_version` are `0.3.0`; update exact version output assertions but keep quick user-agent and installer logic unchanged.

- [x] **Step 2: Document the exact interactive and automated flows**

Document `garden coverage URL`, the guided Target/user/admin creation, hidden temporary-secret lifecycle, `--non-interactive` usage, passive-only meaning and difference disclaimer.

- [x] **Step 3: Run focused release gates**

```bash
.venv/bin/pytest tests/test_install_script.py tests/test_migrations.py::test_built_wheel_installs_with_loadable_migration_assets tests/test_v020_compatibility.py tests/test_v030_release.py -v
```

- [x] **Step 4: Run full verification**

```bash
.venv/bin/ruff format --check .
.venv/bin/ruff check .
.venv/bin/pytest
git diff --check
```

Expected: every command exits 0; quick compatibility, secret lifecycle, browser E2E, wheel migration and installer tests all pass.

- [x] **Step 5: Commit**

```bash
git add pyproject.toml app/core/settings.py README.md docs/deployment.md tests/test_cli.py tests/test_install_script.py tests/test_v030_release.py
git commit -m "release: prepare Garden v0.3.0"
```

## Execution gate

Before product implementation, confirm these public test seams:

1. existing quick CLI/HTTP/Web/report compatibility;
2. `EphemeralSecretStore` lifecycle;
3. `CoverageSetupWizard.run(request)`;
4. `CoverageComparisonService.compare(contexts)`;
5. `ScanApplicationService` assessment start/read;
6. `/api/assessments` and `garden coverage` adapters;
7. `ScanReportService.generate(session, scan_run_id)`;
8. localhost-only real authentication E2E.
