"""Observable contracts for comparing a quick run with its actual rerun source."""

# Imported fixture is intentionally injected by pytest.
# ruff: noqa: F811

import hashlib
from datetime import datetime, timezone

import pytest
from test_scan_preview_pages import browser  # noqa: F401

from app.db.bootstrap import session_scope
from app.models.scan_context import ScanContext
from app.models.scan_run import ScanAsset, ScanEvidence, ScanFinding, ScanRun, ScanRunStage
from app.schemas.scan import ScanOptions


def make_run(session, observed, *, source_id=None, paths=("shared", "old", "new")):
    now = datetime.now(timezone.utc)
    run = ScanRun(
        input_url="http://127.0.0.1/",
        normalized_url="http://127.0.0.1/",
        mode="quick",
        status="completed",
        completeness="legacy_single_context",
        current_stage="finished",
        progress=100,
        rerun_of_run_id=source_id,
        options=ScanOptions(
            request_timeout_seconds=3, overall_timeout_seconds=60, retry_attempts=0
        ).model_dump(),
    )
    session.add(run)
    session.flush()
    context = ScanContext(
        scan_run_id=run.id,
        kind="anonymous",
        status="completed",
        collection_status="completed",
        completeness="legacy_single_context",
        failure_count=0,
    )
    session.add(context)
    session.flush()
    for position, name in enumerate(("collect", "analyze"), 1):
        session.add(
            ScanRunStage(scan_run_id=run.id, name=name, position=position, status="completed")
        )
    for path in paths:
        url = "http://127.0.0.1/" + path
        asset = ScanAsset(
            scan_run_id=run.id,
            context_id=context.id,
            asset_type="page",
            url=url,
            method="GET",
            status_code=200,
            attributes={"body_truncated": False},
            discovered_at=now,
        )
        session.add(asset)
        session.flush()
        evidence = ScanEvidence(
            scan_run_id=run.id,
            asset_id=asset.id,
            evidence_type="http_response",
            title="Response",
            source_url=url,
            summary="HTTP 200",
            data={},
            collected_at=now,
        )
        session.add(evidence)
        session.flush()
        if path in observed:
            session.add(
                ScanFinding(
                    scan_run_id=run.id,
                    dedup_key=hashlib.sha256(f"header|{url}".encode()).hexdigest()[:32],
                    title="缺少安全响应头",
                    category="security-headers",
                    severity="low",
                    confidence="high",
                    summary="被动观察",
                    remediation="结合业务复核",
                    asset_ids=[asset.id],
                    evidence_ids=[evidence.id],
                    created_at=now,
                )
            )
    session.flush()
    return run


@pytest.fixture
def pair(browser):
    with session_scope() as session:
        before = make_run(session, {"shared", "old"})
        after = make_run(session, {"shared", "new"}, source_id=before.id)
        return before.id, after.id


def test_comparison_matches_observations_across_different_record_ids(browser, pair):
    _, service, dispatcher = browser
    before_id, after_id = pair
    original = [service.get_scan(run_id).model_dump() for run_id in pair]
    result = service.compare_with_source(after_id)
    assert result.comparable is True
    assert result.source.id == before_id
    assert result.current.id == after_id
    assert result.counts == {"new": 1, "persistent": 1, "not_observed": 1, "unknown": 0}
    shared = next(item for item in result.items if item.status == "persistent")
    assert shared.before.id != shared.after.id
    assert shared.before.asset_urls == shared.after.asset_urls == ["http://127.0.0.1/shared"]
    assert shared.before.evidence[0].id != shared.after.evidence[0].id
    assert dispatcher.submissions == []
    assert [service.get_scan(run_id).model_dump() for run_id in pair] == original


