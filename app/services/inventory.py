"""Inventory build, persistence, and lookup services."""

from __future__ import annotations

from collections.abc import Iterable
from datetime import datetime, timezone
from urllib.parse import urlparse, urlunparse

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.core.errors import ConflictError, ResourceNotFoundError
from app.integrations.inventory.playwright_gateway import (
    InventoryCollectionResult,
    ObservedEndpoint,
    ObservedPage,
)
from app.models.enums import InventoryRunStatus, JobStatus
from app.models.inventory_annotation import InventoryAnnotation
from app.models.inventory_endpoint import InventoryEndpoint
from app.models.inventory_page import InventoryPage
from app.models.inventory_parameter import InventoryParameter
from app.models.inventory_run import InventoryRun
from app.schemas.inventory import InventoryBuildControls, InventoryBuildSummary, InventoryCounts
from app.schemas.job import ScanJobCreate
from app.services.credentials import CredentialProfileService
from app.services.inventory_annotations import AnnotationCandidate, InventoryAnnotationService
from app.services.inventory_collection import InventoryCollectionService
from app.services.inventory_export import InventoryExportService
from app.services.jobs import ScanJobService
from app.services.sessions import AuthSessionService
from app.services.targets import TargetService


class InventoryBuildService:
    """Build and persist structured authenticated inventory."""

    def __init__(
        self,
        *,
        target_service: TargetService | None = None,
        credential_service: CredentialProfileService | None = None,
        auth_session_service: AuthSessionService | None = None,
        job_service: ScanJobService | None = None,
        collection_service: InventoryCollectionService | None = None,
        annotation_service: InventoryAnnotationService | None = None,
        export_service: InventoryExportService | None = None,
    ) -> None:
        self.target_service = target_service or TargetService()
        self.credential_service = credential_service or CredentialProfileService()
        self.auth_session_service = auth_session_service or AuthSessionService()
        self.job_service = job_service or ScanJobService()
        self.collection_service = collection_service or InventoryCollectionService()
        self.annotation_service = annotation_service or InventoryAnnotationService()
        self.export_service = export_service or InventoryExportService()

    def build_from_target_profile(
        self,
        session: Session,
        target_name: str,
        profile_name: str,
        controls: InventoryBuildControls,
    ) -> InventoryBuildSummary:
        login_result = self.auth_session_service.login_test(session, target_name, profile_name)
        if not login_result.success or login_result.created_session_id is None:
            detail = (
                f"{login_result.message} Last error: {login_result.last_error}"
                if login_result.last_error
                else login_result.message
            )
            raise ConflictError(detail)
        return self.build_from_session(session, login_result.created_session_id, controls)

    def build_from_session(
        self,
        session: Session,
        session_id: int,
        controls: InventoryBuildControls,
    ) -> InventoryBuildSummary:
        validation_result = self.auth_session_service.validate(session, session_id)
        if not validation_result.valid:
            raise ConflictError(validation_result.message)
        auth_session = self.auth_session_service.get(session, session_id)
        target = self.target_service.get(session, auth_session.target_id)
        credential = self.credential_service.get(session, auth_session.credential_profile_id)
        job = self.job_service.create(
            session,
            ScanJobCreate(
                target_id=target.id,
                credential_profile_id=credential.id,
                status=JobStatus.RUNNING,
                started_at=datetime.now(timezone.utc),
                summary="Inventory build in progress.",
            ),
        )
        collection_context = self.collection_service.build_collection_context(target, auth_session)
        inventory_run = InventoryRun(
            target_id=target.id,
            credential_profile_id=credential.id,
            auth_session_id=auth_session.id,
            scan_job_id=job.id,
            status=InventoryRunStatus.RUNNING.value,
            started_at=datetime.now(timezone.utc),
            started_from_url=collection_context.start_url,
            max_pages=controls.max_pages,
            max_depth=controls.max_depth,
            max_requests=controls.max_requests,
            delay_ms=controls.delay_ms,
            include_rules=controls.include,
            exclude_rules=controls.exclude,
            summary="Inventory build in progress.",
        )
        session.add(inventory_run)
        session.flush()

        try:
            result = self.collection_service.collect(target, auth_session, controls)
            self._persist_collection_result(session, inventory_run, result)
            inventory_run.pages_count = len(inventory_run.pages)
            inventory_run.endpoints_count = len(inventory_run.endpoints)
            inventory_run.parameters_count = len(inventory_run.parameters)
            inventory_run.annotations_count = len(inventory_run.annotations)
            inventory_run.status = InventoryRunStatus.COMPLETED.value
            inventory_run.finished_at = datetime.now(timezone.utc)
            inventory_run.summary = (
                f"Collected {inventory_run.pages_count} pages, "
                f"{inventory_run.endpoints_count} endpoints, "
                f"{inventory_run.parameters_count} parameters, and "
                f"{inventory_run.annotations_count} annotations."
            )
            job.status = JobStatus.COMPLETED.value
            job.finished_at = inventory_run.finished_at
            job.summary = inventory_run.summary
        except Exception as error:
            inventory_run.status = InventoryRunStatus.FAILED.value
            inventory_run.finished_at = datetime.now(timezone.utc)
            inventory_run.error_message = str(error)
            inventory_run.summary = "Inventory build failed."
            job.status = JobStatus.FAILED.value
            job.finished_at = inventory_run.finished_at
            job.error_message = str(error)
            raise

        session.flush()
        session.refresh(inventory_run)
        return InventoryBuildSummary(
            inventory_run_id=inventory_run.id,
            scan_job_id=job.id,
            target_name=target.name,
            profile_name=credential.name,
            auth_session_id=auth_session.id,
            status=inventory_run.status,
            counts=InventoryCounts(
                pages=inventory_run.pages_count,
                endpoints=inventory_run.endpoints_count,
                parameters=inventory_run.parameters_count,
                annotations=inventory_run.annotations_count,
            ),
            summary=inventory_run.summary or "",
        )

    def list(self, session: Session) -> list[InventoryRun]:
        statement = (
            select(InventoryRun)
            .options(
                selectinload(InventoryRun.auth_session),
                selectinload(InventoryRun.pages),
                selectinload(InventoryRun.endpoints),
                selectinload(InventoryRun.parameters),
                selectinload(InventoryRun.annotations),
                selectinload(InventoryRun.scan_job),
            )
            .order_by(InventoryRun.id.desc())
        )
        return list(session.scalars(statement))

    def get(self, session: Session, inventory_run_id: int) -> InventoryRun:
        statement = (
            select(InventoryRun)
            .where(InventoryRun.id == inventory_run_id)
            .options(
                selectinload(InventoryRun.auth_session),
                selectinload(InventoryRun.pages),
                selectinload(InventoryRun.endpoints),
                selectinload(InventoryRun.parameters),
                selectinload(InventoryRun.annotations),
                selectinload(InventoryRun.scan_job),
            )
        )
        inventory_run = session.scalar(statement)
        if inventory_run is None:
            raise ResourceNotFoundError(f"Inventory run {inventory_run_id} was not found.")
        return inventory_run

    def get_by_job(self, session: Session, job_id: int) -> InventoryRun | None:
        statement = (
            select(InventoryRun)
            .where(InventoryRun.scan_job_id == job_id)
            .options(selectinload(InventoryRun.pages), selectinload(InventoryRun.endpoints))
        )
        return session.scalar(statement)

    def export_json(self, session: Session, inventory_run_id: int):
        inventory_run = self.get(session, inventory_run_id)
        return self.export_service.export_json(inventory_run)

    def export_csv(self, session: Session, inventory_run_id: int):
        inventory_run = self.get(session, inventory_run_id)
        return self.export_service.export_csv(inventory_run)

    def _persist_collection_result(
        self,
        session: Session,
        inventory_run: InventoryRun,
        result: InventoryCollectionResult,
    ) -> None:
        annotation_keys: set[tuple[str, str, str]] = set()

        for page in result.pages:
            stored_page = self._upsert_page(session, inventory_run, page)
            self._append_annotations(
                session,
                inventory_run,
                annotation_keys,
                self.annotation_service.page_annotations(stored_page.url),
            )

        for endpoint in result.endpoints:
            stored_endpoint = self._upsert_endpoint(session, inventory_run, endpoint)
            self._append_annotations(
                session,
                inventory_run,
                annotation_keys,
                self.annotation_service.endpoint_annotations(
                    stored_endpoint.method,
                    stored_endpoint.path,
                ),
            )
            for source_type, names in endpoint.parameters.items():
                for name in names:
                    parameter = self._upsert_parameter(
                        session,
                        inventory_run,
                        stored_endpoint,
                        source_type,
                        name,
                    )
                    self._append_annotations(
                        session,
                        inventory_run,
                        annotation_keys,
                        self.annotation_service.parameter_annotations(
                            parameter.source_type,
                            parameter.name,
                        ),
                    )

    def _upsert_page(
        self,
        session: Session,
        inventory_run: InventoryRun,
        observed_page: ObservedPage,
    ) -> InventoryPage:
        normalized_url = self._normalize_page_url(observed_page.url)
        statement = select(InventoryPage).where(
            InventoryPage.inventory_run_id == inventory_run.id,
            InventoryPage.url == normalized_url,
        )
        stored_page = session.scalar(statement)
        if stored_page is None:
            stored_page = InventoryPage(
                inventory_run_id=inventory_run.id,
                url=normalized_url,
                title=observed_page.title or None,
                status_code=observed_page.status_code,
                cache_control=observed_page.cache_control,
                text_preview=observed_page.text_preview,
                depth=observed_page.depth,
                first_visited_at=observed_page.visited_at,
                last_visited_at=observed_page.visited_at,
                visit_count=1,
            )
            session.add(stored_page)
            session.flush()
            return stored_page
        stored_page.last_visited_at = observed_page.visited_at
        stored_page.visit_count += 1
        stored_page.title = observed_page.title or stored_page.title
        stored_page.status_code = observed_page.status_code or stored_page.status_code
        stored_page.cache_control = observed_page.cache_control or stored_page.cache_control
        stored_page.text_preview = observed_page.text_preview or stored_page.text_preview
        stored_page.depth = min(stored_page.depth, observed_page.depth)
        session.flush()
        return stored_page

    def _upsert_endpoint(
        self,
        session: Session,
        inventory_run: InventoryRun,
        observed_endpoint: ObservedEndpoint,
    ) -> InventoryEndpoint:
        normalized_path = observed_endpoint.path or "/"
        statement = select(InventoryEndpoint).where(
            InventoryEndpoint.inventory_run_id == inventory_run.id,
            InventoryEndpoint.method == observed_endpoint.method,
            InventoryEndpoint.path == normalized_path,
        )
        stored_endpoint = session.scalar(statement)
        if stored_endpoint is None:
            stored_endpoint = InventoryEndpoint(
                inventory_run_id=inventory_run.id,
                method=observed_endpoint.method,
                url=self._normalize_endpoint_url(observed_endpoint.url),
                path=normalized_path,
                first_seen_at=observed_endpoint.observed_at,
                last_seen_at=observed_endpoint.observed_at,
                request_count=1,
                cache_control=observed_endpoint.cache_control,
                content_type=observed_endpoint.content_type,
                response_preview=observed_endpoint.response_preview,
                set_cookie_names=observed_endpoint.set_cookie_names,
                cookie_issue_flags=observed_endpoint.cookie_issue_flags,
                status_codes_observed=[observed_endpoint.status_code],
            )
            session.add(stored_endpoint)
            session.flush()
            return stored_endpoint
        stored_endpoint.last_seen_at = observed_endpoint.observed_at
        stored_endpoint.request_count += 1
        stored_endpoint.cache_control = (
            observed_endpoint.cache_control or stored_endpoint.cache_control
        )
        stored_endpoint.content_type = (
            observed_endpoint.content_type or stored_endpoint.content_type
        )
        stored_endpoint.response_preview = (
            observed_endpoint.response_preview or stored_endpoint.response_preview
        )
        for name in observed_endpoint.set_cookie_names:
            if name not in stored_endpoint.set_cookie_names:
                stored_endpoint.set_cookie_names.append(name)
        for issue in observed_endpoint.cookie_issue_flags:
            if issue not in stored_endpoint.cookie_issue_flags:
                stored_endpoint.cookie_issue_flags.append(issue)
        if observed_endpoint.status_code not in stored_endpoint.status_codes_observed:
            stored_endpoint.status_codes_observed.append(observed_endpoint.status_code)
        session.flush()
        return stored_endpoint

    def _upsert_parameter(
        self,
        session: Session,
        inventory_run: InventoryRun,
        endpoint: InventoryEndpoint,
        source_type: str,
        name: str,
    ) -> InventoryParameter:
        canonical_name = name.strip()
        statement = select(InventoryParameter).where(
            InventoryParameter.inventory_run_id == inventory_run.id,
            InventoryParameter.inventory_endpoint_id == endpoint.id,
            InventoryParameter.source_type == source_type,
            InventoryParameter.name == canonical_name,
        )
        stored_parameter = session.scalar(statement)
        if stored_parameter is None:
            stored_parameter = InventoryParameter(
                inventory_run_id=inventory_run.id,
                inventory_endpoint_id=endpoint.id,
                source_type=source_type,
                name=canonical_name,
                first_seen_at=datetime.now(timezone.utc),
                last_seen_at=datetime.now(timezone.utc),
                seen_count=1,
                sensitive=self.annotation_service.parameter_is_sensitive(canonical_name),
            )
            session.add(stored_parameter)
            session.flush()
            return stored_parameter
        stored_parameter.last_seen_at = datetime.now(timezone.utc)
        stored_parameter.seen_count += 1
        stored_parameter.sensitive = stored_parameter.sensitive or (
            self.annotation_service.parameter_is_sensitive(canonical_name)
        )
        session.flush()
        return stored_parameter

    def _append_annotations(
        self,
        session: Session,
        inventory_run: InventoryRun,
        annotation_keys: set[tuple[str, str, str]],
        annotations: Iterable[AnnotationCandidate],
    ) -> None:
        for annotation in annotations:
            key = (annotation.subject_type.value, annotation.subject_ref, annotation.marker)
            if key in annotation_keys:
                continue
            annotation_keys.add(key)
            session.add(
                InventoryAnnotation(
                    inventory_run_id=inventory_run.id,
                    subject_type=annotation.subject_type.value,
                    subject_ref=annotation.subject_ref,
                    marker=annotation.marker,
                    reason=annotation.reason,
                )
            )
        session.flush()

    def _normalize_page_url(self, value: str) -> str:
        parsed = urlparse(value)
        normalized = parsed._replace(fragment="")
        return urlunparse(normalized)

    def _normalize_endpoint_url(self, value: str) -> str:
        parsed = urlparse(value)
        normalized = parsed._replace(query="", fragment="")
        return urlunparse(normalized)
