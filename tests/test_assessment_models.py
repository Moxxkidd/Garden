"""统一验证运行模型与请求协议行为测试。"""

import json
from datetime import datetime, timezone

import pytest
from pydantic import ValidationError
from sqlalchemy.exc import IntegrityError

from app.db.bootstrap import get_session
from app.models.coverage_difference import CoverageDifference
from app.models.enums import (
    AssessmentMode,
    CompletenessStatus,
    ContextKind,
    ReplayVerdict,
)
from app.models.replay_execution import ReplayExecution
from app.models.scan_context import ScanContext
from app.models.scan_request import ScanRequest
from app.schemas.assessment import AssessmentRunView, AssessmentStartRequest
from tests.helpers.assessment import add_asset, add_request, make_authenticated_run, make_run


@pytest.fixture
def db_session():
    session = get_session()
    try:
        yield session
    finally:
        session.rollback()
        session.close()


def test_authenticated_assessment_has_exactly_three_contexts(db_session):
    run = make_run(db_session, mode="authenticated_coverage")
    db_session.add_all(
        [
            ScanContext(scan_run_id=run.id, kind="anonymous", status="pending"),
            ScanContext(scan_run_id=run.id, kind="user", status="pending"),
            ScanContext(scan_run_id=run.id, kind="admin", status="pending"),
        ]
    )

    db_session.commit()

    assert [item.kind for item in run.contexts] == ["anonymous", "user", "admin"]


def test_context_kind_is_unique_per_run(db_session):
    run = make_run(db_session)
    db_session.add_all(
        [
            ScanContext(scan_run_id=run.id, kind="user", status="pending"),
            ScanContext(scan_run_id=run.id, kind="user", status="pending"),
        ]
    )

    with pytest.raises(IntegrityError):
        db_session.commit()


@pytest.mark.parametrize("kind", ["operator", "auditor"])
def test_context_kind_rejects_values_outside_fixed_protocol(db_session, kind):
    run = make_run(db_session)
    db_session.add(ScanContext(scan_run_id=run.id, kind=kind, status="pending"))

    with pytest.raises(IntegrityError):
        db_session.commit()


def test_assessment_enums_publish_protocol_values():
    assert [item.value for item in AssessmentMode] == ["quick", "authenticated_coverage"]
    assert [item.value for item in ContextKind] == ["anonymous", "user", "admin"]
    assert [item.value for item in ReplayVerdict] == [
        "blocked",
        "equivalent_access",
        "changed_response",
        "inconclusive",
    ]
    assert CompletenessStatus.COMPLETE.value == "complete"


def test_quick_assessment_rejects_credential_profiles():
    with pytest.raises(ValidationError):
        AssessmentStartRequest(
            url="http://127.0.0.1:8000",
            mode="quick",
            user_profile_id=1,
        )


@pytest.mark.parametrize(
    ("user_profile_id", "admin_profile_id"),
    [(None, 2), (1, None), (None, None)],
)
def test_authenticated_assessment_requires_both_profiles(
    user_profile_id,
    admin_profile_id,
):
    with pytest.raises(ValidationError):
        AssessmentStartRequest(
            url="http://127.0.0.1:8000",
            mode="authenticated_coverage",
            user_profile_id=user_profile_id,
            admin_profile_id=admin_profile_id,
        )


def test_active_replay_requires_authorization_confirmation():
    with pytest.raises(ValidationError):
        AssessmentStartRequest(
            url="http://127.0.0.1:8000",
            mode="authenticated_coverage",
            user_profile_id=1,
            admin_profile_id=2,
            active_checks_enabled=True,
        )


def test_authorized_active_replay_is_accepted():
    request = AssessmentStartRequest(
        url="http://127.0.0.1:8000",
        mode="authenticated_coverage",
        user_profile_id=1,
        admin_profile_id=2,
        active_checks_enabled=True,
        authorization_confirmed=True,
    )

    assert request.active_checks_enabled is True
    assert request.authorization_confirmed is True


def test_assessment_run_view_rejects_unknown_context_count_keys():
    with pytest.raises(ValidationError):
        AssessmentRunView(
            id=1,
            mode="authenticated_coverage",
            completeness="complete",
            active_checks_enabled=False,
            contexts=[],
            context_counts={"anonymous": 1, "user": 1, "admin": 1, "operator": 1},
            difference_count=0,
            replay_count=0,
        )


