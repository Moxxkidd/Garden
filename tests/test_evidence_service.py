from pathlib import Path

from sqlalchemy import select

from app.db.bootstrap import session_scope
from app.models.audit_event import AuditEvent
from app.redaction.service import RedactionService
from app.services.findings import CheckRunService


def test_redaction_masks_sensitive_material() -> None:
    service = RedactionService()
    sample = (
        "Authorization: Bearer abcdef1234567890TOKEN\n"
        "Cookie: sessionid=abcdef1234567890\n"
        '{"email":"admin@example.local","password":"letmein","token":"supersecretvalue"}'
    )

    redacted = service.redact_text(sample)

    assert "admin@example.local" not in redacted
    assert "a***@example.local" in redacted
    assert "Authorization: ***" in redacted
    assert "Cookie: ***" in redacted
    assert 'password":"letmein' not in redacted
    assert 'token":"supersecretvalue' not in redacted


def test_http_header_redaction_preserves_safe_content_type_metadata() -> None:
    service = RedactionService()

    redacted = service.redact_http_headers(
        {
            "content-type": (
                "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
            ),
            "set-cookie": "session=secret-value",
        }
    )

    assert redacted["content-type"] == (
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    )
    assert redacted["set-cookie"] == "***"


def test_evidence_export_outputs_are_redacted(seeded_inventory, fake_evidence_service) -> None:
    check_service = CheckRunService(evidence_service=fake_evidence_service)
    with session_scope() as session:
        check_service.run_inventory_checks(session, seeded_inventory["inventory_run_id"])
        finding = check_service.finding_service.list(session)[0]
        json_result = fake_evidence_service.export_for_finding(
            session,
            finding_id=finding.id,
            export_format="json",
        )
        markdown_result = fake_evidence_service.export_for_finding(
            session,
            finding_id=finding.id,
            export_format="md",
        )

    json_output = Path(json_result.output_path).read_text(encoding="utf-8")
    markdown_output = Path(markdown_result.output_path).read_text(encoding="utf-8")

    assert "support@app.internal.local" not in json_output
    assert "support@app.internal.local" not in markdown_output
    assert "s***@app.internal.local" in json_output or "a***@app.internal.local" in json_output
    assert 'session":"current' not in json_output


def test_page_capture_stores_screenshot_metadata(seeded_inventory, fake_evidence_service) -> None:
    check_service = CheckRunService(evidence_service=fake_evidence_service)
    with session_scope() as session:
        check_service.run_inventory_checks(session, seeded_inventory["inventory_run_id"])
        evidence = next(
            item
            for item in fake_evidence_service.list(session)
            if item.evidence_type == "page_capture"
        )
        payload = fake_evidence_service.payload_for(evidence)

    screenshot_path = payload.get("screenshot", {}).get("path")
    assert screenshot_path
    assert Path(screenshot_path).exists()
    assert payload["screenshot"]["render_default"] is False


def test_evidence_export_records_audit_event(seeded_inventory, fake_evidence_service) -> None:
    check_service = CheckRunService(evidence_service=fake_evidence_service)
    with session_scope() as session:
        check_service.run_inventory_checks(session, seeded_inventory["inventory_run_id"])
        finding = check_service.finding_service.list(session)[0]
        fake_evidence_service.export_for_finding(
            session,
            finding_id=finding.id,
            export_format="json",
        )
        event = session.scalar(select(AuditEvent).order_by(AuditEvent.id.desc()))

    assert event is not None
    assert event.event_type == "evidence_export"
    assert event.status == "success"
    payload = event.detail_redacted
    assert payload["finding_id"] == finding.id
