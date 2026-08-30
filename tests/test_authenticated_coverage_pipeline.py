from pathlib import Path

import pytest

from app.core.settings import get_settings
from app.db.bootstrap import get_session
from app.models.credential_profile import CredentialProfile
from app.models.replay_execution import ReplayExecution
from app.models.target import Target
from app.schemas.scan import ScanOptions
from app.services.context_collection import ContextCollectionSummary
from app.services.ephemeral_secret_store import EphemeralSecretStore
from app.services.scan_application import create_assessment_stages
from app.services.scan_network import HttpScanGateway, TargetNetworkPolicy
from app.services.scan_pipeline import ScanPipeline
from app.services.scan_reporting import ScanReportService
from tests.helpers.assessment import add_asset, make_authenticated_run


@pytest.fixture
def db_session():
    session = get_session()
    try:
        yield session
    finally:
        session.rollback()
        session.close()


def _loopback_resolver(host, port, **kwargs):
    return [(2, 1, 6, "", ("127.0.0.1", port))]


class ReadyContextService:
    def establish(self, session, run, *, before_request=None):
        for context in run.contexts:
            context.status = "ready"
            context.login_status = "not_applicable" if context.kind == "anonymous" else "succeeded"
            context.session_validation_status = (
                "not_applicable" if context.kind == "anonymous" else "valid"
            )
        session.flush()
        return list(run.contexts)


class CompleteContextCollectionService:
    def collect(self, session, run, context, *, before_request=None):
        add_asset(
            session,
            run,
            context_id=context.id,
            identity_key="GET:/shared",
            url="http://127.0.0.1:8080/shared",
            status_code=200,
            title="Shared",
            attributes={
                "content_type": "text/html",
                "content_signature": "same-signature",
            },
        )
        context.status = "completed"
        context.collection_status = "completed"
        context.completeness = "complete"
        context.asset_count = 1
        session.flush()
        return ContextCollectionSummary(
            context_id=context.id,
            asset_count=1,
            request_count=0,
            failure_count=0,
        )


class GuardedReadyContextService(ReadyContextService):
    def __init__(self) -> None:
        self.guard_calls = 0

    def establish(self, session, run, *, before_request=None):
        assert before_request is not None
        before_request()
        self.guard_calls += 1
        return super().establish(session, run)


class GuardedCompleteContextCollectionService(CompleteContextCollectionService):
    def __init__(self) -> None:
        self.guard_calls = 0

    def collect(self, session, run, context, *, before_request=None):
        assert before_request is not None
        before_request()
        self.guard_calls += 1
        return super().collect(session, run, context)


class MissingUserContextService(ReadyContextService):
    def establish(self, session, run, *, before_request=None):
        contexts = super().establish(session, run, before_request=before_request)
        user = next(context for context in contexts if context.kind == "user")
        user.status = "failed"
        user.login_status = "failed"
        user.session_validation_status = "invalid"
        user.collection_status = "failed"
        user.completeness = "incomplete"
        user.error_code = "authentication_session_unavailable"
        user.error_message = "Authentication session could not be established and validated."
        run.status = "incomplete"
        run.completeness = "missing_user_context"
        run.error_code = "context_establishment_failed"
        run.error_message = "One or more required authentication contexts failed."
        session.flush()
        return contexts


def _pipeline(
    tmp_path: Path,
    *,
    context_service=None,
    collection_service=None,
    comparison_service=None,
    secret_store=None,
):
    policy = TargetNetworkPolicy(get_settings(), resolver=_loopback_resolver)
    return ScanPipeline(
        policy=policy,
        gateway=HttpScanGateway(policy),
        context_service=context_service or ReadyContextService(),
        context_collection_service=collection_service or CompleteContextCollectionService(),
        coverage_comparison_service=comparison_service,
        ephemeral_secret_store=secret_store,
        report_service=ScanReportService(tmp_path / "reports"),
    )


def _queued_authenticated_run(db_session):
    run = make_authenticated_run(db_session)
    run.input_url = "http://127.0.0.1:8080/"
    run.normalized_url = run.input_url
    run.options = ScanOptions().model_dump()
    create_assessment_stages(db_session, run)
    db_session.flush()
    return run


