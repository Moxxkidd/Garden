from app.db.bootstrap import session_scope
from app.services.evidence import EvidenceService
from app.services.findings import CheckRunService, FindingService


def test_check_run_generates_multiple_findings(seeded_inventory, fake_evidence_service) -> None:
    service = CheckRunService(evidence_service=fake_evidence_service)
    with session_scope() as session:
        summary = service.run_inventory_checks(session, seeded_inventory["inventory_run_id"])
        findings = service.finding_service.list(session)
        evidence_records = EvidenceService(
            storage_service=fake_evidence_service.storage_service,
            session_storage_service=fake_evidence_service.session_storage_service,
            screenshot_gateway=fake_evidence_service.screenshot_gateway,
        ).list(session)

    assert summary.total_findings >= 6
    assert summary.findings_created >= 6
    assert evidence_records
    categories = {finding.category for finding in findings}
    assert "debug_error_leakage" in categories
    assert "authenticated_headers_cache" in categories
    assert "administrative_surface" in categories
    assert "idor_indicators" in categories
    assert "upload_import_entrypoints" in categories
    assert "sensitive_information_disclosure" in categories


def test_check_run_updates_existing_findings_instead_of_duplicating(
    seeded_inventory,
    fake_evidence_service,
) -> None:
    service = CheckRunService(evidence_service=fake_evidence_service)
    finding_service = FindingService()
    with session_scope() as session:
        first_summary = service.run_inventory_checks(session, seeded_inventory["inventory_run_id"])
        first_count = len(finding_service.list(session))
        second_summary = service.run_inventory_checks(session, seeded_inventory["inventory_run_id"])
        second_count = len(finding_service.list(session))

    assert first_summary.findings_created >= 6
    assert second_summary.findings_updated >= 6
    assert first_count == second_count


def test_severity_and_confidence_mapping_is_stable(seeded_inventory, fake_evidence_service) -> None:
    service = CheckRunService(evidence_service=fake_evidence_service)
    with session_scope() as session:
        service.run_inventory_checks(session, seeded_inventory["inventory_run_id"])
        findings = {finding.category: finding for finding in service.finding_service.list(session)}

    assert findings["debug_error_leakage"].severity == "medium"
    assert findings["debug_error_leakage"].confidence == "medium"
    assert findings["administrative_surface"].severity == "low"
    assert findings["administrative_surface"].confidence == "high"
    assert findings["idor_indicators"].severity == "medium"
    assert findings["idor_indicators"].confidence == "low"


def test_findings_link_to_redacted_evidence(seeded_inventory, fake_evidence_service) -> None:
    service = CheckRunService(evidence_service=fake_evidence_service)
    with session_scope() as session:
        service.run_inventory_checks(session, seeded_inventory["inventory_run_id"])
        finding = service.finding_service.list(session)[0]
        evidences = fake_evidence_service.list(session, finding_id=finding.id)
        evidence = next(
            (
                item
                for item in evidences
                if "@app.internal.local" in item.preview_redacted
                or "@example.local" in item.preview_redacted
            ),
            evidences[0],
        )
        payload = fake_evidence_service.payload_for(evidence)

    assert finding.evidence_refs
    assert evidence.finding_id == finding.id
    assert "support@app.internal.local" not in evidence.preview_redacted
    assert "support@app.internal.local" not in str(payload)
    assert (
        "@app.internal.local" in evidence.preview_redacted
        or "@example.local" in evidence.preview_redacted
    )