@pytest.mark.parametrize("side", [0, 1])
@pytest.mark.parametrize(
    "change",
    [
        "budget",
        "missing_options",
        "entry",
        "failed",
        "coverage",
        "context",
        "analysis",
        "truncated",
    ],
)
def test_incomparable_runs_keep_positive_matches_but_do_not_infer_absence(
    browser, pair, side, change
):
    _, service, _ = browser
    with session_scope() as session:
        run = session.get(ScanRun, pair[side])
        if change == "budget":
            run.options = {**run.options, "max_pages": 7}
        elif change == "missing_options":
            run.options = {"max_pages": 50}
        elif change == "entry":
            run.normalized_url = "http://127.0.0.1/different?value=secret-entry"
        elif change == "failed":
            run.status = "failed"
        elif change == "coverage":
            run.completeness = "pending"
        elif change == "context":
            run.contexts[0].collection_status = "completed_with_warnings"
        elif change == "analysis":
            next(stage for stage in run.stages if stage.name == "analyze").status = "failed"
        else:
            run.assets[0].attributes = {"body_truncated": True}
    result = service.compare_with_source(pair[1])
    assert result.comparable is False
    assert result.reasons
    assert result.counts == {"new": 0, "persistent": 1, "not_observed": 0, "unknown": 2}
    assert "secret-entry" not in result.model_dump_json()


@pytest.mark.parametrize("side", [0, 1])
@pytest.mark.parametrize("change", ["running", "authenticated", "active"])
def test_unsupported_or_unfinished_tasks_cannot_be_compared(browser, pair, side, change):
    from app.core.errors import ConflictError, InputValidationError

    _, service, _ = browser
    with session_scope() as session:
        run = session.get(ScanRun, pair[side])
        if change == "running":
            run.status = "running"
        elif change == "authenticated":
            run.mode = "authenticated_coverage"
        else:
            run.active_checks_enabled = True
    with pytest.raises((ConflictError, InputValidationError)):
        service.compare_with_source(pair[1])


def test_uncollected_asset_is_unknown_even_when_each_run_finished_within_its_scope(browser):
    _, service, _ = browser
    with session_scope() as session:
        before = make_run(session, {"old"}, paths=("old",))
        after = make_run(session, {"new"}, source_id=before.id, paths=("new",))
        after_id = after.id
    result = service.compare_with_source(after_id)
    assert result.counts == {"new": 0, "persistent": 0, "not_observed": 0, "unknown": 2}
    assert all("对应资产" in item.reason for item in result.items)


@pytest.mark.parametrize("field", ["asset_ids", "evidence_ids", "dedup_key"])
def test_unverifiable_legacy_observations_are_not_collapsed_or_assumed_new(browser, pair, field):
    _, service, _ = browser
    with session_scope() as session:
        source = session.get(ScanRun, pair[0])
        for index, finding in enumerate(source.findings):
            setattr(finding, field, [] if field != "dedup_key" else ("" if index == 0 else " "))
    result = service.compare_with_source(pair[1])
    assert result.counts == {"new": 0, "persistent": 0, "not_observed": 0, "unknown": 4}
    assert len(result.items) == 4


def test_query_values_are_distinct_matching_locations_but_never_displayed(browser):
    _, service, _ = browser
    with session_scope() as session:
        before = make_run(session, {"item?q=alpha-private"}, paths=("item?q=alpha-private",))
        after = make_run(
            session, {"item?q=beta-private"}, source_id=before.id, paths=("item?q=beta-private",)
        )
        after_id = after.id
    result = service.compare_with_source(after_id)
    assert result.counts["persistent"] == 0
    assert result.counts["unknown"] == 2
    assert "alpha-private" not in result.model_dump_json()
    assert "beta-private" not in result.model_dump_json()


def test_foreign_record_references_never_expose_other_run_evidence(browser, pair):
    _, service, _ = browser
    with session_scope() as session:
        unrelated = make_run(session, {"private?q=do-not-leak"}, paths=("private?q=do-not-leak",))
        finding = session.get(ScanRun, pair[1]).findings[0]
        finding.asset_ids = [unrelated.assets[0].id]
        finding.evidence_ids = [unrelated.evidence[0].id]
    result = service.compare_with_source(pair[1])
    assert "do-not-leak" not in result.model_dump_json()
    assert result.counts["new"] == result.counts["not_observed"] == 0
    assert any(item.after and item.after.evidence_missing for item in result.items)


