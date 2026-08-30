"""Guided setup for passive authenticated coverage runs."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Protocol
from urllib.parse import urljoin, urlparse

from app.cli.paths import formal_runtime_paths
from app.core.errors import InputValidationError
from app.db.bootstrap import session_scope
from app.models.credential_profile import CredentialProfile
from app.models.enums import AuthType, TargetType
from app.models.target import Target
from app.schemas.assessment import PassiveCoverageStartRequest
from app.schemas.credential import CredentialProfileCreate
from app.schemas.target import TargetCreate
from app.services.credentials import CredentialProfileService
from app.services.ephemeral_secret_store import EphemeralSecretStore
from app.services.login_configs import LoginConfigService, encode_inline_login_config
from app.services.targets import TargetService


@dataclass(frozen=True)
class PromptChoice:
    value: str
    label: str


class CoveragePrompts(Protocol):
    def choose(self, label: str, choices: list[PromptChoice]) -> str: ...

    def confirm(self, label: str, *, default: bool = True) -> bool: ...

    def text(self, label: str, *, default: str | None = None) -> str: ...

    def secret(self, label: str) -> str: ...

    def write(self, text: str) -> None: ...


class CoverageSetupWizard:
    """Select same-origin target and exact fixed-role credential profiles."""

    def __init__(
        self,
        *,
        prompts: CoveragePrompts,
        target_service: TargetService | None = None,
        credential_service: CredentialProfileService | None = None,
        login_config_service: LoginConfigService | None = None,
        secret_store: EphemeralSecretStore | None = None,
        session_factory=session_scope,
    ) -> None:
        self.prompts = prompts
        self.target_service = target_service or TargetService()
        self.credential_service = credential_service or CredentialProfileService()
        self.login_config_service = login_config_service or LoginConfigService()
        self.secret_store = secret_store or EphemeralSecretStore(self._default_secret_root())
        self.session_factory = session_factory

    def run(self, entry_url: str) -> PassiveCoverageStartRequest:
        entry_origin = self._origin(entry_url)
        draft_secret_refs: list[str] = []
        self.secret_store.purge_expired()
        try:
            with self.session_factory() as session:
                targets = [
                    target
                    for target in self.target_service.list(session)
                    if self._origin(target.base_url) == entry_origin
                ]
                target = self._select_or_create_target(session, entry_url, targets)
                user = self._select_or_create_profile(session, target, "user", draft_secret_refs)
                admin = self._select_or_create_profile(session, target, "admin", draft_secret_refs)
                self.prompts.write(
                    "\n认证覆盖配置摘要\n"
                    f"Target: #{target.id} {target.name}\n"
                    f"user: #{user.id} {user.name}\n"
                    f"admin: #{admin.id} {admin.name}\n"
                    "模式: 仅被动采集\n"
                )
                if not self.prompts.confirm("提交认证覆盖任务？", default=True):
                    raise InputValidationError("已取消认证覆盖任务。")
                return PassiveCoverageStartRequest(
                    url=entry_url,
                    target_id=target.id,
                    user_profile_id=user.id,
                    admin_profile_id=admin.id,
                )
        except Exception:
            for reference in draft_secret_refs:
                self.secret_store.delete(reference)
            raise

    def _select_or_create_target(
        self,
        session,
        entry_url: str,
        targets: list[Target],
    ) -> Target:
        if targets:
            return self._select_target(targets)
        if not self.prompts.confirm("没有同源 Target，立即创建？", default=True):
            raise InputValidationError("没有与入口 URL 同源的 Target。")
        name = self.prompts.text("Target 名称", default=urlparse(entry_url).hostname)
        owner = self.prompts.text("Target 负责人（用于资产归属）", default="local-user")
        return self.target_service.create(
            session,
            TargetCreate(
                name=name,
                base_url=self._origin_base_url(entry_url),
                type=TargetType.WEB,
                owner=owner,
            ),
        )

    def _select_target(self, targets: list[Target]) -> Target:
        if not targets:
            raise InputValidationError("没有与入口 URL 同源的 Target。")
        selected = self.prompts.choose(
            "选择同源 Target",
            [
                PromptChoice(value=str(target.id), label=f"#{target.id} {target.name}")
                for target in targets
            ],
        )
        return next(target for target in targets if str(target.id) == selected)

    def _select_profile_or_create(
        self,
        profiles: list[CredentialProfile],
        role: str,
    ) -> CredentialProfile | None:
        selected = self.prompts.choose(
            f"选择 {role} 凭据档案",
            [
                PromptChoice(value=str(profile.id), label=f"#{profile.id} {profile.name}")
                for profile in profiles
            ]
            + [PromptChoice(value="__create__", label=f"创建新的 {role} 凭据档案")],
        )
        if selected == "__create__":
            return None
        return next(profile for profile in profiles if str(profile.id) == selected)

    def _select_or_create_profile(
        self,
        session,
        target: Target,
        role: str,
        draft_secret_refs: list[str],
    ) -> CredentialProfile:
        profiles = self.credential_service.list_for_target_role(session, target.id, role)
        if profiles:
            selected = self._select_profile_or_create(profiles, role)
            if selected is not None:
                return self._prepare_existing_profile(selected, role, draft_secret_refs)
        elif not self.prompts.confirm(
            f"没有 role={role} 的凭据档案，立即创建？",
            default=True,
        ):
            raise InputValidationError(f"Target #{target.id} 没有 role={role} 的凭据档案。")

        name = self.prompts.text(f"{role} 档案名称", default=f"{target.name}-{role}")
        login_url = self._same_origin_url(
            target.base_url,
            self.prompts.text(f"{role} 登录地址", default="/login"),
        )
        validate_url = self._same_origin_url(
            target.base_url,
            self.prompts.text(f"{role} 登录后验证地址", default="/"),
        )
        username = self.prompts.text(f"{role} 用户名")
        secret = self.prompts.secret(f"{role} 密码（输入已隐藏）")
        reference = self.secret_store.write(secret)
        draft_secret_refs.append(reference)
        login_config = encode_inline_login_config(
            {
                "adapter": "playwright",
                "login_url": login_url,
                "validate_url": validate_url,
                "username_selector": "auto",
                "password_selector": "auto",
                "submit_selector": "auto",
                "auto_detect_selectors": True,
            }
        )
        return self.credential_service.create(
            session,
            CredentialProfileCreate(
                target_id=target.id,
                name=name,
                role=role,
                auth_type=AuthType.PASSWORD,
                username=username,
                secret_ref=reference,
                login_config_path=login_config,
            ),
        )

    def _prepare_existing_profile(
        self,
        profile: CredentialProfile,
        role: str,
        draft_secret_refs: list[str],
    ) -> CredentialProfile:
        self.login_config_service.load(profile.login_config_path)
        if not profile.secret_ref.startswith("ephemeral-file://"):
            return profile
        try:
            if self.secret_store.path_for(profile.secret_ref).exists():
                return profile
        except InputValidationError:
            pass
        secret = self.prompts.secret(f"{role} 密码已过期，请重新输入（输入已隐藏）")
        reference = self.secret_store.write(secret)
        draft_secret_refs.append(reference)
        profile.secret_ref = reference
        return profile

    def _same_origin_url(self, base_url: str, value: str) -> str:
        candidate = urljoin(f"{base_url.rstrip('/')}/", value.strip())
        if self._origin(candidate) != self._origin(base_url):
            raise InputValidationError("登录与验证地址必须和 Target 同源。")
        return candidate

    def _origin_base_url(self, url: str) -> str:
        parsed = urlparse(url)
        origin = self._origin(url)
        host = (
            f"[{parsed.hostname}]"
            if parsed.hostname and ":" in parsed.hostname
            else parsed.hostname
        )
        default_port = 443 if origin[0] == "https" else 80
        port = f":{origin[2]}" if origin[2] != default_port else ""
        return f"{origin[0]}://{host}{port}"

    def _default_secret_root(self) -> Path:
        paths = formal_runtime_paths()
        return paths.secrets_dir if paths is not None else Path.cwd() / "data/ephemeral-secrets"

    def _origin(self, url: str) -> tuple[str, str, int]:
        parsed = urlparse(url)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            raise InputValidationError("coverage 入口必须是有效 HTTP(S) URL。")
        try:
            port = parsed.port or (443 if parsed.scheme == "https" else 80)
        except ValueError as error:
            raise InputValidationError("coverage 入口端口无效。") from error
        return parsed.scheme.lower(), parsed.hostname.lower(), port
