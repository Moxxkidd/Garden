from pathlib import Path

from sqlalchemy import select

from app.db.bootstrap import session_scope
from app.integrations.auth.http_adapter import HttpLoginAdapter
from app.models.audit_event import AuditEvent
from app.models.enums import FindingStatus
from app.services.finding_exports import FindingExportService
from app.services.findings import CheckRunService, FindingService
from app.services.inventory import InventoryBuildService
from app.services.reporting import ReportService
from app.services.retest import RetestService
from app.services.sessions import AuthSessionService


def test_finding_status_machine_validates_transitions(seeded_findings) -> None:
    service = FindingService()
    with session_scope() as session:
        finding = service.list(session)[0]
        assert finding.status == FindingStatus.NEW.value

        service.update_status(session, finding.id, FindingStatus.TRIAGED.value)
        service.update_status(session, finding.id, FindingStatus.ACCEPTED.value)
        service.update_status(session, finding.id, FindingStatus.FIXED.value)
        service.update_status(session, finding.id, FindingStatus.RETEST_PENDING.value)
        updated = service.update_status(session, finding.id, FindingStatus.CLOSED.value)

    assert updated.status == FindingStatus.CLOSED.value


def test_finding_status_machine_rejects_invalid_transition(seeded_findings) -> None:
    service = FindingService()
    with session_scope() as session:
        finding = service.list(session)[0]
        try:
            service.update_status(session, finding.id, FindingStatus.RETEST_PENDING.value)
        except Exception as error:  # noqa: BLE001
            message = str(error)
        else:
            raise AssertionError("Expected invalid transition to raise an error.")

    assert "cannot move" in message


def test_dedup_reopens_fixed_findings_when_issue_reappears(
    seeded_inventory, fake_evidence_service
) -> None:
    service = CheckRunService(evidence_service=fake_evidence_service)
    finding_service = FindingService()
    with session_scope() as session:
        service.run_inventory_checks(session, seeded_inventory["inventory_run_id"])
        finding = finding_service.list(session)[0]
        finding_service.update_status(session, finding.id, FindingStatus.TRIAGED.value)
        finding_service.update_status(session, finding.id, FindingStatus.ACCEPTED.value)
        finding_service.update_status(session, finding.id, FindingStatus.FIXED.value)
        count_before = len(finding_service.list(session))
        service.run_inventory_checks(session, seeded_inventory["inventory_run_id"])
        refreshed = finding_service.get(session, finding.id)
        count_after = len(finding_service.list(session))

    assert count_before == count_after
    assert refreshed.status == FindingStatus.TRIAGED.value


def test_retest_flow_updates_status_and_records_context(
    seeded_inventory,
    fake_evidence_service,
    fake_inventory_collection_service,
    auth_http_transport,
) -> None:
    finding_service = FindingService()
    session_service = AuthSessionService(
        http_adapter=HttpLoginAdapter(transport=auth_http_transport)
    )
    inventory_service = InventoryBuildService(
        auth_session_service=session_service,
        collection_service=fake_inventory_collection_service,
    )
    check_service = CheckRunService(
        inventory_service=inventory_service,
        session_service=session_service,
        evidence_service=fake_evidence_service,
        finding_service=finding_service,
    )
    retest_service = RetestService(
        finding_service=finding_service,
        session_service=session_service,
        inventory_service=inventory_service,
        check_service=check_service,
    )
    with session_scope() as session:
        check_service.run_inventory_checks(session, seeded_inventory["inventory_run_id"])
        finding = next(
            item
            for item in finding_service.list(session)
            if item.check_name == "administrative_surface"
        )
        finding_service.update_status(session, finding.id, FindingStatus.TRIAGED.value)
        finding_service.update_status(session, finding.id, FindingStatus.ACCEPTED.value)
        finding_service.update_status(session, finding.id, FindingStatus.FIXED.value)
        summary = retest_service.run(session, finding_id=finding.id)
        refreshed = finding_service.get(session, finding.id)

    assert summary.inventory_run_id is not None
    assert summary.scan_job_id is not None
    assert refreshed.retests
    assert summary.status in {"failed", "passed"}
    expected_status = (
        FindingStatus.TRIAGED.value if summary.status == "failed" else FindingStatus.CLOSED.value
    )
    assert refreshed.status == expected_status


def test_findings_and_report_exports_generate_files(seeded_findings) -> None:
    export_service = FindingExportService()
    report_service = ReportService()
    with session_scope() as session:
        finding_service = FindingService()
        findings = finding_service.list(session)
        breakdown = finding_service.status_breakdown(session)
        md_export = export_service.export_markdown(findings, status_breakdown=breakdown)
        json_export = export_service.export_json(findings, status_breakdown=breakdown)
        csv_export = export_service.export_csv(findings)
        report = report_service.generate(
            session,
            job_id=seeded_findings["check_summary"].scan_job_id,
            export_format="md",
        )
        audit_event = session.scalar(select(AuditEvent).order_by(AuditEvent.id.desc()))

    assert Path(md_export.output_path).exists()
    assert Path(json_export.output_path).exists()
    assert Path(csv_export.output_path).exists()
    assert Path(report.output_path).exists()
    assert "new" in Path(md_export.output_path).read_text(encoding="utf-8")
    assert audit_event is not None
    assert audit_event.event_type == "report_export"
    assert audit_event.status == "success"
