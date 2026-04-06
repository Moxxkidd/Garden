"""Scan job service layer."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.core.errors import ResourceNotFoundError
from app.models.credential_profile import CredentialProfile
from app.models.scan_job import ScanJob
from app.models.target import Target
from app.schemas.job import ScanJobCreate


class ScanJobService:
    """Read and seed operations for scan jobs."""

    def create(self, session: Session, payload: ScanJobCreate) -> ScanJob:
        target = session.get(Target, payload.target_id)
        if target is None:
            raise ResourceNotFoundError(f"Target {payload.target_id} was not found.")
        credential = session.get(CredentialProfile, payload.credential_profile_id)
        if credential is None:
            raise ResourceNotFoundError(
                f"Credential profile {payload.credential_profile_id} was not found."
            )
        job = ScanJob(
            target_id=payload.target_id,
            credential_profile_id=payload.credential_profile_id,
            status=payload.status.value,
            started_at=payload.started_at,
            finished_at=payload.finished_at,
            summary=payload.summary,
            error_message=payload.error_message,
        )
        session.add(job)
        session.flush()
        session.refresh(job)
        return job

    def list(self, session: Session) -> list[ScanJob]:
        return list(
            session.scalars(
                select(ScanJob)
                .options(
                    selectinload(ScanJob.target),
                    selectinload(ScanJob.credential_profile),
                )
                .order_by(ScanJob.id.desc())
            )
        )

    def get(self, session: Session, job_id: int) -> ScanJob:
        job = session.scalar(
            select(ScanJob)
            .where(ScanJob.id == job_id)
            .options(
                selectinload(ScanJob.target),
                selectinload(ScanJob.credential_profile),
            )
        )
        if job is None:
            raise ResourceNotFoundError(f"Scan job {job_id} was not found.")
        return job
