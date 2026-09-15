"""Local configuration inspection. Never performs DNS, login, or collection."""

import hashlib
import json
from urllib.parse import urljoin, urlsplit, urlunsplit

import yaml
from pydantic import ValidationError

from app.core.errors import GardenError
from app.core.settings import Settings
from app.models.credential_profile import CredentialProfile
from app.models.scan_run import TERMINAL_SCAN_RUN_STATUSES, ScanRun
from app.models.target import Target
from app.schemas.assessment import AssessmentStartRequest
from app.schemas.auth import PlaywrightLoginConfig
from app.schemas.scan_preview import PreviewContext, PreviewIssue, ScanPreview
from app.services.coverage_identity import redacted_observed_url
from app.services.login_configs import LoginConfigService
from app.services.scan_network import TargetNetworkPolicy
from app.services.scan_options import resolve_scan_options


def _origin(normalized: str) -> tuple[str, str, int]:
    parsed = urlsplit(normalized)
    return (
        parsed.scheme,
        parsed.hostname or "",
        parsed.port or (443 if parsed.scheme == "https" else 80),
    )


def build_preview(session, request: AssessmentStartRequest, settings: Settings) -> ScanPreview:
    # A caller may have uncommitted wizard drafts; inspecting them must not flush unrelated edits.
    with session.no_autoflush:
        return _inspect(session, request, settings)


def _inspect(session, request: AssessmentStartRequest, settings: Settings) -> ScanPreview:
    policy = TargetNetworkPolicy(settings)
    options = resolve_scan_options(request.options, settings)
    contexts = [PreviewContext(kind="anonymous")]
    issues = [
        PreviewIssue(
            code="network_not_checked",
            field="url",
            severity="info",
            message="尚未执行 DNS 或连通性检查；覆盖结果在任务执行后产生。",
        )
    ]

    def error(code: str, field: str, message: str) -> None:
        issues.append(PreviewIssue(code=code, field=field, message=message))

    normalized = ""
    display = "入口需修正"
    origin_display = "尚未确定"
    try:
        normalized = policy.normalize_url(request.url)
        display = redacted_observed_url(normalized)
        parsed = urlsplit(display)
        origin_display = urlunsplit((parsed.scheme, parsed.netloc, "", "", ""))
    except (GardenError, ValueError):
        error(
            "invalid_url", "url", "请输入有效 HTTP(S) 地址和端口，且不要在 URL 中嵌入用户名或密码。"
        )

    if request.active_checks_enabled:
        error("active_mode_not_supported", "mode", "此预览仅支持普通扫描与被动认证覆盖。")

    fingerprint_data: list = [
        request.model_copy(update={"options": options, "url": normalized}).model_dump(mode="json"),
        options.model_dump(),
        settings.allow_non_local_targets,
        settings.allow_private_targets,
    ]
    target_id = request.target_id
    if request.mode.value == "authenticated_coverage":
        issues.append(
            PreviewIssue(
                code="login_not_verified",
                field="contexts",
                severity="info",
                message="配置匹配不代表登录已验证；本次尚未验证登录。",
            )
        )
        profiles = []
        for kind, profile_id in (
            ("user", request.user_profile_id),
            ("admin", request.admin_profile_id),
        ):
            profile = session.get(CredentialProfile, profile_id)
            profiles.append(profile)
            contexts.append(
                PreviewContext(
                    kind=kind, profile_id=profile_id, profile_name=profile.name if profile else None
                )
            )
            field = f"{kind}_profile_id"
            if profile is None:
                error("profile_missing", field, f"{kind} 凭据档案不存在，请重新选择。")
                continue
            # Secrets and session state intentionally do not participate: a post-confirmation
            # secret replacement must not invalidate an otherwise unchanged configuration.
            fingerprint_data.append(
                [
                    profile.id,
                    profile.target_id,
                    profile.role,
                    profile.username,
                    profile.auth_type,
                    profile.name,
                    profile.login_config_path,
                ]
            )
            if profile.role != kind:
                error("profile_role_mismatch", field, f"请选择 role={kind} 的凭据档案。")
            if target_id is None:
                target_id = profile.target_id
            if target_id != profile.target_id:
                error("profile_target_mismatch", field, "凭据档案必须属于所选的同一个 Target。")

        target = session.get(Target, target_id) if target_id is not None else None
        target_normalized = ""
        if target is None:
            error("target_missing", "target_id", "Target 不存在，请重新选择目标。")
        else:
            fingerprint_data.append([target.id, target.name, target.base_url])
            try:
                target_normalized = policy.normalize_url(target.base_url)
                if normalized and _origin(normalized) != _origin(target_normalized):
                    error(
                        "target_origin_mismatch",
                        "url",
                        "入口地址必须与 Target 同源（协议、主机和端口一致）。",
                    )
            except (GardenError, ValueError):
                error("invalid_target_url", "target_id", "Target 地址无效，请修正目标配置。")

        for kind, profile in zip(("user", "admin"), profiles, strict=True):
            if profile is None:
                continue
            field = f"{kind}_profile_id"
            try:
                config = LoginConfigService().load(profile.login_config_path)
                fingerprint_data.append(config.model_dump(mode="json"))
                if isinstance(config, PlaywrightLoginConfig):
                    urls = (config.login_url, config.validate_url)
                else:
                    urls = tuple(
                        item.url
                        for item in (
                            config.login_request,
                            config.validate_request,
                            config.refresh_request,
                        )
                        if item is not None
                    )
                for url in urls:
                    if not target_normalized:
                        break
                    configured = policy.normalize_url(
                        urljoin(target_normalized.rstrip("/") + "/", url)
                    )
                    if _origin(configured) != _origin(target_normalized):
                        error(
                            "login_origin_mismatch",
                            field,
                            f"{kind} 的登录、验证及刷新地址必须与 Target 同源。",
                        )
                        break
            except (GardenError, OSError, ValueError, ValidationError, yaml.YAMLError):
                error(
                    "login_config_invalid",
                    field,
                    f"无法解析 {kind} 登录配置，请检查配置格式和文件可读性。",
                )
    elif target_id is not None and session.get(Target, target_id) is None:
        error("target_missing", "target_id", "Target 不存在，请重新选择目标。")

    if request.source_run_id is not None:
        source = session.get(ScanRun, request.source_run_id)
        if source is not None:
            fingerprint_data.append([source.id, source.mode, source.status, source.target_id])
        if (
            source is None
            or request.mode.value != "authenticated_coverage"
            or source.mode != "quick"
            or source.status not in TERMINAL_SCAN_RUN_STATUSES
            or source.target_id != target_id
        ):
            error(
                "source_run_invalid", "source_run_id", "来源必须是同一 Target 下已结束的普通扫描。"
            )

    return ScanPreview(
        mode=request.mode.value,
        entry_display=display,
        origin_display=origin_display,
        contexts=contexts,
        issues=issues,
        effective_options=options,
        can_submit=not any(issue.severity == "error" for issue in issues),
        budget_note=(
            "页面与静态资源上限为采集预算，不是保证采集量。"
            if request.mode.value == "quick"
            else "各上下文分别受页面与深度预算约束；max_resources 为已登录上下文请求记录上限，"
            "不是整个任务的网络请求总量。"
        ),
        configuration_fingerprint=hashlib.sha256(
            json.dumps(fingerprint_data, sort_keys=True).encode()
        ).hexdigest(),
    )