def test_authenticated_pipeline_completes_all_passive_stages(db_session, tmp_path) -> None:
    run = _queued_authenticated_run(db_session)

    _pipeline(tmp_path).execute_authenticated(db_session, run.id)

    db_session.refresh(run)
    assert run.status == "completed", (
        run.error_message,
        {stage.name: stage.status for stage in run.stages},
    )
    assert run.active_checks_enabled is False
    assert run.report_path is not None
    assert Path(run.report_path).exists()
    assert {stage.name: stage.status for stage in run.stages} == {
        "validate": "completed",
        "establish_contexts": "completed",
        "collect": "completed",
        "normalize": "completed",
        "compare_coverage": "completed",
        "replay_authorization": "skipped",
        "analyze": "completed",
        "report": "completed",
    }
    replay_stage = next(stage for stage in run.stages if stage.name == "replay_authorization")
    assert "v0.3" in replay_stage.summary
    assert "被动" in replay_stage.summary
    assert db_session.query(ReplayExecution).filter_by(scan_run_id=run.id).count() == 0


def test_authenticated_pipeline_guards_establishment_and_every_context_collection(
    db_session,
    tmp_path,
) -> None:
    run = _queued_authenticated_run(db_session)
    context_service = GuardedReadyContextService()
    collection_service = GuardedCompleteContextCollectionService()

    _pipeline(
        tmp_path,
        context_service=context_service,
        collection_service=collection_service,
    ).execute_authenticated(db_session, run.id)

    assert context_service.guard_calls == 1
    assert collection_service.guard_calls == 3


def test_failed_user_context_continues_with_unknown_and_report(db_session, tmp_path) -> None:
    run = _queued_authenticated_run(db_session)
    run.active_key = "partial-context-active-key"
    db_session.flush()

    _pipeline(tmp_path, context_service=MissingUserContextService()).execute_authenticated(
        db_session, run.id
    )

    db_session.refresh(run)
    assert run.status == "incomplete"
    assert run.completeness == "missing_user_context"
    assert run.active_key is None
    assert run.report_path is not None
    assert Path(run.report_path).exists()
    assert len(run.coverage_differences) == 1
    difference = run.coverage_differences[0]
    assert difference.user_state == "unknown"
    assert difference.user_present is None
    assert difference.classification == "unknown"
    assert next(stage for stage in run.stages if stage.name == "report").status == "completed"


class FailingComparisonService:
    def __init__(self, secret: str) -> None:
        self.secret = secret

    def compare(self, session, run):
        raise RuntimeError(f"comparison exploded with {self.secret}")


def test_fatal_comparison_cleans_secrets_skips_later_stages_and_writes_safe_diagnostic(
    db_session,
    tmp_path,
) -> None:
    raw_secret = "never-persist-this-comparison-secret"
    secret_store = EphemeralSecretStore(tmp_path / "secrets")
    target = Target(
        name="fatal-target",
        base_url="http://127.0.0.1:8080/",
        type="web",
        owner="appsec",
        tags=[],
        status="active",
    )
    db_session.add(target)
    db_session.flush()
    user_ref = secret_store.write(raw_secret)
    admin_ref = secret_store.write(f"admin-{raw_secret}")
    profiles = [
        CredentialProfile(
            target_id=target.id,
            name=f"fatal-{role}",
            role=role,
            auth_type="password",
            username=role,
            secret_ref=reference,
            login_config_path="inline://unused-by-orchestration-test",
        )
        for role, reference in (("user", user_ref), ("admin", admin_ref))
    ]
    db_session.add_all(profiles)
    db_session.flush()
    run = _queued_authenticated_run(db_session)
    run.target_id = target.id
    run.active_key = "fatal-comparison-active-key"
    for context in run.contexts:
        if context.kind == "user":
            context.credential_profile_id = profiles[0].id
        elif context.kind == "admin":
            context.credential_profile_id = profiles[1].id
    db_session.flush()

    _pipeline(
        tmp_path,
        comparison_service=FailingComparisonService(raw_secret),
        secret_store=secret_store,
    ).execute_authenticated(db_session, run.id)

    db_session.refresh(run)
    stages = {stage.name: stage for stage in run.stages}
    assert run.status == "failed"
    assert run.active_key is None
    assert stages["compare_coverage"].status == "failed"
    assert stages["replay_authorization"].status == "skipped"
    assert stages["analyze"].status == "skipped"
    assert stages["report"].status == "completed"
    assert run.report_path is not None
    report_text = Path(run.report_path).read_text(encoding="utf-8")
    persisted_text = " ".join(
        filter(
            None,
            [
                run.error_message,
                stages["compare_coverage"].error_message,
                report_text,
            ],
        )
    )
    assert raw_secret not in persisted_text
    assert list(secret_store.root.glob("*.secret")) == []
