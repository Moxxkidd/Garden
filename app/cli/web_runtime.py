"""正式 CLI 使用的本地 Garden Web UI 进程管理。"""

from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from urllib.error import URLError
from urllib.request import urlopen

from app.cli.paths import GardenPaths


class WebRuntimeError(RuntimeError):
    """本地 Web UI 无法安全复用或启动。"""


@dataclass(frozen=True)
class WebRuntime:
    pid: int
    port: int
    home: str
    started_at: str

    @property
    def base_url(self) -> str:
        return f"http://127.0.0.1:{self.port}"


class WebRuntimeManager:
    """复用或启动只监听回环地址的 Uvicorn 进程。"""

    def __init__(
        self,
        *,
        paths: GardenPaths,
        default_port: int = 8000,
        startup_timeout_seconds: float = 10.0,
    ) -> None:
        self.paths = paths
        self.default_port = default_port
        self.startup_timeout_seconds = startup_timeout_seconds

    def ensure(self, *, ui_port: int | None = None) -> WebRuntime:
        existing = self._load_state()
        if existing is not None:
            if self._is_healthy(existing):
                if ui_port is not None and existing.port != ui_port:
                    raise WebRuntimeError(
                        f"Garden Web UI 已在端口 {existing.port} 运行；"
                        "请使用相同端口或先执行 garden stop。"
                    )
                return existing
            self._remove_state()

        if ui_port is not None:
            if not self._is_port_available(ui_port):
                raise WebRuntimeError(f"指定的 Web UI 端口 {ui_port} 已被占用。")
            candidate_ports = [ui_port]
        elif self._is_port_available(self.default_port):
            candidate_ports = [self.default_port]
        else:
            candidate_ports = [self._reserve_free_port() for _ in range(3)]

        last_error: WebRuntimeError | None = None
        for port in candidate_ports:
            process = self._start_process(port)
            if self._wait_for_health(port):
                runtime = WebRuntime(
                    pid=process.pid,
                    port=port,
                    home=str(self.paths.home),
                    started_at=datetime.now(timezone.utc).isoformat(),
                )
                self._write_state(runtime)
                return runtime
            self._terminate_process(process)
            last_error = WebRuntimeError(
                f"Garden Web UI 未能在 {self.startup_timeout_seconds:g} 秒内就绪。"
            )
            if ui_port is not None:
                break

        raise last_error or WebRuntimeError("Garden Web UI 启动失败。")

    def _load_state(self) -> WebRuntime | None:
        path = self.paths.server_state_file
        if not path.is_file():
            return None
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            runtime = WebRuntime(
                pid=int(payload["pid"]),
                port=int(payload["port"]),
                home=str(payload["home"]),
                started_at=str(payload["started_at"]),
            )
        except (KeyError, TypeError, ValueError, json.JSONDecodeError):
            return None
        if runtime.home != str(self.paths.home):
            return None
        return runtime

    def _write_state(self, runtime: WebRuntime) -> None:
        self.paths.ensure_directories()
        temp_path = self.paths.runtime_dir / ".server.json.tmp"
        temp_path.write_text(json.dumps(asdict(runtime), sort_keys=True), encoding="utf-8")
        temp_path.chmod(0o600)
        temp_path.replace(self.paths.server_state_file)

    def _remove_state(self) -> None:
        self.paths.server_state_file.unlink(missing_ok=True)

    def _is_healthy(self, runtime: WebRuntime) -> bool:
        if not self._is_process_alive(runtime.pid):
            return False
        try:
            with urlopen(f"{runtime.base_url}/healthz", timeout=0.5) as response:  # noqa: S310
                return response.status == 200
        except (URLError, TimeoutError, OSError):
            return False

    def _wait_for_health(self, port: int) -> bool:
        runtime = WebRuntime(
            pid=0,
            port=port,
            home=str(self.paths.home),
            started_at="",
        )
        deadline = time.monotonic() + self.startup_timeout_seconds
        while time.monotonic() < deadline:
            try:
                with urlopen(f"{runtime.base_url}/healthz", timeout=0.5) as response:  # noqa: S310
                    if response.status == 200:
                        return True
            except (URLError, TimeoutError, OSError):
                time.sleep(0.1)
        return False

    @staticmethod
    def _is_process_alive(pid: int) -> bool:
        try:
            os.kill(pid, 0)
        except OSError:
            return False
        return True

    @staticmethod
    def _is_port_available(port: int) -> bool:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
            probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                probe.bind(("127.0.0.1", port))
            except OSError:
                return False
        return True

    @staticmethod
    def _reserve_free_port() -> int:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
            probe.bind(("127.0.0.1", 0))
            return int(probe.getsockname()[1])

    def _start_process(self, port: int) -> subprocess.Popen:
        self.paths.ensure_directories()
        environment = os.environ.copy()
        environment.update(
            {
                "GARDEN_CLI_RUNTIME": "1",
                "GARDEN_HOME": str(self.paths.home),
            }
        )
        with self.paths.web_log_file.open("a", encoding="utf-8") as log_file:
            return subprocess.Popen(
                [
                    sys.executable,
                    "-m",
                    "uvicorn",
                    "app.main:app",
                    "--host",
                    "127.0.0.1",
                    "--port",
                    str(port),
                ],
                env=environment,
                stdin=subprocess.DEVNULL,
                stdout=log_file,
                stderr=subprocess.STDOUT,
                start_new_session=True,
            )

    @staticmethod
    def _terminate_process(process: subprocess.Popen) -> None:
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                process.kill()
