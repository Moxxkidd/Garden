"""Private bounded known-work checkpoints and immutable linked recovery runs."""

import hashlib
import hmac
import json
import secrets
from pathlib import Path

from sqlalchemy import func, select

from app.core.errors import InputValidationError
from app.models.credential_profile import CredentialProfile
from app.models.identity_checkpoint import IdentityCheckpoint
from app.models.scan_context import ScanContext
from app.models.scan_run import ScanRun
from app.models.target import Target
from app.schemas.identity import IdentityCollectionRequest, RecoveryPreview, RecoveryRequest
from app.services.coverage_identity import redacted_observed_url
from app.services.discovery import origin
from app.services.session_storage import SessionStorageService


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=True).encode()).hexdigest()


class IdentityRecoveryService:
    def __init__(self, *, storage=None, collection=None):
        self.storage = storage or SessionStorageService()
        self.collection = collection

    def _binding(self, session, run, context):
        target = session.get(Target, run.target_id)
        profile = session.get(CredentialProfile, context.credential_profile_id)
        if (
            not target
            or target.status != "active"
            or not profile
            or profile.target_id != target.id
            or context.context_key != f"profile:{profile.id}"
        ):
            raise InputValidationError("补采身份或目标已变更，请重新建立采集任务。")
        return {
            "target_id": target.id,
            "target_origin": origin(target.base_url),
            "profile_id": profile.id,
            "context_key": context.context_key,
            "options": run.options,
        }, profile

    def save(self, session, context_id, pending, uncertain):
        context = session.get(ScanContext, context_id)
        if not context or context.kind != "identity":
            raise InputValidationError("只有独立身份上下文支持此补采检查点。")
        run = session.get(ScanRun, context.scan_run_id)
        binding, _ = self._binding(session, run, context)
        groups = [[], []]
        seen = set()
        truncated = False
        size = 0
        for index, items in enumerate((uncertain, pending)):
            for item in items:
                url, method = item.get("url"), item.get("method", "GET").upper()
                if (
                    not isinstance(url, str)
                    or len(url) > 2000
                    or origin(url) != binding["target_origin"]
                    or method not in {"GET", "HEAD"}
                ):
                    raise InputValidationError("检查点包含无效请求或目标范围外的内容。")
                key = (url, method)
                if key in seen:
                    continue
                size += len(url.encode()) + 100
                if len(seen) >= 2000 or size > 1024 * 1024:
                    truncated = True
                    continue
                seen.add(key)
                groups[index].append({"url": url, "method": method})
        payload = {
            "version": 1,
            "binding": binding,
            "nonce": secrets.token_hex(32),
            "pending": groups[1],
            "uncertain": groups[0],
            "truncated": truncated,
        }
        version = (
            session.scalar(
                select(func.max(IdentityCheckpoint.version)).where(
                    IdentityCheckpoint.context_id == context_id
                )
            )
            or 0
        ) + 1
        storage_ref = self.storage.write_payload(context_id, payload)
        try:
            with session.begin_nested():
                checkpoint = IdentityCheckpoint(
                    context_id=context_id,
                    version=version,
                    storage_ref=storage_ref,
                    configuration_digest=digest(binding),
                    pending_count=len(groups[1]),
                    uncertain_count=len(groups[0]),
                )
                session.add(checkpoint)
                session.flush()
        except Exception:
            Path(storage_ref).unlink(missing_ok=True)
            raise InputValidationError("检查点保存失败，不能安全补采。") from None
        return version

    def _load(self, session, request):
        run = session.get(ScanRun, request.source_run_id)
        context = session.get(ScanContext, request.source_context_id)
        if (
            not run
            or not context
            or context.scan_run_id != run.id
            or run.mode != "identity_collection"
            or context.kind != "identity"
            or run.status in {"queued", "running"}
        ):
            raise InputValidationError("补采来源必须是已结束的同一身份采集任务。")
        binding, profile = self._binding(session, run, context)
        checkpoint = session.scalar(
            select(IdentityCheckpoint)
            .where(IdentityCheckpoint.context_id == context.id)
            .order_by(IdentityCheckpoint.version.desc())
        )
        if not checkpoint or checkpoint.version != request.checkpoint_version:
            raise InputValidationError("检查点不存在或已更新，请重新预览。")
        payload = self.storage.read_identity_payload(checkpoint.storage_ref)
        if (
            payload.get("version") != 1
            or payload.get("binding") != binding
            or checkpoint.configuration_digest != digest(binding)
        ):
            raise InputValidationError("检查点配置不匹配，请重新建立采集任务。")
        if not payload.get("pending") and not payload.get("uncertain"):
            raise InputValidationError("没有已知待补采请求。")
        profile_snapshot = {
            column.name: getattr(profile, column.name)
            for column in profile.__table__.columns
            if column.name not in {"created_at", "updated_at"}
        }
        token = hmac.new(
            payload["nonce"].encode(),
            json.dumps(
                {
                    "request": request.model_dump(),
                    "binding": binding,
                    "profile": profile_snapshot,
                    "checkpoint": checkpoint.id,
                },
                sort_keys=True,
            ).encode(),
            hashlib.sha256,
        ).hexdigest()
        return run, context, checkpoint, payload, token

    def preview(self, session, request: RecoveryRequest):
        run, context, checkpoint, payload, token = self._load(session, request)
        return RecoveryPreview(
            source_run_id=run.id,
            source_context_id=context.id,
            checkpoint_version=checkpoint.version,
            profile_id=context.credential_profile_id,
            pending_count=checkpoint.pending_count,
            uncertain_count=checkpoint.uncertain_count,
            examples=[
                redacted_observed_url(x["url"])
                for x in (payload["uncertain"] + payload["pending"])[:5]
            ],
            known_scope_truncated=payload["truncated"],
            preview_token=token,
        )

    def start(self, session, request: RecoveryRequest, preview_token: str):
        run, context, checkpoint, payload, token = self._load(session, request)
        if not hmac.compare_digest(token, preview_token):
            raise InputValidationError("预览已失效，身份或选项发生变化，请重新预览。")
        from app.services.identity_collection import IdentityCollectionService

        collection = self.collection or IdentityCollectionService()
        work = payload["uncertain"] + payload["pending"]
        return collection.start(
            session,
            IdentityCollectionRequest(
                url=work[0]["url"],
                target_id=run.target_id,
                profile_ids=[context.credential_profile_id],
                options=request.options,
            ),
            recovery={
                "parent_run_id": run.id,
                "context_id": context.id,
                "checkpoint_version": checkpoint.version,
                "work": work,
            },
        )