def test_unified_run_relationships_persist_without_mapper_ambiguity(db_session):
    run = make_authenticated_run(db_session)
    anonymous, user, admin = run.contexts
    asset = add_asset(db_session, run, context_id=admin.id, identity_key="GET:/admin")
    request = add_request(db_session, admin)
    difference = CoverageDifference(
        scan_run_id=run.id,
        identity_key="GET:/admin",
        classification="admin_only",
        anonymous_state="absent",
        user_state="absent",
        admin_state="present",
        anonymous_present=False,
        user_present=False,
        admin_present=True,
        context_summaries={"admin": {"status_code": 200}},
        confidence="high",
        diagnostic="仅管理员上下文观察到该资产。",
    )
    replay = ReplayExecution(
        scan_run_id=run.id,
        source_request_id=request.id,
        source_context_id=admin.id,
        target_context_id=user.id,
        policy_snapshot={"allowed_methods": ["GET"]},
        policy_hash="policy-fingerprint",
        status="completed",
        redirects=[],
        response_summary_redacted={"status_code": 403},
        verdict="blocked",
        verdict_reason="目标上下文被拒绝。",
        started_at=datetime.now(timezone.utc),
        finished_at=datetime.now(timezone.utc),
    )
    db_session.add_all([difference, replay])

    db_session.commit()

    assert [context.kind for context in run.contexts] == ["anonymous", "user", "admin"]
    assert run.requests == [request]
    assert run.coverage_differences == [difference]
    assert run.replay_executions == [replay]
    assert admin.assets == [asset]
    assert admin.requests == [request]
    assert replay.source_context is admin
    assert replay.target_context is user
    assert anonymous.assets == []


def test_same_asset_url_can_be_persisted_once_per_context(db_session):
    run = make_authenticated_run(db_session)
    _anonymous, user, admin = run.contexts
    user_asset = add_asset(
        db_session,
        run,
        context_id=user.id,
        url="http://127.0.0.1:8000/account",
        identity_key="GET:/account",
    )
    admin_asset = add_asset(
        db_session,
        run,
        context_id=admin.id,
        url="http://127.0.0.1:8000/account",
        identity_key="GET:/account",
    )

    db_session.commit()

    assert user.assets == [user_asset]
    assert admin.assets == [admin_asset]


def test_source_run_relationship_is_unambiguous(db_session):
    source = make_run(db_session)
    derived = make_run(db_session, mode="authenticated_coverage")
    derived.source_run_id = source.id

    db_session.commit()

    assert derived.source_run is source
    assert source.derived_runs == [derived]


def test_scan_request_stores_only_redacted_request_data(db_session):
    run = make_authenticated_run(db_session)
    secret = "real-session-secret-42"
    request = ScanRequest.from_capture(
        scan_run_id=run.id,
        source_context_id=run.contexts[2].id,
        method="GET",
        raw_url=f"http://127.0.0.1:8000/items?token={secret}&id=7#fragment",
        header_names=["Authorization", "Cookie"],
        fingerprint="request-fingerprint",
        protected_storage_ref="vault://assessment/request/42",
    )
    db_session.add(request)

    db_session.commit()

    persisted = (
        db_session.execute(ScanRequest.__table__.select().where(ScanRequest.id == request.id))
        .mappings()
        .one()
    )
    ordinary_values = {
        key: value for key, value in persisted.items() if key != "protected_storage_ref"
    }
    assert request.normalized_redacted_url == (
        "http://127.0.0.1:8000/items?id=[REDACTED]&token=[REDACTED]"
    )
    assert "normalized_url" not in persisted
    assert "redacted_url" not in persisted
    assert secret not in json.dumps(ordinary_values, default=str)
    assert secret not in json.dumps(dict(persisted), default=str)
    assert request.fingerprint == "request-fingerprint"
    assert request.protected_storage_ref == "vault://assessment/request/42"


def test_scan_request_rejects_unredacted_query_values():
    with pytest.raises(ValueError, match="脱敏"):
        ScanRequest(
            scan_run_id=1,
            source_context_id=1,
            method="GET",
            normalized_redacted_url="http://app/items?token=real-secret",
            fingerprint="request-fingerprint",
            protected_storage_ref="vault://assessment/request/1",
        )


def test_scan_request_rejects_source_context_from_another_run(db_session):
    first = make_authenticated_run(db_session)
    second = make_authenticated_run(db_session)
    request = add_request(db_session, second.contexts[2])
    request.scan_run_id = first.id

    with pytest.raises(IntegrityError):
        db_session.commit()


def test_scan_request_rejects_asset_from_another_run(db_session):
    first = make_authenticated_run(db_session)
    second = make_authenticated_run(db_session)
    foreign_asset = add_asset(db_session, second, context_id=second.contexts[2].id)
    request = add_request(db_session, first.contexts[2])
    request.asset_id = foreign_asset.id

    with pytest.raises(IntegrityError):
        db_session.commit()


@pytest.mark.parametrize(
    "mismatch",
    ["source_request", "source_context", "target_context"],
)
def test_replay_execution_rejects_cross_run_links(db_session, mismatch):
    first = make_authenticated_run(db_session)
    second = make_authenticated_run(db_session)
    first_request = add_request(db_session, first.contexts[2])
    second_request = add_request(db_session, second.contexts[2])
    source_request = second_request if mismatch == "source_request" else first_request
    source_context = second.contexts[2] if mismatch == "source_context" else first.contexts[2]
    target_context = second.contexts[1] if mismatch == "target_context" else first.contexts[1]
    execution = ReplayExecution(
        scan_run_id=first.id,
        source_request_id=source_request.id,
        source_context_id=source_context.id,
        target_context_id=target_context.id,
        policy_snapshot={},
        policy_hash=f"policy-{mismatch}",
        status="pending",
        redirects=[],
        response_summary_redacted={},
    )
    db_session.add(execution)

    with pytest.raises(IntegrityError):
        db_session.commit()
