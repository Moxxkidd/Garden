from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

import httpx
import pytest

from app.core.errors import InputValidationError
from app.core.settings import get_settings
from app.db.bootstrap import get_session
from app.models.credential_profile import CredentialProfile
from app.models.scan_run import ScanRun
from app.models.target import Target
from app.schemas.assessment import AssessmentStartRequest
from app.schemas.scan import ScanOptions
from app.services.scan_application import InlineScanDispatcher, ScanApplicationService
from app.services.scan_network import HttpScanGateway, TargetNetworkPolicy
from app.services.scan_pipeline import ScanPipeline
from app.services.scan_reporting import ScanReportService


def _loopback_resolver(host, port, **kwargs):
    return [(2, 1, 6, "", ("127.0.0.1", port))]


def build_inline_service(tmp_path) -> ScanApplicationService:
    settings = get_settings()
    policy = TargetNetworkPolicy(settings, resolver=_loopback_resolver)
    gateway = HttpScanGateway(
        policy,
        transport=httpx.MockTransport(
            lambda request: httpx.Response(
                200,
                headers={"content-type": "text/html"},
                text="<html><title>Assessment fixture</title></html>",
            )
        ),
    )
    return ScanApplicationService(
        settings=settings,
        pipeline=ScanPipeline(
            policy=policy,
            gateway=gateway,
            report_service=ScanReportService(tmp_path / "reports"),
        ),
        dispatcher=InlineScanDispatcher(),
    )


class HoldingDispatcher:
    def __init__(self) -> None:
        self.ids: list[int] = []

    def submit(self, scan_run_id: int, callback) -> None:
        self.ids.append(scan_run_id)


def build_holding_service(tmp_path) -> tuple[ScanApplicationService, HoldingDispatcher]:
    settings = get_settings()
    policy = TargetNetworkPolicy(settings, resolver=_loopback_resolver)
    dispatcher = HoldingDispatcher()
    return (
        ScanApplicationService(
            settings=settings,
            pipeline=ScanPipeline(
                policy=policy,
                gateway=HttpScanGateway(policy),
                report_service=ScanReportService(tmp_path / "reports"),
            ),
            dispatcher=dispatcher,
        ),
        dispatcher,
    )


@dataclass(frozen=True)
class AssessmentProfiles:
    url: str
    target_id: int
    user_id: int
    admin_id: int
    alternate_user_id: int


@pytest.fixture
def assessment_profiles() -> AssessmentProfiles:
    with get_session() as session:
        target = Target(
            name="assessment-target",
            base_url="http://127.0.0.1:8080/",
            type="web",
            owner="security",
            tags=[],
            status="active",
        )
        session.add(target)
        session.flush()
        profiles = [
            CredentialProfile(
                target_id=target.id,
                name=name,
                role=role,
                auth_type="password",
                username=name,
                secret_ref=f"env://{name}",
                login_config_path=f"configs/{name}.yaml",
            )
            for name, role in [
                ("user", "user"),
                ("admin", "admin"),
                ("alternate-user", "user"),
            ]
        ]
        session.add_all(profiles)
        session.commit()
        return AssessmentProfiles(
            url=target.base_url,
            target_id=target.id,
            user_id=profiles[0].id,
            admin_id=profiles[1].id,
            alternate_user_id=profiles[2].id,
        )


@pytest.fixture
def inline_service(tmp_path) -> ScanApplicationService:
    return build_inline_service(tmp_path)


@pytest.fixture
def completed_quick_run(inline_service, assessment_profiles):
    return inline_service.start_assessment(
        AssessmentStartRequest(
            url=assessment_profiles.url,
            mode="quick",
            target_id=assessment_profiles.target_id,
        )
    )


def test_start_scan_delegates_to_quick_assessment(tmp_path):
    service = build_inline_service(tmp_path)

    view = service.start_scan("http://127.0.0.1:8080/")

    assert view.mode == "quick"
    assert [context.kind for context in view.contexts] == ["anonymous"]


def test_start_assessment_creates_three_contexts(assessment_profiles, inline_service):
    request = AssessmentStartRequest(
        url=assessment_profiles.url,
        mode="authenticated_coverage",
        user_profile_id=assessment_profiles.user_id,
        admin_profile_id=assessment_profiles.admin_id,
    )

    view = inline_service.start_assessment(request)

    assert [item.kind for item in view.contexts] == ["anonymous", "user", "admin"]
    assert [stage.name for stage in view.stages] == [
        "validate",
        "establish_contexts",
        "collect",
        "normalize",
        "compare_coverage",
        "replay_authorization",
        "analyze",
        "report",
    ]
    assert view.status == "queued"


def test_authenticated_assessment_can_reference_quick_source_run(
    inline_service, completed_quick_run, assessment_profiles
):
    view = inline_service.start_assessment(
        AssessmentStartRequest(
            url=completed_quick_run.normalized_url,
            mode="authenticated_coverage",
            source_run_id=completed_quick_run.id,
            user_profile_id=assessment_profiles.user_id,
            admin_profile_id=assessment_profiles.admin_id,
        )
    )

    assert view.source_run_id == completed_quick_run.id
    assert view.asset_count == 0


