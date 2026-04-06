"""Target service layer."""

from __future__ import annotations

import json
from dataclasses import dataclass
from enum import Enum
from pathlib import Path

import yaml
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.core.errors import ConflictError, InputValidationError, ResourceNotFoundError
from app.core.guardrails import ensure_target_allowed
from app.core.settings import Settings, get_settings
from app.models.target import Target
from app.schemas.target import TargetCreate


class DuplicateAction(str, Enum):
    SKIP = "skip"
    UPDATE = "update"
    FAIL = "fail-on-duplicate"


@dataclass
class TargetImportResult:
    imported: int = 0
    updated: int = 0
    skipped: int = 0


class TargetService:
    """CRUD and import operations for targets."""

    def create(self, session: Session, payload: TargetCreate) -> Target:
        settings = get_settings()
        ensure_target_allowed(payload.base_url, settings)
        self._assert_unique_target(session, payload.name, payload.base_url)
        target = Target(
            name=payload.name,
            base_url=payload.base_url,
            type=payload.type.value,
            owner=payload.owner,
            tags=payload.tags,
            status=payload.status.value,
        )
        session.add(target)
        session.flush()
        session.refresh(target)
        return target

    def list(self, session: Session) -> list[Target]:
        return list(session.scalars(select(Target).order_by(Target.name)))

    def get(self, session: Session, target_id: int) -> Target:
        target = session.get(Target, target_id)
        if target is None:
            raise ResourceNotFoundError(f"Target {target_id} was not found.")
        return target

    def get_by_name(self, session: Session, name: str) -> Target:
        target = session.scalar(select(Target).where(Target.name == name))
        if target is None:
            raise ResourceNotFoundError(f"Target '{name}' was not found.")
        return target

    def delete(self, session: Session, target_id: int) -> None:
        target = self.get(session, target_id)
        if target.credential_profiles:
            raise ConflictError("Cannot delete a target that still has credential profiles.")
        if target.scan_jobs:
            raise ConflictError("Cannot delete a target that still has scan jobs.")
        session.delete(target)
        session.flush()

    def import_file(
        self,
        session: Session,
        file_path: Path,
        on_duplicate: DuplicateAction = DuplicateAction.SKIP,
        settings: Settings | None = None,
    ) -> TargetImportResult:
        effective_settings = settings or get_settings()
        payloads = self._load_payloads(file_path)
        result = TargetImportResult()
        for payload in payloads:
            ensure_target_allowed(payload.base_url, effective_settings)
            existing = self._find_existing_target(session, payload.name, payload.base_url)
            if existing is None:
                self.create(session, payload)
                result.imported += 1
                continue
            if on_duplicate is DuplicateAction.FAIL:
                raise ConflictError(
                    "Duplicate target detected for name "
                    f"'{payload.name}' or base_url '{payload.base_url}'."
                )
            if on_duplicate is DuplicateAction.SKIP:
                result.skipped += 1
                continue
            existing.name = payload.name
            existing.base_url = payload.base_url
            existing.type = payload.type.value
            existing.owner = payload.owner
            existing.tags = payload.tags
            existing.status = payload.status.value
            session.flush()
            result.updated += 1
        return result

    def _assert_unique_target(self, session: Session, name: str, base_url: str) -> None:
        duplicate = self._find_existing_target(session, name, base_url)
        if duplicate is not None:
            raise ConflictError(f"Target '{name}' or base_url '{base_url}' already exists.")

    def _find_existing_target(self, session: Session, name: str, base_url: str) -> Target | None:
        statement = select(Target).where(or_(Target.name == name, Target.base_url == base_url))
        matches = list(session.scalars(statement))
        if len(matches) > 1:
            raise ConflictError(
                f"Multiple existing targets matched '{name}' or '{base_url}'. "
                "Resolve duplicates first."
            )
        return matches[0] if matches else None

    def _load_payloads(self, file_path: Path) -> list[TargetCreate]:
        if not file_path.exists():
            raise InputValidationError(f"Import file '{file_path}' does not exist.")
        suffix = file_path.suffix.lower()
        raw_data: object
        if suffix == ".json":
            raw_data = json.loads(file_path.read_text(encoding="utf-8"))
        elif suffix in {".yaml", ".yml"}:
            raw_data = yaml.safe_load(file_path.read_text(encoding="utf-8"))
        else:
            raise InputValidationError("Import file must be JSON or YAML.")
        if isinstance(raw_data, list):
            items = raw_data
        elif isinstance(raw_data, dict) and isinstance(raw_data.get("targets"), list):
            items = raw_data["targets"]
        else:
            raise InputValidationError(
                "Target import file must contain a top-level list or a 'targets' list."
            )
        return [TargetCreate.model_validate(item) for item in items]