def test_http_comparison_and_page_link_to_both_observations_without_running_scans(browser, pair):
    client, service, dispatcher = browser
    response = client.get(f"/api/scans/{pair[1]}/comparison")
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    data = response.json()
    assert data["counts"] == {"new": 1, "persistent": 1, "not_observed": 1, "unknown": 0}
    page = client.get(f"/scans/{pair[1]}/comparison")
    assert page.status_code == 200
    for label in (
        "可比性",
        "新增观察",
        "持续观察",
        "未再观察到",
        "无法判断",
        "不代表已修复",
        "页面上限",
    ):
        assert label in page.text
    for item in data["items"]:
        for side in ("before", "after"):
            observation = item[side]
            if observation:
                anchor = f"observation-{observation['run_id']}-{observation['id']}"
                assert f'href="#{anchor}"' in page.text
                assert f'id="{anchor}"' in page.text
                assert observation["evidence"][0]["summary"] in page.text
    assert f'href="/scans/{pair[1]}/comparison"' in client.get(f"/scans/{pair[1]}").text
    assert len(service.list_scans()) == 2
    assert dispatcher.submissions == []


def test_comparison_displays_changed_budget_without_exposing_user_agent_value(browser, pair):
    client, _, _ = browser
    with session_scope() as session:
        run = session.get(ScanRun, pair[1])
        run.options = {**run.options, "max_pages": 7, "user_agent": "secret-user-agent"}
    response = client.get(f"/api/scans/{pair[1]}/comparison")
    assert response.status_code == 200
    settings = {item["field"]: item for item in response.json()["configuration"]}
    assert settings["max_pages"]["source"] == "50"
    assert settings["max_pages"]["current"] == "7"
    assert settings["max_pages"]["changed"] is True
    assert settings["retry_attempts"]["current"] == "0"
    assert "secret-user-agent" not in response.text


@pytest.mark.parametrize(
    "case, expected",
    [("no_source", 400), ("self", 400), ("missing", 404), ("running", 409), ("authenticated", 400)],
)
def test_unavailable_comparison_has_visible_error_and_no_scan_side_effect(
    browser, pair, case, expected
):
    client, service, dispatcher = browser
    run_id = pair[1]
    with session_scope() as session:
        run = session.get(ScanRun, run_id)
        if case == "no_source":
            run.rerun_of_run_id = None
        elif case == "self":
            run.rerun_of_run_id = run.id
        elif case == "missing":
            run_id = 999
        elif case == "running":
            run.status = "running"
        else:
            run.mode = "authenticated_coverage"
    assert client.get(f"/api/scans/{run_id}/comparison").status_code == expected
    page = client.get(f"/scans/{run_id}/comparison")
    assert page.status_code == expected
    assert 'role="alert"' in page.text
    assert dispatcher.submissions == []
    assert len(service.list_scans()) == 2


def test_empty_runs_do_not_imply_safety_and_keep_all_counts(browser):
    client, _, _ = browser
    with session_scope() as session:
        source = make_run(session, set())
        current = make_run(session, set(), source_id=source.id)
        run_id = current.id
    payload = client.get(f"/api/scans/{run_id}/comparison").json()
    assert payload["counts"] == {"new": 0, "persistent": 0, "not_observed": 0, "unknown": 0}
    assert "不代表目标安全" in client.get(f"/scans/{run_id}/comparison").text


@pytest.mark.parametrize("scheme", ["http", "HTTP"])
def test_evidence_projection_redacts_values_and_does_not_render_untrusted_markup(
    browser, pair, scheme
):
    client, _, _ = browser
    with session_scope() as session:
        current = session.get(ScanRun, pair[1])
        current.findings[0].title = "<img src=x onerror=alert(1)>"
        current.findings[0].summary = f"See {scheme}://127.0.0.1/?q=short-private-value"
        current.evidence[
            0
        ].source_url = "http://user:private-password@127.0.0.1/?q=private-query#private-fragment"
        current.evidence[0].summary = "Cookie: private-cookie"
        current.evidence[0].data = {
            "preview": "private-body",
            "headers": {"authorization": "private-auth"},
        }
    for path in (f"/api/scans/{pair[1]}/comparison", f"/scans/{pair[1]}/comparison"):
        response = client.get(path)
        assert response.status_code == 200
        for secret in (
            "short-private-value",
            "private-password",
            "private-query",
            "private-fragment",
            "private-cookie",
            "private-body",
            "private-auth",
        ):
            assert secret not in response.text
    assert "<img src=x onerror=alert(1)>" not in client.get(f"/scans/{pair[1]}/comparison").text


