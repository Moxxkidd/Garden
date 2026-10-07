"""Read-only identity submission previews; private inputs stay on the Garden host."""

import hashlib
import json
import secrets
import threading
import time

from sqlalchemy import select

from app.core.errors import InputValidationError
from app.models.auth_session import AuthSession
from app.models.credential_profile import CredentialProfile
from app.models.target import Target
from app.services.coverage_identity import redacted_observed_url
from app.services.discovery import origin


class IdentityPreviewService:
    def __init__(self, collection, clock=time.monotonic):
        self.collection = collection
        self.clock = clock
        self.previews = {}
        self.lock = threading.Lock()

    def _binding(self, session, request):
        target = session.get(Target, request.target_id, populate_existing=True)
        if not target or target.status != "active":
            raise InputValidationError("目标不存在或不可用。")
        entry = self.collection.policy.normalize_url(request.url)
        if origin(entry) != origin(target.base_url):
            raise InputValidationError("采集入口必须与目标同源。")
        records = [{"target": target.id, "url": target.base_url, "status": target.status}]
        labels = []
        for profile_id in request.profile_ids:
            profile = session.get(CredentialProfile, profile_id, populate_existing=True)
            if not profile or profile.target_id != target.id:
                raise InputValidationError("身份不属于当前目标。")
            records.append(
                {
                    name: getattr(profile, name)
                    for name in (
                        "id",
                        "target_id",
                        "name",
                        "role",
                        "username",
                        "secret_ref",
                        "login_config_path",
                    )
                }
            )
            sessions = session.scalars(
                select(AuthSession).where(AuthSession.credential_profile_id == profile_id)
            ).all()
            records.append([(s.id, s.status, str(s.revoked_at)) for s in sessions])
            labels.append({"profile_id": profile.id, "name": profile.name, "role": profile.role})
        return hashlib.sha256(json.dumps(records, sort_keys=True).encode()).hexdigest(), labels

    def preview(self, session, request):
        binding, profiles = self._binding(session, request)
        token = secrets.token_urlsafe(32)
        with self.lock:
            self.previews = {k: v for k, v in self.previews.items() if v[0] > self.clock()}
            if len(self.previews) >= 100:
                raise InputValidationError("待确认预览过多，请稍后重试。")
            self.previews[token] = (self.clock() + 900, request, binding)
        return {
            "preview_token": token,
            "url": redacted_observed_url(request.url),
            "target_id": request.target_id,
            "profiles": profiles,
            "include_anonymous": request.include_anonymous,
            "max_pages": request.options.max_pages,
            "max_browser_requests": request.options.max_browser_requests,
            "note": "预览不访问目标。确认后仅 GET/HEAD；身份验证请求也占用请求预算。",
        }

    def start(self, session, token):
        with self.lock:
            item = self.previews.get(token)
            if not item or item[0] <= self.clock():
                raise InputValidationError("预览已过期，请重新预览。")
            _, request, binding = item
            if self._binding(session, request)[0] != binding:
                raise InputValidationError("目标、身份或会话已变更，请重新预览。")
            self.previews.pop(token)
        return self.collection.start(session, request)
