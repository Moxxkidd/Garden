from datetime import datetime, timezone

from fastapi.testclient import TestClient

from app.schemas.assessment import AssessmentRunView, CoverageDifferenceView


def _assessment_view() -> AssessmentRunView:
    return AssessmentRunView(
        id=31,
        mode="authenticated_coverage",
        input_url="http://127.0.0.1:8080/",
        normalized_url="http://127.0.0.1:8080/",
        status="queued",
        current_stage="queued",
        progress=0,
        retry_count=0,
        created_at=datetime.now(timezone.utc),
        completeness="pending",
        active_checks_enabled=False,
    )


class RecordingAssessmentService:
    def __init__(self) -> None:
        self.started = None

    def start_assessment(self, request):
        self.started = request
        return _assessment_view()

    def get_assessment(self, assessment_id):
        assert assessment_id == 31
        return _assessment_view()

    def list_coverage_differences(self, assessment_id):
        assert assessment_id == 31
        return [
            CoverageDifferenceView(
                id=7,
                identity_key="GET:/admin",
                classification="admin_only",
                anonymous_state="absent",
                user_state="absent",
                admin_state="present",
                anonymous_present=False,
                user_present=False,
                admin_present=True,
            )
        ]

    def cancel_assessment(self, assessment_id):
        assert assessment_id == 31
        return _assessment_view().model_copy(update={"status": "interrupted"})

    def read_report(self, assessment_id):
        assert assessment_id == 31
        return "# Garden 认证覆盖报告 #31\n"


def test_passive_assessment_http_start_and_read_interfaces(app) -> None:
    service = RecordingAssessmentService()
    with TestClient(app) as client:
        original_service = app.state.scan_service
        app.state.scan_service = service
        try:
            started = client.post(
                "/api/assessments",
                json={
                    "url": "http://127.0.0.1:8080/",
                    "source_run_id": 9,
                    "user_profile_id": 12,
                    "admin_profile_id": 13,
                    "options": {"max_pages": 8, "max_resources": 15},
                },
            )
            status = client.get("/api/assessments/31")
            differences = client.get("/api/assessments/31/differences")
            cancelled = client.post("/api/assessments/31/cancel")
            report = client.get("/api/assessments/31/report")
        finally:
            app.state.scan_service = original_service

    assert started.status_code == 202
    assert service.started.mode.value == "authenticated_coverage"
    assert service.started.active_checks_enabled is False
    assert service.started.authorization_confirmed is False
    assert service.started.source_run_id == 9
    assert service.started.options.max_pages == 8
    assert service.started.options.max_resources == 15
    assert status.status_code == 200
    assert differences.status_code == 200
    assert differences.json()[0]["classification"] == "admin_only"
    assert cancelled.json()["status"] == "interrupted"
    assert report.status_code == 200
    assert "Garden 认证覆盖报告" in report.text


def test_assessment_http_rejects_active_replay_fields_before_application_call(app) -> None:
    for forbidden_field in ("active_checks_enabled", "authorization_confirmed"):
        service = RecordingAssessmentService()
        with TestClient(app) as client:
            original_service = app.state.scan_service
            app.state.scan_service = service
            try:
                response = client.post(
                    "/api/assessments",
                    json={
                        "url": "http://127.0.0.1:8080/",
                        "user_profile_id": 12,
                        "admin_profile_id": 13,
                        forbidden_field: True,
                    },
                )
            finally:
                app.state.scan_service = original_service

        assert response.status_code == 422
        assert service.started is None
