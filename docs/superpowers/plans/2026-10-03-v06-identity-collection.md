# Garden v0.6.0 Identity Collection Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [x]`) syntax for tracking.

**Goal:** Complete single/multiple identity asset collection, manually restored sessions, health-aware observations, visibility matrices and linked recovery runs.
**Architecture:** Add a separate identity collection orchestrator; keep quick and fixed anonymous/user/admin coverage paths compatible. Share discovery transport, persistence and catalog projection through explicit context inputs; keep private session payloads outside public models.
**Tech Stack:** Python >=3.10, existing FastAPI/Pydantic/SQLAlchemy/Alembic, Playwright, Typer, Jinja; no new runtime dependency planned.
**Spec:** ../specs/2026-10-03-v06-identity-collection-design.md (user approved 2026-10-03).

## Global Constraints

- identity_collection accepts 1–10 distinct profile IDs, or anonymous alone; reject an empty selection, cross-target profiles and active replay.
- Context key is anonymous or profile:<id>; names and roles are display labels, not verified permissions.
- Login attempts expire after 15 minutes; at most 3 live browser workers per process. Imported UTF-8 JSON is at most 1 MiB.
- Protected directories 0700/files 0600; public schemas never expose raw state, tokens, private filesystem paths or query values.
- Restore into a fresh browser context and require a positive authentication condition. HTTP 200 or cookies alone never prove success.
- Only explicit authentication origins may be used during manual login. Scanning remains same-target-origin GET/HEAD with v0.5 budgets and cancellation.
- Check health before collection, after 20 allowed requests or 30 seconds (whichever first), and before closing the final batch. Health requests consume budget but are not business assets.
- Failed health checks preserve confirmed observations; the unconfirmed batch is identity_uncertain. Unknown/failed identity coverage never means absent or denied.
- Recovery creates a new linked run for the same target/profile and known unfinished work; original reports remain unchanged.
- No v0.7 project-wide asset store, remote desktop, automated CAPTCHA solution, or promise that all device-bound/IndexedDB sessions can be transferred.
- Full delivery includes Web/CLI/API/catalog/export/report/docs, migration 0008, real local browser fixtures and regression checks. No push/PR without a separate user request.

## Review Focus

1. Two same-role/same-name profiles must remain distinct through storage, grouping, matrix columns and exports (Tasks 1, 7).
2. A valid identity receiving 403 must not be marked expired without a separate validation failure (Task 5).
3. Revoke/expiry/cancel racing with a queued request must prevent subsequent sends without deleting earlier evidence (Tasks 2, 3, 5).
4. User edits profile/target configuration or replaces a checkpoint between recovery preview and confirmation: reject stale recovery, never switch identity silently (Tasks 6, 8).
5. Oversized/malformed imports, unsafe cookie domains, SSO redirects, worker crashes and writes failing mid-save must leave no ready session or public secret (Tasks 2, 3).

## File and interface boundaries

New schemas live in `app/schemas/identity.py`; private storage/validation in `services/identity_sessions.py`; manual browser ownership in `services/manual_login.py`; task orchestration in `services/identity_collection.py`; request/response health gate in `services/identity_health.py`; checkpoint/recovery in `services/identity_recovery.py`; matrix projection in `services/identity_matrix.py`. Transport extraction goes in `services/browser_discovery.py`, keeping the existing anonymous API as a compatibility wrapper.

New models: `models/login_attempt.py`, `models/identity_checkpoint.py`; register in `models/__init__.py`. Existing ScanRun/ScanContext/AuthSession gain additive fields. All public adapters call services; none read raw state directly.

Use `/Users/an/Documents/Garden/.venv/bin/python`, `PYTHONPATH=.` and the isolated worktree. Each numbered task follows RED → minimal implementation → GREEN → commit, and may not be marked done using mocks alone where real browser acceptance is listed.

### Task 1: Identity contracts and non-destructive migration

