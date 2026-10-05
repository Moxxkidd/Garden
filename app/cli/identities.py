"""Identity collection and authentication controls backed by shared services."""

import json
from pathlib import Path
from typing import Annotated

import httpx
import typer
from pydantic import ValidationError

from app.cli.utils import handle_cli_error
from app.core.errors import GardenError, InputValidationError
from app.db.bootstrap import session_scope
from app.schemas.identity import IdentityCollectionRequest, ManualLoginRequest, RecoveryRequest
from app.schemas.scan import ScanOptions
from app.services.identity_collection import IdentityCollectionService
from app.services.identity_preview import IdentityPreviewService
from app.services.identity_recovery import IdentityRecoveryService
from app.services.identity_sessions import IdentitySessionService
from app.services.scan_application import InlineScanDispatcher

app = typer.Typer(help="独立身份采集、状态管理与已知缺口补采。", no_args_is_help=True)


def config(profile_id, login_url, validate_url, success_selector, success_text):
    try:
        return ManualLoginRequest(
            profile_id=profile_id,
            login_url=login_url,
            validate_url=validate_url,
            success_selector=success_selector,
            success_text=success_text,
        )
    except ValidationError:
        raise InputValidationError("需要身份 ID、登录/验证地址以及成功元素或文本。") from None


def remote(api_url, path, data=None, method="POST"):
    try:
        response = httpx.request(method, api_url.rstrip("/") + path, json=data, timeout=30)
        response.raise_for_status()
        typer.echo(json.dumps(response.json(), ensure_ascii=False, indent=2))
    except (httpx.HTTPError, ValueError):
        raise InputValidationError(
            "无法完成登录操作。请启动 Garden Web 服务并检查 API 地址和尝试状态。"
        ) from None


@app.command("collect")
def collect(
    url: str,
    target_id: int = typer.Option(...),
    profile_id: Annotated[list[int] | None, typer.Option("--profile-id")] = None,
    anonymous: bool = typer.Option(False),
    max_pages: int = typer.Option(50),
    yes: bool = typer.Option(False, "--yes"),
):
    try:
        request = IdentityCollectionRequest(
            url=url,
            target_id=target_id,
            profile_ids=profile_id or [],
            include_anonymous=anonymous,
            options=ScanOptions(max_pages=max_pages),
        )
        service = IdentityCollectionService(dispatcher=InlineScanDispatcher())
        preview_service = IdentityPreviewService(service)
        with session_scope() as session:
            preview = preview_service.preview(session, request)
            typer.echo(
                json.dumps(
                    {k: v for k, v in preview.items() if k != "preview_token"}, ensure_ascii=False
                )
            )
            if not yes:
                typer.confirm("确认开始身份采集？", abort=True)
            run_id = preview_service.start(session, preview["preview_token"])
        typer.echo(
            json.dumps(
                {
                    "run_id": run_id,
                    "assets_url": f"/assets?source=scan&run_id={run_id}&view=grouped",
                }
            )
        )
    except ValidationError:
        handle_cli_error(InputValidationError("身份或采集选项无效。"))
    except GardenError as error:
        handle_cli_error(error)


@app.command("import")
def import_state(
    path: Path,
    profile_id: int = typer.Option(...),
    login_url: str = typer.Option(...),
    validate_url: str = typer.Option(...),
    success_selector: str | None = None,
    success_text: str | None = None,
):
    try:
        with path.open("rb") as stream:
            raw = stream.read(1024 * 1024 + 1)
        if len(raw) > 1024 * 1024:
            raise InputValidationError("状态文件超过 1 MiB。")
        proof = config(profile_id, login_url, validate_url, success_selector, success_text)
        with session_scope() as session:
            result = IdentitySessionService().import_state(session, profile_id, raw.decode(), proof)
        typer.echo(result.model_dump_json())
    except (OSError, UnicodeError):
        handle_cli_error(InputValidationError("无法读取 UTF-8 状态文件。"))
    except GardenError as error:
        handle_cli_error(error)


@app.command("login")
def login(
    profile_id: int,
    login_url: str = typer.Option(...),
    validate_url: str = typer.Option(...),
    success_selector: str | None = None,
    success_text: str | None = None,
    api_url: str = "http://127.0.0.1:8000",
):
    """由正在运行的 Garden Web 服务持有登录窗口，CLI 退出后仍可确认。"""
    try:
        proof = config(profile_id, login_url, validate_url, success_selector, success_text)
        remote(api_url, "/api/login-attempts", proof.model_dump())
    except GardenError as error:
        handle_cli_error(error)


@app.command("confirm")
def confirm(attempt_id: int, api_url: str = "http://127.0.0.1:8000"):
    try:
        remote(api_url, f"/api/login-attempts/{attempt_id}/confirm")
    except GardenError as error:
        handle_cli_error(error)


@app.command("cancel")
def cancel(attempt_id: int, api_url: str = "http://127.0.0.1:8000"):
    try:
        remote(api_url, f"/api/login-attempts/{attempt_id}/cancel")
    except GardenError as error:
        handle_cli_error(error)


@app.command("status")
def status(attempt_id: int, api_url: str = "http://127.0.0.1:8000"):
    try:
        remote(api_url, f"/api/login-attempts/{attempt_id}", method="GET")
    except GardenError as error:
        handle_cli_error(error)


@app.command("validate")
def validate(session_id: int):
    try:
        with session_scope() as session:
            result = IdentitySessionService().validate(session, session_id)
        typer.echo(result.model_dump_json())
    except GardenError as error:
        handle_cli_error(error)


@app.command("revoke")
def revoke(session_id: int):
    try:
        with session_scope() as session:
            IdentitySessionService().revoke(session, session_id)
        typer.echo(json.dumps({"session_id": session_id, "status": "revoked"}))
    except GardenError as error:
        handle_cli_error(error)


@app.command("activate")
def activate(profile_id: int):
    """使用已有自动登录配置，转换并验证浏览器状态。"""
    try:
        with session_scope() as session:
            result = IdentitySessionService().activate_automatic_profile(session, profile_id)
        typer.echo(result.model_dump_json())
    except GardenError as error:
        handle_cli_error(error)


@app.command("recover")
def recover(
    source_run_id: int,
    source_context_id: int,
    checkpoint_version: int,
    yes: bool = typer.Option(False, "--yes"),
):
    try:
        request = RecoveryRequest(
            source_run_id=source_run_id,
            source_context_id=source_context_id,
            checkpoint_version=checkpoint_version,
        )
        collection = IdentityCollectionService(dispatcher=InlineScanDispatcher())
        service = IdentityRecoveryService(storage=collection.storage, collection=collection)
        with session_scope() as session:
            preview = service.preview(session, request)
            typer.echo(preview.model_dump_json(exclude={"preview_token"}))
            if not yes:
                typer.confirm("确认使用当前身份补采已知缺口？", abort=True)
            run_id = service.start(session, request, preview.preview_token)
        typer.echo(json.dumps({"run_id": run_id}))
    except ValidationError:
        handle_cli_error(InputValidationError("补采来源无效。"))
    except GardenError as error:
        handle_cli_error(error)
