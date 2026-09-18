"""Reuse completed quick-run configuration through public service and HTTP boundaries."""

# Imported pytest fixture is intentionally injected by name.
# ruff: noqa: F811

import pytest
from test_scan_preview_pages import Inputs, browser  # noqa: F401

from app.db.bootstrap import session_scope
from app.models.scan_run import ScanRun
from app.schemas.assessment import AssessmentStartRequest
from app.schemas.scan import ScanOptions


def seed_run(**changes):
    values = dict(
        input_url="http://127.0.0.1:8080/?q=original",
        normalized_url="http://127.0.0.1:8080/?q=original",
        status="completed",
        current_stage="report",
        progress=100,
        options=ScanOptions(
            max_pages=7,
            max_depth=1,
            retry_attempts=0,
            request_timeout_seconds=3.5,
            overall_timeout_seconds=100,
        ).model_dump(),
    )
    values.update(changes)
    with session_scope() as session:
        run = ScanRun(**values)
        session.add(run)
        session.flush()
        return run.id


def test_reusing_configuration_creates_linked_run_without_changing_original(browser):
    client, service, dispatcher = browser
    original_id = seed_run()
    before = service.get_scan(original_id)
    configuration = service.get_reuse_configuration(original_id)
    assert configuration["options"]["retry_attempts"] == 0
    assert configuration["options"]["max_pages"] == 7
    new = service.start_assessment(
        AssessmentStartRequest(
            url=configuration["url"],
            rerun_of_run_id=original_id,
            options=ScanOptions(**{**configuration["options"], "max_pages": 8}),
        )
    )
    assert new.id != original_id
    assert new.rerun_of_run_id == original_id
    assert new.source_run_id is None
    assert service.get_scan(original_id) == before
    assert service.get_reuse_configuration(original_id) == configuration
    assert dispatcher.submissions == [new.id]
    assert client.get(f"/api/scans/{new.id}").json()["rerun_of_run_id"] == original_id


@pytest.mark.parametrize(
    "changes",
    [
        {"status": "running"},
        {"status": "queued"},
        {"mode": "authenticated_coverage"},
        {"active_checks_enabled": True},
    ],
)
def test_ineligible_run_cannot_be_reused_even_through_direct_service(browser, changes):
    from app.core.errors import InputValidationError

    _, service, dispatcher = browser
    run_id = seed_run(**changes)
    with pytest.raises(InputValidationError):
        service.get_reuse_configuration(run_id)
    with pytest.raises(InputValidationError):
        service.start_assessment(
            AssessmentStartRequest(
                url="http://127.0.0.1:8080/",
                rerun_of_run_id=run_id,
            )
        )
    assert len(service.list_scans()) == 1
    assert dispatcher.submissions == []


def test_nonexistent_reuse_source_is_rejected(browser):
    from app.core.errors import ResourceNotFoundError

    _, service, dispatcher = browser
    with pytest.raises(ResourceNotFoundError):
        service.start_assessment(
            AssessmentStartRequest(
                url="http://127.0.0.1/",
                rerun_of_run_id=999,
            )
        )
    assert dispatcher.submissions == []