**Files:** modify `models/enums.py`, `models/scan_context.py`, `models/scan_run.py`, `models/auth_session.py`, `models/__init__.py`, `schemas/assessment.py`; create `schemas/identity.py`, the two models above, `migrations/versions/0008_identity_collection.py`, `tests/test_identity_contracts.py`; extend `tests/test_migrations.py`.
**Interfaces:** `IdentityCollectionRequest(url: str, target_id: int, profile_ids: list[int], include_anonymous: bool, options: ScanOptions)`; `SessionHealthView(status, checked_at, reason_code)`; `IdentityContextView(context_id, context_key, profile_id, display_name, health, completeness)`; private `StoredIdentityState(version, target_origin, storage_state, session_storage)`; `ManualLoginRequest(profile_id, login_url, validate_url, success_selector, success_text, allowed_auth_origins)` requiring a positive condition; `RecoveryRequest(source_run_id, source_context_id, checkpoint_version, options)`.

- [x] RED: `test_single_profile_does_not_require_admin` asserts profile_ids=[7] validates; `test_empty_or_eleven_profiles_rejected` asserts neither anonymous nor profiles, duplicate IDs and 11 IDs fail. Cross-target checks belong to Task 4.
- [x] RED: `test_context_keys_allow_two_users_preserve_legacy` inserts profile:7/profile:8 in one run, rejects duplicate context keys and leaves fixed coverage uniqueness intact.
- [x] RED: `test_0008_upgrade_preserves_ids_and_downgrade_refuses_loss` seeds 0007, upgrades, checks keys/references, exercises reversible and unsafe downgrade cases.
- [x] Run the named tests and save failure output; implement identity mode/kind, context_key uniqueness, snapshots, health timestamps, parent/context/checkpoint references, and nullable session provenance. Migrate old kinds to context_key without guessing identity.
- [x] GREEN: `PYTHONPATH=. python -m pytest tests/test_identity_contracts.py tests/test_migrations.py tests/test_assessment_models.py -q`; commit contracts/migration only.

### Task 2: Protected state import, restore validation and revocation

**Files:** create `services/identity_sessions.py`, `tests/test_identity_sessions.py`; modify `services/session_storage.py`, `services/sessions.py`, `schemas/identity.py`.
**Interfaces:** `IdentitySessionService.import_state(session: Session, profile_id: int, raw_json: str, verification: ManualLoginRequest) -> SessionHealthView`; `validate(session, session_id: int, before_request: Callable[[], None]) -> SessionHealthView`; `revoke(session, session_id: int) -> None`; `load_for_collection(session, session_id: int) -> StoredIdentityState` (private). Import returns the created session_id in the public health view only after successful fresh-context validation.

- [x] RED: `test_import_requires_positive_restore_proof` imports cookies into a local fixture; assert a fresh context passes the configured selector and a login-page HTTP 200 fails.
- [x] RED: `test_import_limits_and_atomic_storage` rejects >1048576 UTF-8 bytes, malformed JSON, invalid origins/cookie domains and path traversal; assert 0700/0600 and a simulated replace failure leaves no ready DB row.
- [x] RED: `test_revoke_blocks_load_and_keeps_history` asserts future load fails, prior evidence remains, and public errors contain no payload/path/query value.
- [x] Implement bounded schema validation, origin filtering, capability metadata, independent restore validation, immutable session versions and protected atomic writes. Existing legacy session files remain readable only through the service's trusted reference path; new client endpoints accept no filesystem references.
- [x] GREEN: run `tests/test_identity_sessions.py`, `tests/test_session_lifecycle.py`, `tests/test_context_establishment.py`; commit.

### Task 3: User-driven browser login lifecycle

**Files:** create `services/manual_login.py`, `tests/test_manual_login.py`, `tests/fixtures/identity_site.py`; use Task 1 login-attempt model and Task 2 session service.
**Interfaces:** `ManualLoginService.start(session, request: ManualLoginRequest) -> LoginAttemptView`; `get(session, attempt_id: int) -> LoginAttemptView`; `confirm(session, attempt_id: int) -> LoginAttemptView`; `cancel(session, attempt_id: int) -> LoginAttemptView`; `cleanup_interrupted(session) -> int`. LoginAttemptView exposes ID/state/expiry/session_id/generic diagnostics only.

