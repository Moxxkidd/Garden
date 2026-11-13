"""停止本机 Web UI 并中断尚未结束的扫描。"""

from __future__ import annotations

from app.cli.local_api import LocalApiError
from app.cli.paths import GardenPaths
from app.cli.utils import console, handle_cli_error
from app.cli.web_runtime import WebRuntimeError, WebRuntimeManager


def stop() -> None:
    """中断全部活动扫描并停止本机 Garden Web UI。"""

    try:
        manager = WebRuntimeManager(paths=GardenPaths.from_environment())
        runtime = manager._load_state()
        if runtime is not None and manager._is_healthy(runtime):
            from app.cli.local_api import LocalScanApi

            LocalScanApi(runtime.base_url, session_token=runtime.session_token)._request_json(
                "POST", "/_garden/quiesce", {"cancel": True}
            )
        stopped = manager.stop()
        console.print("本机 Garden Web UI 已停止。" if stopped else "本机 Garden Web UI 未运行。")
    except (WebRuntimeError, LocalApiError) as error:
        handle_cli_error(error)
