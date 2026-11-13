"""Fixed native-shell entry operations, sharing the ordinary local runtime and data APIs."""

import json
from dataclasses import asdict
from urllib.request import Request, urlopen

from app.cli.web_runtime import WebRuntimeError, WebRuntimeManager
from app.core.settings import get_settings
from app.core.version import project_version
from app.db.bootstrap import ensure_database_at_head, upgrade_database
from app.services.doctor import check_database, check_proxy


def inspect_home(paths):
    return dict(
        home=str(paths.home.resolve()),
        exists=paths.database_file.exists(),
        database=check_database(paths.database_url).model_dump(mode="json"),
        proxy=check_proxy(get_settings()).model_dump(mode="json"),
    )


def connect(paths, initialize=False, *, expected_version=None):
    if expected_version is not None and expected_version != project_version():
        raise WebRuntimeError(
            f"App {expected_version} 与引擎 {project_version()} 不一致；未打开数据目录。"
        )
    if not paths.database_file.exists():
        if not initialize:
            raise WebRuntimeError("请先确认创建新数据目录。")
        paths.ensure_directories()
        upgrade_database(paths.database_url)
    ensure_database_at_head(paths.database_url, environment="production", auto_migrate=False)
    manager = WebRuntimeManager(paths=paths, startup_timeout_seconds=20)
    runtime = manager.ensure()
    return {
        **asdict(runtime),
        "owned": manager.started_by_this_command,
        "browser_url": runtime.browser_url,
    }


def control(paths, operation, payload=None, *, expected_instance=None):
    manager = WebRuntimeManager(paths=paths)
    runtime = manager._load_state()
    if (
        runtime is not None
        and expected_instance is not None
        and runtime.instance_id != expected_instance
    ):
        raise WebRuntimeError("本地实例已变化；未操作后来启动的引擎。")
    if runtime is None or not manager._is_healthy(runtime):
        raise WebRuntimeError("Garden 实例握手未通过；未操作外部进程。")
    if operation == "stop":
        request_control(runtime, "/_garden/quiesce", dict(cancel=True))
        return dict(stopped=manager.stop())
    routes = {
        "activity": "/_garden/activity",
        "quiesce": "/_garden/quiesce",
        "backup": "/_garden/backup",
        "restore": "/api/storage/restore",
    }
    return request_control(runtime, routes[operation], payload)


def request_control(runtime, route, payload):
    request = Request(
        runtime.base_url + route,
        data=json.dumps(payload).encode() if payload is not None else None,
        headers={
            "Authorization": "Bearer " + runtime.session_token,
            "Content-Type": "application/json",
        },
    )
    with urlopen(request, timeout=120) as response:  # noqa: S310 - authenticated fixed loopback
        return json.loads(response.read())


def upgrade(paths):
    from app.services.collection_executor import executor_operation

    manager = WebRuntimeManager(paths=paths)
    existing = manager._load_state()
    if existing is not None and manager._is_process_alive(existing.pid):
        raise WebRuntimeError("请先用原版本停止当前引擎，再确认备份升级；未修改数据库。")
    with executor_operation(paths.database_url):
        return dict(backup=str(upgrade_database(paths.database_url)))