def test_web_reuses_edits_previews_and_confirms_without_early_dispatch(browser):
    client, service, dispatcher = browser
    original_id = seed_run()
    detail = client.get(f"/scans/{original_id}")
    assert f'href="/scans/{original_id}/reuse"' in detail.text
    edit = client.get(f"/scans/{original_id}/reuse")
    assert edit.status_code == 200
    values = Inputs(edit.text).values
    assert values["max_pages"] == "7"
    assert values["retry_attempts"] == "0"
    assert values["rerun_of_run_id"] == str(original_id)
    assert "开始匿名扫描" not in edit.text
    values["max_pages"] = "8"
    preview = client.post("/scans/preview", data=values)
    assert preview.status_code == 200
    assert f"沿用任务 #{original_id}" in preview.text
    back = client.post("/scans/edit", data=Inputs(preview.text).values)
    assert Inputs(back.text).values["max_pages"] == "8"
    assert Inputs(back.text).values["rerun_of_run_id"] == str(original_id)
    assert len(service.list_scans()) == 1
    assert dispatcher.submissions == []
    response = client.post(
        "/scans/confirm", data=Inputs(preview.text).values, follow_redirects=False
    )
    assert response.status_code == 303
    new_id = int(response.headers["location"].rsplit("/", 1)[1])
    new = service.get_scan(new_id)
    assert new.rerun_of_run_id == original_id
    assert new.id != original_id
    assert f'href="/scans/{original_id}"' in client.get(f"/scans/{new_id}").text
    with session_scope() as session:
        assert session.get(ScanRun, new_id).options["max_pages"] == 8
        assert session.get(ScanRun, original_id).options["max_pages"] == 7
    repeated = client.post(
        "/scans/confirm", data=Inputs(preview.text).values, follow_redirects=False
    )
    assert repeated.headers["location"] == response.headers["location"]
    assert dispatcher.submissions == [new_id]


def test_legacy_missing_options_require_explicit_input_and_zero_is_preserved(browser):
    client, service, dispatcher = browser
    original_id = seed_run(
        options={"max_pages": 7, "max_depth": 0, "retry_attempts": 0, "secret_ref": "must-not-copy"}
    )
    edit = client.get(f"/scans/{original_id}/reuse")
    assert "must-not-copy" not in edit.text
    assert 'id="error-max_resources"' in edit.text
    values = Inputs(edit.text).values
    assert values["max_depth"] == "0"
    assert values["retry_attempts"] == "0"
    assert values["max_resources"] == ""
    preview = client.post("/scans/preview", data=values)
    assert preview.status_code == 422
    assert "开始匿名扫描" not in preview.text
    assert "历史配置" in preview.text
    values.update(
        max_resources="0",
        request_timeout_seconds="3",
        overall_timeout_seconds="90",
        max_redirects="0",
        user_agent="Garden-Test",
    )
    valid = client.post("/scans/preview", data=values)
    assert valid.status_code == 200
    assert "开始匿名扫描" in valid.text
    assert dispatcher.submissions == []
    assert len(service.list_scans()) == 1


@pytest.mark.parametrize(
    "change", ["id", "removed_id", "source_options", "source_status", "deleted"]
)
def test_confirmation_revalidates_source_and_rejects_changed_provenance(browser, change):
    client, service, dispatcher = browser
    original_id = seed_run()
    values = Inputs(client.get(f"/scans/{original_id}/reuse").text).values
    values = Inputs(client.post("/scans/preview", data=values).text).values
    if change == "id":
        values["rerun_of_run_id"] = str(seed_run())
    elif change == "removed_id":
        del values["rerun_of_run_id"]
    else:
        with session_scope() as session:
            source = session.get(ScanRun, original_id)
            if change == "source_options":
                source.options = {**source.options, "max_pages": 9}
            elif change == "source_status":
                source.status = "running"
            else:
                session.delete(source)
    response = client.post("/scans/confirm", data=values, follow_redirects=False)
    assert response.status_code in (409, 422)
    assert dispatcher.submissions == []


@pytest.mark.parametrize(
    "changes",
    [{"status": "running"}, {"mode": "authenticated_coverage"}, {"active_checks_enabled": True}],
)
def test_result_page_and_direct_reuse_link_reject_ineligible_runs(browser, changes):
    client, _, dispatcher = browser
    run_id = seed_run(**changes)
    assert f'href="/scans/{run_id}/reuse"' not in client.get(f"/scans/{run_id}").text
    response = client.get(f"/scans/{run_id}/reuse")
    assert response.status_code == 409
    assert 'role="alert"' in response.text
    assert dispatcher.submissions == []