- [x] RED: `test_manual_challenge_restores_in_new_context` drives a local visible challenge manually in the browser fixture, confirms, closes the original context and proves a fresh context is authenticated; no auto-login selectors/password submission in the worker.
- [x] RED: parameterize cancel, 900-second expiry, fourth worker, no display, rejected SSO redirect, failed save, process restart and queued-confirm/revoke races. Assert worker/context cleanup and no ready state on failure.
- [x] Implement worker-owned Playwright objects and a bounded command queue. Persist state transitions, target/profile bindings and explicit login origin policy; limit saved state to the collection target. GET/status remains read-only.
- [x] GREEN: `tests/test_manual_login.py tests/test_identity_sessions.py`; commit manual login with all cleanup paths.

### Task 4: General identity collection orchestration and shared discovery

**Files:** create `services/identity_collection.py`, `services/browser_discovery.py`, `tests/test_identity_collection.py`; modify `services/discovery_browser.py`, `services/discovery_scan.py`, `services/scan_application.py`, `services/scan_pipeline.py`, `services/context_collection.py` narrowly.
**Interfaces:** `IdentityCollectionService.start(session, request: IdentityCollectionRequest) -> int`; `execute(run_id: int) -> None`. Shared `BrowserDiscovery.collect(start_url, options, before_request, on_response, on_candidate, seed_urls=None, on_attempt=None, *, identity_state: StoredIdentityState | None=None, on_checkpoint=None) -> dict`; preserve `AnonymousDiscoveryBrowser.collect` signature and behavior as wrapper. Existing FetchResult/candidate callbacks remain transport-neutral.

- [x] RED: `test_single_user_and_anonymous_only_create_catalog` proves one profile and anonymous-alone each generate a terminal task and catalog without admin.
- [x] RED: `test_same_role_profiles_isolated_and_cross_target_rejected` verifies two accounts get distinct protected content, independent cookies/context IDs and target-policy checks before network access.
- [x] RED: `test_all_discovery_sources_share_identity_and_budget` exercises HTML/Sitemap/JS/import/Hash/dynamic requests on a local target; assert candidate-only declarations are not called, actual requests are GET/HEAD and within budget/origin.
- [x] Implement a separate dispatch path, stable context creation, profile-bound session selection, shared transport and incremental persistence; keep fixed three-context comparison code on its existing path. Entry/source URLs never carry private state in public options.
- [x] GREEN: identity tests plus `tests/test_discovery_browser.py tests/test_discovery_pipeline.py tests/test_authenticated_coverage_pipeline.py tests/test_authenticated_coverage_e2e.py`; commit.

### Task 5: Health-aware response batches and stopping on expiry

**Files:** create `services/identity_health.py`, `tests/test_identity_health.py`; integrate Task 4 collector callbacks and ScanRequest/asset attributes as versioned metadata.
**Interfaces:** `IdentityHealthGate.before_send() -> None`; `observe(result: FetchResult) -> None`; `finish() -> SessionHealthView`, constructed with session_id, request-budget admission, monotonic clock, validation callback and persistence callback. Public assessment values are confirmed, identity_uncertain, auth_diagnostic; persist boundaries per batch.

- [x] RED: `test_health_check_at_twenty_requests_or_thirty_seconds` uses controlled clock; assert validations consume budget, are not assets, and finish validates the final batch.
- [x] RED: `test_403_does_not_mean_expired` proves an authorized session hitting denied content remains valid after positive recheck.
- [x] RED: `test_expiry_retains_confirmed_batch_and_stops_sends` revokes a fixture cookie mid-run; assert earlier confirmed responses retained, current batch uncertain, login fallbacks diagnostic, later requests stopped and another identity continues.
- [x] RED: `test_budget_or_revoke_race_never_confirms_unchecked_batch` exhausts budget immediately before validation and revokes before send; assert unknown/incomplete and preserved evidence.
- [x] Implement periodic/triggered checks, batch transition persistence, per-request revocation admission and stop reasons; no silent anonymous fallback or automatic identity replacement.
- [x] GREEN: health, collection and old authenticated reporting tests; commit.

### Task 6: Durable known-gap checkpoints and linked recovery

**Files:** create `services/identity_recovery.py`, `tests/test_identity_recovery.py`; integrate private storage and Task 4/5 checkpoints.
**Interfaces:** `IdentityRecoveryService.save(session, context_id: int, pending: list[dict], uncertain: list[dict]) -> int`; `preview(session, request: RecoveryRequest) -> RecoveryPreview`; `start(session, request: RecoveryRequest, preview_token: str) -> int`. Preview binds target/profile/config/checkpoint version and returns safe counts/examples, not raw URLs.

