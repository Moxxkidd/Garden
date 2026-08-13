"""停止本机 Web UI 并中断尚未结束的扫描。"""

from __future__ import annotations

from app.cli.paths import GardenPaths
from app.cli.utils import console
from app.cli.web_runtime import WebRuntimeManager
from app.services.scan_application import InlineScanDispatcher, ScanApplicationService


def stop() -> None:
    """中断全部活动扫描并停止本机 Garden Web UI。"""

    interrupted = ScanApplicationService(dispatcher=InlineScanDispatcher()).interrupt_active_scans()
    manager = WebRuntimeManager(paths=GardenPaths.from_environment())
    stopped = manager.stop()
    console.print(f"已中断 {interrupted} 个活动扫描。")
    console.print("本机 Garden Web UI 已停止。" if stopped else "本机 Garden Web UI 未运行。")
