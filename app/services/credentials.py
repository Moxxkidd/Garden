"""Credential profile service layer."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.errors import ConflictError, ResourceNotFoundError
from app.models.credential_profile import CredentialProfile
from app.models.scan_job import ScanJob
from app.models.target import Target
from app.schemas.credential import CredentialProfileCreate


class CredentialProfileService:
    """CRUD operations for credential profiles."""

    def create(self, session: Session, payload: CredentialProfileCreate) -> CredentialProfile:
        target = session.get(Target, payload.target_id)
        if target is None:
            raise ResourceNotFoundError(f"Target {payload.target_id} was not found.")
        duplicate = session.scalar(
            select(CredentialProfile).where(
                CredentialProfile.target_id == payload.target_id,
                CredentialProfile.name == payload.name,
            )
        )
        if duplicate is not None:
            raise ConflictError(
                f"Credential profile '{payload.name}' already exists "
                f"for target {payload.target_id}."
            )
        credential = CredentialProfile(
            target_id=payload.target_id,
            name=payload.name,
            role=payload.role,
            auth_type=payload.auth_type.value,
            username=payload.username,
            secret_ref=payload.secret_ref,
            login_config_path=payload.login_config_path,
        )
        session.add(credential)
        session.flush()
        session.refresh(credential)
        return credential

    def list(self, session: Session) -> list[CredentialProfile]:
        return list(
            session.scalars(
                select(CredentialProfile).order_by(
                    CredentialProfile.target_id,
                    CredentialProfile.name,
                )
            )
        )

    def list_for_target_role(
        self,
        session: Session,
        target_id: int,
        role: str,
    ) -> list[CredentialProfile]:
        return list(
            session.scalars(
                select(CredentialProfile)
                .where(
                    CredentialProfile.target_id == target_id,
                    CredentialProfile.role == role,
                )
                .order_by(CredentialProfile.name, CredentialProfile.id)
            )
        )

    def get(self, session: Session, credential_id: int) -> CredentialProfile:
        credential = session.get(CredentialProfile, credential_id)
        if credential is None:
            raise ResourceNotFoundError(f"Credential profile {credential_id} was not found.")
        return credential

    def get_by_target_and_name(
        self,
        session: Session,
        target_id: int,
        name: str,
    ) -> CredentialProfile:
        credential = session.scalar(
            select(CredentialProfile).where(
                CredentialProfile.target_id == target_id,
                CredentialProfile.name == name,
            )
        )
        if credential is None:
            raise ResourceNotFoundError(
                f"Credential profile '{name}' was not found for target {target_id}."
            )
        return credential

    def delete(self, session: Session, credential_id: int) -> None:
        credential = self.get(session, credential_id)
        linked_job = session.scalar(
            select(ScanJob).where(ScanJob.credential_profile_id == credential_id).limit(1)
        )
        if linked_job is not None:
            raise ConflictError("Cannot delete a credential profile that already has scan jobs.")
        session.delete(credential)
        session.flush()