- [x] RED: `test_recovery_only_requeues_known_unfinished_identity` restores a new session for the same profile, rechecks uncertain/pending work and leaves successful other identities and original report untouched.
- [x] RED: `test_recovery_rejects_wrong_identity_stale_or_missing_checkpoint` covers cross-target/profile, profile edits between preview/confirm, replaced/deleted checkpoint and unsupported version.
- [x] Implement capped private queues persisted after each batch, linked child runs and immutable original evidence; annotate limits of known scope without claiming full-site recovery.
- [x] GREEN: recovery and collection tests; commit.

### Task 7: Context-aware catalog, visibility matrix and consistent outputs

**Files:** create `services/identity_matrix.py`, `tests/test_identity_matrix.py`; modify `schemas/assets.py`, `services/asset_catalog.py`, `services/asset_grouping.py`, `services/asset_export.py`, `services/scan_reporting.py`, `cli/assets.py`, `templates/asset_groups_table.html`; create `templates/identity_matrix.html`.
**Interfaces:** `build_identity_matrix(records: list[AssetRecord], contexts: list[IdentityContextView]) -> IdentityMatrix`; matrix cells use observed, identity_uncertain, not_observed, unknown with actual status lists and evidence references. Group identity uses existing route-v1/hash-v1; public rows gain additive context/profile/health metadata.

- [x] RED: `test_two_same_name_profiles_one_subject_two_observations` asserts one subject, two stable matrix columns and unmerged 200/403 response evidence.
- [x] RED: `test_failed_identity_is_unknown_and_rules_do_not_cross_context` asserts missing coverage is not denied/absent and login-fallback/uniform-response rules remain context-isolated.
- [x] RED: `test_cli_web_json_csv_report_share_counts` compares projections, filtered downloads and legacy unknown metadata; ensure private query/state never appears.
- [x] Implement one shared projection, readable labels and separate subject/observation/candidate counts. Keep legacy fields and fixed coverage summaries unchanged.
- [x] GREEN: matrix, all asset surface/grouping/validity tests and reporting tests; commit.

### Task 8: Complete Web/CLI/API user journeys

**Files:** create `api/routes/identities.py`, `cli/identities.py`, `templates/identities.html`, `templates/identity_collection_preview.html`, `tests/test_identity_adapters.py`, `tests/test_identity_e2e.py`; modify actual router/CLI registries discovered in `app/api/router.py` and `app/cli/main.py` (inspect lazy registration before editing), session pages and submission preview service.
**Interfaces:** `/api/identity-runs` POST; `/api/login-attempts` POST, `/{id}` GET, `/{id}/confirm` and `/cancel` POST; `/api/identity-sessions/import`, `/{id}/validate`, `/{id}/revoke` POST; `/api/identity-recovery/preview` and `/start` POST. `garden identities` subcommands collect, login, confirm, import, validate, revoke, recover call the same application services; CLI import reads an explicitly supplied local file and enforces the same byte cap.

- [x] RED: `test_manual_login_and_collection_have_separate_confirmations` verifies login does not scan, positive restore precedes ready, and submission preview does not access the target.
- [x] RED: `test_identity_form_previews_and_recovers_without_secret_echo` exercises selection/edit/confirm, expired token, stale recovery and server error responses; no state text reflected in HTML/API/CLI.
- [x] RED: `test_desktop_mobile_full_identity_journey` runs a real local browser target with challenge, single-user scan, two-user matrix, expiry and recovery. Assert files/downloads/Markdown, profile labels and current filter consistency. Test native forms with JavaScript disabled.
- [x] Implement adapters, input limits, progress/error messages, headful-host explanation and import fallback; expose no arbitrary payload path or raw state download route.
- [x] GREEN: adapters/E2E and original preview, coverage wizard and CLI tests; commit.

### Task 9: Release verification and evidence-based documentation

**Files:** modify `pyproject.toml`, `app/core/settings.py`, version checks and doctor/migration expectations, `README.md`, `docs/index.html`; add `docs/demo-v06-identities.md`.

