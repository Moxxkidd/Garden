"""Audit trail helpers."""

from __future__ import annotations

from sqlalchemy.orm import Session

from app.models.audit_event import AuditEvent
from app.models.enums import AuditEventStatus, AuditEventType


class AuditService:
    """Write redacted audit events for security-sensitive actions."""

    def record(
        self,
        session: Session,
        event_type: AuditEventType,
        status: AuditEventStatus,
        detail_redacted: dict[str, object],
        *,
        target_id: int | None = None,
        credential_profile_id: int | None = None,
        auth_session_id: int | None = None,
        scan_job_id: int | None = None,
    ) -> AuditEvent:
        event = AuditEvent(
            event_type=event_type.value,
            status=status.value,
            detail_redacted=detail_redacted,
            target_id=target_id,
            credential_profile_id=credential_profile_id,
            auth_session_id=auth_session_id,
            scan_job_id=scan_job_id,
        )
        session.add(event)
        session.flush()
        session.refresh(event)
        return event
