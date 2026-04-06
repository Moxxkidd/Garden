"""Evidence capture, lookup, and export services."""

from __future__ import annotations

from collections import defaultdict
from typing import Any
from urllib.parse import urlparse

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.core.errors import ResourceNotFoundError
from app.integrations.evidence.playwright_capture import (
    PageEvidenceCaptureGatewayProtocol,
    SyncPlaywrightEvidenceCaptureGateway,
)
from app.models.enums import (
    AuditEventStatus,
    AuditEventType,
    EvidenceType,
    SessionType,
)
from app.models.evidence import Evidence
from app.models.finding import Finding
from app.models.inventory_endpoint import InventoryEndpoint
from app.models.inventory_page import InventoryPage
from app.models.inventory_parameter import InventoryParameter
from app.redaction.service import RedactionService
from app.schemas.evidence import EvidenceExportResult
from app.security_checks.base import CheckContext
from app.services.audit import AuditService
from app.services.evidence_export import EvidenceExportService
from app.services.evidence_storage import EvidenceStorageService
from app.services.session_storage import SessionStorageService


class EvidenceService:
    """Capture and browse redacted evidence linked to findings."""

    def __init__(
        self,
        *,
        storage_service: EvidenceStorageService | None = None,
        session_storage_service: SessionStorageService | None = None,
        export_service: EvidenceExportService | None = None,
        redaction_service: RedactionService | None = None,
        screenshot_gateway: PageEvidenceCaptureGatewayProtocol | None = None,
        audit_service: AuditService | None = None,
    ) -> None:
        self.storage_service = storage_service or EvidenceStorageService()
        self.session_storage_service = session_storage_service or SessionStorageService()
        self.export_service = export_service or EvidenceExportService(
            storage_service=self.storage_service
        )
        self.redaction_service = redaction_service or RedactionService()
        self.screenshot_gateway = screenshot_gateway or SyncPlaywrightEvidenceCaptureGateway()
        self.audit_service = audit_service or AuditService()

    def list(self, session: Session, *, finding_id: int | None = None) -> list[Evidence]:
        statement = (
            select(Evidence)
            .options(
                selectinload(Evidence.target),
                selectinload(Evidence.credential_profile),
                selectinload(Evidence.auth_session),
                selectinload(Evidence.scan_job),
                selectinload(Evidence.inventory_run),
                selectinload(Evidence.finding),
            )
            .order_by(Evidence.id.desc())
        )
        if finding_id is not None:
            statement = statement.where(Evidence.finding_id == finding_id)
        return list(session.scalars(statement))

    def get(self, session: Session, evidence_id: int) -> Evidence:
        statement = (
            select(Evidence)
            .where(Evidence.id == evidence_id)
            .options(
                selectinload(Evidence.target),
                selectinload(Evidence.credential_profile),
                selectinload(Evidence.auth_session),
                selectinload(Evidence.scan_job),
                selectinload(Evidence.inventory_run),
                selectinload(Evidence.finding),
            )
        )
        evidence = session.scalar(statement)
        if evidence is None:
            raise ResourceNotFoundError(f"Evidence {evidence_id} was not found.")
        return evidence

    def payload_for(self, evidence: Evidence) -> dict[str, Any]:
        payload = self.storage_service.read_payload(evidence.storage_ref)
        return self.redaction_service.redact_mapping(payload, limit=500)

    def capture_for_finding(
        self,
        session: Session,
        *,
        finding: Finding,
        context: CheckContext,
        job_id: int,
    ) -> list[Evidence]:
        inventory_run = context.inventory_run
        page_lookup = {page.id: page for page in context.pages}
        endpoint_lookup = {endpoint.id: endpoint for endpoint in context.endpoints}
        parameter_lookup = {parameter.id: parameter for parameter in context.parameters}
        evidence_records: list[Evidence] = []
        grouped_parameters = self._parameter_map(context.parameters)

        session_payload = self.session_storage_service.read_payload(
            context.auth_session.storage_ref
        )
        session_type = SessionType(context.auth_session.session_type)

        for ref in finding.inventory_refs:
            kind = ref.get("kind")
            if kind == "page":
                page = self._resolve_page(page_lookup, context.pages, ref)
                if page is None:
                    continue
                evidence_records.append(
                    self._upsert_page_capture(
                        session,
                        finding=finding,
                        context=context,
                        inventory_run_id=inventory_run.id,
                        page=page,
                        job_id=job_id,
                        session_payload=session_payload,
                        session_type=session_type,
                    )
                )
            elif kind == "endpoint":
                endpoint = self._resolve_endpoint(endpoint_lookup, context.endpoints, ref)
                if endpoint is None:
                    continue
                evidence_records.append(
                    self._upsert_http_exchange(
                        session,
                        finding=finding,
                        context=context,
                        inventory_run_id=inventory_run.id,
                        endpoint=endpoint,
                        job_id=job_id,
                        parameter_map=grouped_parameters.get(endpoint.id, {}),
                    )
                )
            elif kind == "parameter":
                parameter_id = ref.get("id")
                if parameter_id is None:
                    continue
                parameter = parameter_lookup.get(int(parameter_id))
                if parameter is None or parameter.inventory_endpoint_id is None:
                    continue
                endpoint = endpoint_lookup.get(parameter.inventory_endpoint_id)
                if endpoint is None:
                    continue
                evidence_records.append(
                    self._upsert_http_exchange(
                        session,
                        finding=finding,
                        context=context,
                        inventory_run_id=inventory_run.id,
                        endpoint=endpoint,
                        job_id=job_id,
                        parameter_map=grouped_parameters.get(endpoint.id, {}),
                    )
                )

        finding.evidence_refs = [
            {
                "id": evidence.id,
                "kind": evidence.evidence_type,
                "title": evidence.title,
                "url": evidence.url,
            }
            for evidence in evidence_records
        ]
        session.flush()
        return evidence_records

    def _resolve_page(
        self,
        page_lookup: dict[int, InventoryPage],
        pages: list[InventoryPage],
        ref: dict[str, Any],
    ) -> InventoryPage | None:
        page_id = ref.get("id")
        if page_id is not None:
            return page_lookup.get(int(page_id))
        subject_ref = str(ref.get("subject_ref", ""))
        if not subject_ref.startswith("page:"):
            return None
        target_path = subject_ref.removeprefix("page:")
        return next((page for page in pages if urlparse(page.url).path == target_path), None)

    def _resolve_endpoint(
        self,
        endpoint_lookup: dict[int, InventoryEndpoint],
        endpoints: list[InventoryEndpoint],
        ref: dict[str, Any],
    ) -> InventoryEndpoint | None:
        endpoint_id = ref.get("id")
        if endpoint_id is not None:
            return endpoint_lookup.get(int(endpoint_id))
        subject_ref = str(ref.get("subject_ref", ""))
        if not subject_ref.startswith("endpoint:"):
            return None
        _, method, path = subject_ref.split(":", maxsplit=2)
        return next(
            (
                endpoint
                for endpoint in endpoints
                if endpoint.method == method and endpoint.path == path
            ),
            None,
        )

    def export_for_finding(
        self,
        session: Session,
        *,
        finding_id: int,
        export_format: str,
    ) -> EvidenceExportResult:
        finding = self._get_finding(session, finding_id)
        evidences = self.list(session, finding_id=finding_id)
        if export_format == "json":
            result = self.export_service.export_json(finding, evidences)
        else:
            result = self.export_service.export_markdown(finding, evidences)
        self.audit_service.record(
            session,
            AuditEventType.EVIDENCE_EXPORT,
            AuditEventStatus.SUCCESS,
            {
                "finding_id": finding.id,
                "format": export_format,
                "evidence_count": len(evidences),
                "output_path": result.output_path,
            },
            target_id=finding.target_id,
            credential_profile_id=finding.credential_profile_id,
            auth_session_id=finding.session_id,
            scan_job_id=finding.job_id,
        )
        return result

    def _parameter_map(
        self,
        parameters: list[InventoryParameter],
    ) -> dict[int, dict[str, list[str]]]:
        grouped: dict[int, dict[str, list[str]]] = defaultdict(dict)
        for parameter in parameters:
            if parameter.inventory_endpoint_id is None:
                continue
            grouped.setdefault(parameter.inventory_endpoint_id, {}).setdefault(
                parameter.source_type,
                [],
            ).append(parameter.name)
        for endpoint_id, values in grouped.items():
            for source_type, names in values.items():
                grouped[endpoint_id][source_type] = sorted(set(names))
        return grouped

    def _upsert_page_capture(
        self,
        session: Session,
        *,
        finding: Finding,
        context: CheckContext,
        inventory_run_id: int,
        page: InventoryPage,
        job_id: int,
        session_payload: dict[str, Any],
        session_type: SessionType,
    ) -> Evidence:
        statement = select(Evidence).where(
            Evidence.finding_id == finding.id,
            Evidence.evidence_type == EvidenceType.PAGE_CAPTURE.value,
            Evidence.url == page.url,
        )
        evidence = session.scalar(statement)
        screenshot_path = self.storage_service.screenshot_path(evidence.id) if evidence else None
        if evidence is None:
            evidence = Evidence(
                target_id=context.target.id,
                credential_profile_id=context.credential_profile.id,
                session_id=context.auth_session.id,
                job_id=job_id,
                inventory_run_id=inventory_run_id,
                finding_id=finding.id,
                evidence_type=EvidenceType.PAGE_CAPTURE.value,
                title=f"Page capture: {page.title or page.url}",
                summary=f"Captured page metadata and screenshot for {page.url}",
                storage_ref="",
                preview_redacted="",
                url=page.url,
                page_title=page.title,
                http_method="GET",
                http_status=page.status_code,
            )
            session.add(evidence)
            session.flush()
            screenshot_path = self.storage_service.screenshot_path(evidence.id)

        artifact = self.screenshot_gateway.capture_page(
            base_url=context.target.base_url,
            url=page.url,
            session_type=session_type,
            session_payload=session_payload,
            output_path=screenshot_path or self.storage_service.screenshot_path(evidence.id),
        )
        preview = self.redaction_service.redact_text(page.text_preview or "")
        payload = {
            "page": {
                "url": artifact.final_url,
                "page_title": artifact.page_title or page.title,
                "http_status": artifact.status_code or page.status_code,
                "cache_control": page.cache_control,
                "preview_redacted": preview,
            },
            "screenshot": {
                "path": artifact.screenshot_path,
                "render_default": False,
            },
        }
        evidence.title = f"Page capture: {artifact.page_title or page.url}"
        evidence.summary = f"Captured redacted page preview and screenshot for {page.url}"
        evidence.preview_redacted = (
            preview or "Screenshot stored separately; preview redacted by default."
        )
        evidence.url = artifact.final_url
        evidence.page_title = artifact.page_title or page.title
        evidence.http_status = artifact.status_code or page.status_code
        evidence.storage_ref = self.storage_service.write_payload(evidence.id, payload)
        session.flush()
        return evidence

    def _upsert_http_exchange(
        self,
        session: Session,
        *,
        finding: Finding,
        context: CheckContext,
        inventory_run_id: int,
        endpoint: InventoryEndpoint,
        job_id: int,
        parameter_map: dict[str, list[str]],
    ) -> Evidence:
        statement = select(Evidence).where(
            Evidence.finding_id == finding.id,
            Evidence.evidence_type == EvidenceType.HTTP_EXCHANGE.value,
            Evidence.url == endpoint.url,
            Evidence.http_method == endpoint.method,
        )
        evidence = session.scalar(statement)
        if evidence is None:
            evidence = Evidence(
                target_id=context.target.id,
                credential_profile_id=context.credential_profile.id,
                session_id=context.auth_session.id,
                job_id=job_id,
                inventory_run_id=inventory_run_id,
                finding_id=finding.id,
                evidence_type=EvidenceType.HTTP_EXCHANGE.value,
                title=f"HTTP exchange: {endpoint.method} {endpoint.path}",
                summary=f"Captured request/response metadata for {endpoint.method} {endpoint.path}",
                storage_ref="",
                preview_redacted="",
                url=endpoint.url,
                page_title=None,
                http_method=endpoint.method,
                http_status=(
                    endpoint.status_codes_observed[0] if endpoint.status_codes_observed else None
                ),
            )
            session.add(evidence)
            session.flush()

        request_preview = self.redaction_service.redact_text(
            self._request_body_preview(parameter_map),
        )
        response_preview = self.redaction_service.redact_text(endpoint.response_preview or "")
        payload = {
            "request": {
                "method": endpoint.method,
                "url": endpoint.url,
                "path": endpoint.path,
                "parameter_names": parameter_map,
                "body_preview_redacted": request_preview,
            },
            "response": {
                "status_codes": endpoint.status_codes_observed,
                "cache_control": endpoint.cache_control,
                "content_type": endpoint.content_type,
                "set_cookie_names": endpoint.set_cookie_names,
                "cookie_issue_flags": endpoint.cookie_issue_flags,
                "body_preview_redacted": response_preview,
            },
        }
        evidence.summary = (
            f"Captured redacted request/response metadata for {endpoint.method} {endpoint.path}"
        )
        evidence.preview_redacted = response_preview or request_preview or "No preview available."
        evidence.http_status = (
            endpoint.status_codes_observed[0] if endpoint.status_codes_observed else None
        )
        evidence.storage_ref = self.storage_service.write_payload(evidence.id, payload)
        session.flush()
        return evidence

    def _request_body_preview(self, parameter_map: dict[str, list[str]]) -> str:
        fragments: list[str] = []
        for source_type, names in sorted(parameter_map.items()):
            fragments.append(f"{source_type} fields: {', '.join(names)}")
        return "; ".join(fragments)

    def _get_finding(self, session: Session, finding_id: int) -> Finding:
        statement = select(Finding).where(Finding.id == finding_id)
        finding = session.scalar(statement)
        if finding is None:
            raise ResourceNotFoundError(f"Finding {finding_id} was not found.")
        return finding