- [x] Run the complete fixed local journey; record observed subject/identity/candidate counts and privacy/expiry outcomes. Write documentation from those results, including 0008 upgrade/rollback behavior, supported state storage, local-window limitation and recovery scope.
- [x] Set 0.6.0 only when all previous tasks are complete. Include migration/templates/new services in wheel and run old database upgrade tests and current CLI smoke commands.
- [x] Run `PYTHONPATH=. python -m pytest -o addopts='' -q`, `ruff check .`, `ruff format --check .`, `git diff --check`; verify exact exit status and totals.
- [x] Request independent read-only whole-branch review, resolve concrete findings with RED/GREEN tests, and rerun affected/full checks as required. Review identity leakage, worker lifecycle, checkpoint races and current schema compatibility.
- [x] Commit verified code/docs. Report achieved scope, test evidence and limitations. Preserve the isolated worktree; no remote publication unless requested.

## Execution proposal and review ledger

Recommend Native execution in this session: the tasks share evolving context/session/checkpoint interfaces, so one implementer can integrate them sequentially with less coordination overhead. Use one independent reviewer at the final gate. If the user prefers fresh implementer/reviewer agents per task, use Subagent-driven instead; do not dispatch before that choice.

Spec-to-plan mapping: spec 1→Tasks 1/4/8; 2→2/3/8; 3→2/3/5; 4→4/5; 5→7/8; 6→6/8; 7→all task tests/9. All five review focus conditions have named tests. User approved the spec and Native execution. Tasks 1–9 implemented; independent review findings resolved and final verification passed. Named test scenarios are covered across contract, service, adapter and browser tests; multi-identity/expiry/recovery use separate real-browser fixtures alongside the native-form journey.


## Final verification — 2026-10-06

Environment: Darwin 25.3.0 arm64, Python 3.10.10, real local Chromium; branch `codex/identity-collection-v06`, implementation head before final fixes `d581f84bd8b3cc298e20a345bbaaa73355100fab`.

- `PYTHONPATH=. /Users/an/Documents/Garden/.venv/bin/python -m pytest -o addopts='' -q`: **755 passed, 2 warnings in 479.05s**. Warnings are upstream websockets/uvicorn deprecations. Earlier failed experiments are not counted as success.
- `ruff check .`, `ruff format --check .`, `git diff --check`: passed (279 Python files formatted).
- `python -m hatchling build -t wheel -d .superpowers/dist`: built `garden-0.6.0-py3-none-any.whl`. Verified identity services, API/CLI, templates and migration 0008 in the archive. From an isolated extracted wheel, `--version`, `identities --help`, and `db upgrade` each exited 0. Dependencies came from the existing virtual environment; this is not a clean dependency installation claim.
- Real JS-disabled desktop/mobile journey: 2 observed subjects, HTTP 200, 0 authentication diagnostics; desktop/mobile screenshots inspected. Cookie, localStorage and sessionStorage long expiry/recovery plus clear-state regressions passed.
- Independent whole-branch reviewer found three Important issues: old-state health proof, incomplete target binding in recovery, and mixed confirmed/uncertain matrix evidence. All fixed with regression tests. Identity runs now explicitly skip risk analysis and persist browser collection mode.
- Live-state regression exposed navigation-time storage-query blocking; rejected callback-queue experiments were removed. Final implementation tracks Chromium storage mutation events and current cookies, hydrates sessionStorage once, and preserves budget/unobserved status for browser-cancelled requests.
- Remote CI and other operating systems were not run in this local delivery. No tag, release, package upload, push or PR.

### Implementation rulings and costs

1. Preserve legacy three-role enum and partial run/kind uniqueness; new identity views use stable context keys. Cost: maintain legacy adapters alongside the new mode.
2. Source documents consume existing request/page limits plus their own bounds. Cost: source discovery can leave less budget for business pages.
3. Checkpoints cap known work at 2000 requests / 1 MiB URL material and expose truncation. Cost: larger queues cannot be completely recovered in one checkpoint.
4. Keep the isolated worktree under `.worktrees/identity-collection-v06` after temporary-directory cleanup. Cost: local disk usage; the main checkout's user changes remain separate.
5. Manual-login CLI uses the running Garden Web service via `--api-url` to own browser lifetime. Cost: that workflow requires the Web service.

No known review finding is deferred. v0.7 cross-run asset merging, remote desktop, arbitrary IndexedDB/device-bound session portability remain outside this approved version.