def test_authenticated_assessments_keep_distinct_quick_source_runs(
    inline_service, completed_quick_run, assessment_profiles
):
    second_quick_run = inline_service.start_assessment(
        AssessmentStartRequest(
            url=assessment_profiles.url,
            mode="quick",
            target_id=assessment_profiles.target_id,
        )
    )

    first_upgrade = inline_service.start_assessment(
        AssessmentStartRequest(
            url=assessment_profiles.url,
            mode="authenticated_coverage",
            source_run_id=completed_quick_run.id,
            user_profile_id=assessment_profiles.user_id,
            admin_profile_id=assessment_profiles.admin_id,
        )
    )
    second_upgrade = inline_service.start_assessment(
        AssessmentStartRequest(
            url=assessment_profiles.url,
            mode="authenticated_coverage",
            source_run_id=second_quick_run.id,
            user_profile_id=assessment_profiles.user_id,
            admin_profile_id=assessment_profiles.admin_id,
        )
    )

    assert completed_quick_run.id != second_quick_run.id
    assert first_upgrade.id != second_upgrade.id
    assert first_upgrade.source_run_id == completed_quick_run.id
    assert second_upgrade.source_run_id == second_quick_run.id


def test_source_run_must_be_terminal_quick_run_for_same_target(tmp_path, assessment_profiles):
    service, _dispatcher = build_holding_service(tmp_path)
    queued_quick = service.start_assessment(
        AssessmentStartRequest(
            url=assessment_profiles.url,
            mode="quick",
            target_id=assessment_profiles.target_id,
        )
    )

    with pytest.raises(InputValidationError, match="终态 quick"):
        service.start_assessment(
            AssessmentStartRequest(
                url=assessment_profiles.url,
                mode="authenticated_coverage",
                source_run_id=queued_quick.id,
                user_profile_id=assessment_profiles.user_id,
                admin_profile_id=assessment_profiles.admin_id,
            )
        )


def test_source_run_rejects_terminal_quick_run_from_another_target(
    inline_service, completed_quick_run, assessment_profiles
):
    with get_session() as session:
        other_target = Target(
            name="other-assessment-target",
            base_url="http://127.0.0.1:9090/",
            type="web",
            owner="security",
            tags=[],
            status="active",
        )
        session.add(other_target)
        session.flush()
        source = session.get(ScanRun, completed_quick_run.id)
        source.target_id = other_target.id
        session.commit()

    with pytest.raises(InputValidationError, match="同一 target"):
        inline_service.start_assessment(
            AssessmentStartRequest(
                url=assessment_profiles.url,
                mode="authenticated_coverage",
                source_run_id=completed_quick_run.id,
                user_profile_id=assessment_profiles.user_id,
                admin_profile_id=assessment_profiles.admin_id,
            )
        )


def test_source_run_rejects_terminal_authenticated_assessment(inline_service, assessment_profiles):
    source = inline_service.start_assessment(
        AssessmentStartRequest(
            url=assessment_profiles.url,
            mode="authenticated_coverage",
            user_profile_id=assessment_profiles.user_id,
            admin_profile_id=assessment_profiles.admin_id,
        )
    )
    with get_session() as session:
        run = session.get(ScanRun, source.id)
        run.status = "incomplete"
        run.active_key = None
        session.commit()

    with pytest.raises(InputValidationError, match="终态 quick"):
        inline_service.start_assessment(
            AssessmentStartRequest(
                url=assessment_profiles.url,
                mode="authenticated_coverage",
                source_run_id=source.id,
                user_profile_id=assessment_profiles.user_id,
                admin_profile_id=assessment_profiles.admin_id,
            )
        )


def test_active_key_distinguishes_mode_profiles_active_flag_and_full_options(
    tmp_path, assessment_profiles
):
    service, dispatcher = build_holding_service(tmp_path)
    base = service.start_assessment(
        AssessmentStartRequest(
            url=assessment_profiles.url,
            mode="authenticated_coverage",
            user_profile_id=assessment_profiles.user_id,
            admin_profile_id=assessment_profiles.admin_id,
        )
    )
    duplicate = service.start_assessment(
        AssessmentStartRequest(
            url=assessment_profiles.url,
            mode="authenticated_coverage",
            user_profile_id=assessment_profiles.user_id,
            admin_profile_id=assessment_profiles.admin_id,
        )
    )
    changed_profile = service.start_assessment(
        AssessmentStartRequest(
            url=assessment_profiles.url,
            mode="authenticated_coverage",
            user_profile_id=assessment_profiles.alternate_user_id,
            admin_profile_id=assessment_profiles.admin_id,
        )
    )
    changed_active_flag = service.start_assessment(
        AssessmentStartRequest(
            url=assessment_profiles.url,
            mode="authenticated_coverage",
            user_profile_id=assessment_profiles.user_id,
            admin_profile_id=assessment_profiles.admin_id,
            active_checks_enabled=True,
            authorization_confirmed=True,
        )
    )
    changed_options = service.start_assessment(
        AssessmentStartRequest(
            url=assessment_profiles.url,
            mode="authenticated_coverage",
            user_profile_id=assessment_profiles.user_id,
            admin_profile_id=assessment_profiles.admin_id,
            options=ScanOptions(max_depth=3),
        )
    )
    quick = service.start_assessment(
        AssessmentStartRequest(url=assessment_profiles.url, mode="quick")
    )

    assert duplicate.id == base.id
    assert (
        len({base.id, changed_profile.id, changed_active_flag.id, changed_options.id, quick.id})
        == 5
    )
    assert dispatcher.ids == [quick.id]


@pytest.mark.parametrize(
    "terminal_status",
    ["completed", "completed_with_warnings", "failed", "incomplete", "interrupted"],
)
def test_committing_new_terminal_status_clears_active_key_and_allows_retry(
    tmp_path, terminal_status
):
    service, _dispatcher = build_holding_service(tmp_path)
    view = service.start_scan("http://127.0.0.1:8080/")
    with get_session() as session:
        run = session.get(ScanRun, view.id)
        run.status = terminal_status
        run.finished_at = datetime.now(timezone.utc)
        session.commit()

    retry = service.start_scan("http://127.0.0.1:8080/")

    assert retry.id != view.id
    with get_session() as session:
        assert session.get(ScanRun, view.id).active_key is None