def test_real_pipeline_redacted_asset_urls_do_not_merge_different_query_values(tmp_path):
    from test_assessment_application import build_inline_service

    from app.schemas.assessment import AssessmentStartRequest

    service = build_inline_service(tmp_path)
    options = ScanOptions(request_timeout_seconds=3, overall_timeout_seconds=60, retry_attempts=0)
    before = service.start_scan("http://127.0.0.1/item?q=alpha-private", options)
    after = service.start_assessment(
        AssessmentStartRequest(
            url="http://127.0.0.1/item?q=beta-private",
            options=options,
            rerun_of_run_id=before.id,
        )
    )
    with session_scope() as session:
        old, new = session.get(ScanRun, before.id), session.get(ScanRun, after.id)
        assert old.assets[0].url == new.assets[0].url
        assert old.findings[0].dedup_key == new.findings[0].dedup_key
        assert old.evidence[0].source_url != new.evidence[0].source_url
    result = service.compare_with_source(after.id)
    assert result.counts == {"new": 0, "persistent": 0, "not_observed": 0, "unknown": 4}
    assert "alpha-private" not in result.model_dump_json()
    assert "beta-private" not in result.model_dump_json()


def test_same_run_wrong_asset_evidence_is_not_presented_as_proof(browser, pair):
    _, service, _ = browser
    with session_scope() as session:
        run = session.get(ScanRun, pair[1])
        finding = run.findings[0]
        wrong = next(item for item in run.evidence if item.asset_id not in finding.asset_ids)
        wrong.summary = "unrelated-evidence-summary"
        finding.evidence_ids = [wrong.id]
        finding_id = finding.id
    result = service.compare_with_source(pair[1])
    observation = next(
        item.after for item in result.items if item.after and item.after.id == finding_id
    )
    assert observation.evidence == []
    assert observation.evidence_missing is True


def test_multiple_precise_urls_collapsed_into_one_asset_are_unknown(browser, pair):
    _, service, _ = browser
    with session_scope() as session:
        run = session.get(ScanRun, pair[0])
        asset = run.assets[0]
        asset.url = "http://127.0.0.1/item?q=[REDACTED]"
        original = next(item for item in run.evidence if item.asset_id == asset.id)
        original.source_url = "http://127.0.0.1/item?q=alpha"
        session.add(
            ScanEvidence(
                scan_run_id=run.id,
                asset_id=asset.id,
                evidence_type="http_response",
                title="Response",
                summary="HTTP 200",
                source_url="http://127.0.0.1/item?q=beta",
                data={},
                collected_at=datetime.now(timezone.utc),
            )
        )
    result = service.compare_with_source(pair[1])
    assert result.comparable is False
    assert any(reason.code == "identity_unknown" for reason in result.reasons)
    assert result.counts["new"] == result.counts["not_observed"] == 0


@pytest.mark.parametrize("observed_after", [False, True])
def test_redacted_locations_cannot_prove_opposite_query_value_was_collected(
    browser, observed_after
):
    from app.services.coverage_identity import redacted_observed_url

    _, service, _ = browser
    with session_scope() as session:
        old_path, new_path = "item?q=alpha", "item?q=beta"
        before = make_run(session, {old_path}, paths=(old_path,))
        after = make_run(
            session, {new_path} if observed_after else set(), source_id=before.id, paths=(new_path,)
        )
        for run in (before, after):
            for asset in run.assets:
                asset.url = redacted_observed_url(asset.url)
            for finding in run.findings:
                finding.dedup_key = hashlib.sha256(
                    f"header|{run.assets[0].url}".encode()
                ).hexdigest()[:32]
        run_id = after.id
    result = service.compare_with_source(run_id)
    assert result.comparable is True
    assert result.counts == {
        "new": 0,
        "persistent": 0,
        "not_observed": 0,
        "unknown": 2 if observed_after else 1,
    }
