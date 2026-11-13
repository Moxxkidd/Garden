"""正式 CLI 使用的本地 Garden Web UI 进程管理。"""

from __future__ import annotations

import fcntl
import json
import os
import secrets
import signal
import socket
import subprocess
import sys
import time
from dataclasses import asdict, dataclass, replace
from datetime import datetime, timezone
from pathlib import Path
from urllib.error import URLError
from urllib.request import Request, urlopen

from app.cli.paths import GardenPaths
from app.core.version import project_version


class WebRuntimeError(RuntimeError):
    """本地 Web UI 无法安全复用或启动。"""


@dataclass(frozen=True)
class WebRuntime:
    pid: int
    port: int
    home: str
    started_at: str
    version: str | None = None
    instance_id: str = ""
    session_token: str = ""

    @property
    def base_url(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    @property
    def browser_url(self) -> str:
        return f"{self.base_url}/_garden/session#{self.session_token}"


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
        self.started_by_this_command = False

    def ensure(self, *, ui_port: int | None = None) -> WebRuntime:
        self.paths.ensure_directories()
        # Serialize startup only; the engine retains the existing executor ownership.
        with (self.paths.runtime_dir / "start.lock").open("a") as lock:
            os.chmod(lock.name, 0o600)
            fcntl.flock(lock, fcntl.LOCK_EX)
            return self._ensure(ui_port=ui_port)

    def _ensure(self, *, ui_port: int | None = None) -> WebRuntime:
        existing = self._load_state()
        if existing is not None:
            if self._is_healthy(existing):
                if ui_port is not None and existing.port != ui_port:
                    raise WebRuntimeError(
                        f"Garden Web UI 已在端口 {existing.port} 运行；"
                        "请使用相同端口或先执行 garden stop。"
                    )
                if existing.version != project_version():
                    raise WebRuntimeError(
                        f"已有 Garden {existing.version} 与当前 {project_version()} 不一致；"
                        "请用原版本停止服务后重试。"
                    )
                self.started_by_this_command = False
                return existing
            if self._is_process_alive(existing.pid):
                raise WebRuntimeError(
                    "已有进程未通过 Garden 实例握手（可能为旧版本或不同引擎）；"
                    "请用原版本停止服务后重试。未接管或终止该进程。"
                )
            self._remove_state()

        if ui_port is not None:
            if not self._is_port_available(ui_port):
                raise WebRuntimeError(f"指定的 Web UI 端口 {ui_port} 已被占用。")
            candidate_ports = [ui_port]
        elif self._is_port_available(self.default_port):
            candidate_ports = [self.default_port]
        else:
            candidate_ports = [self._reserve_free_port()]

        last_error: WebRuntimeError | None = None
        for port in candidate_ports:
            self._starting = WebRuntime(
                pid=0,
                port=port,
                home=str(self.paths.home.resolve()),
                started_at=datetime.now(timezone.utc).isoformat(),
                version=project_version(),
                instance_id=secrets.token_urlsafe(24),
                session_token=secrets.token_urlsafe(32),
            )
            process = self._start_process(port)
            self._starting = replace(self._starting, pid=process.pid)
            if self._wait_for_health(port):
                runtime = self._starting
                self._write_state(runtime)
                self.started_by_this_command = True
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
                version=payload.get("version"),
                instance_id=payload.get("instance_id", ""),
                session_token=payload.get("session_token", ""),
            )
        except (KeyError, TypeError, ValueError, json.JSONDecodeError):
            raise WebRuntimeError("本地实例状态无法读取；请核对 runtime/server.json。") from None
        if Path(runtime.home).resolve() != self.paths.home.resolve():
            raise WebRuntimeError("本地实例数据目录不匹配；未启动另一引擎。")
        return runtime

    def _write_state(self, runtime: WebRuntime) -> None:
        self.paths.ensure_directories()
        temp_path = self.paths.runtime_dir / ".server.json.tmp"
        descriptor = os.open(temp_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            os.fchmod(handle.fileno(), 0o600)
            json.dump(asdict(runtime), handle, sort_keys=True)
        temp_path.replace(self.paths.server_state_file)
        self.paths.server_pid_file.write_text(f"{runtime.pid}\n", encoding="utf-8")
        self.paths.server_pid_file.chmod(0o600)

    def _remove_state(self) -> None:
        self.paths.server_state_file.unlink(missing_ok=True)
        self.paths.server_pid_file.unlink(missing_ok=True)

    def stop(self) -> bool:
        """只终止状态文件确认的健康 Garden UI；不触碰不确定的外部进程。"""

        runtime = self._load_state()
        if runtime is None or not self._is_healthy(runtime):
            return False
        try:
            os.killpg(runtime.pid, signal.SIGTERM)
        except (AttributeError, OSError):
            self._remove_state()
            return False
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            if not self._is_process_alive(runtime.pid):
                self._remove_state()
                return True
            time.sleep(0.05)
        try:
            os.killpg(runtime.pid, signal.SIGKILL)
        except OSError:
            pass
        self._remove_state()
        return True

    def _is_healthy(self, runtime: WebRuntime) -> bool:
        if not runtime.instance_id or not runtime.session_token:
            return False
        if not self._is_process_alive(runtime.pid):
            return False
        request = Request(
            f"{runtime.base_url}/_garden/runtime",
            headers={"Authorization": f"Bearer {runtime.session_token}"},
        )
        try:
            with urlopen(request, timeout=0.5) as response:  # noqa: S310 - fixed loopback
                payload = json.loads(response.read())
            return payload == dict(
                application="garden",
                version=runtime.version,
                home=runtime.home,
                instance_id=runtime.instance_id,
                pid=runtime.pid,
            )
        except (URLError, TimeoutError, OSError, ValueError):
            return False

    def _wait_for_health(self, port: int) -> bool:
        deadline = time.monotonic() + self.startup_timeout_seconds
        while time.monotonic() < deadline:
            if self._is_healthy(self._starting):
                return True
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
                "GARDEN_HOME": str(self.paths.home.resolve()),
                "GARDEN_RUNTIME_TOKEN": self._starting.session_token,
                "GARDEN_RUNTIME_INSTANCE": self._starting.instance_id,
                "GARDEN_RUNTIME_PORT": str(port),
                "PYINSTALLER_RESET_ENVIRONMENT": "1",
            }
        )
        command = (
            [sys.executable, "serve", "--home", str(self.paths.home.resolve()), "--port", str(port)]
            if getattr(sys, "frozen", False)
            else [
                sys.executable,
                "-m",
                "uvicorn",
                "app.main:app",
                "--host",
                "127.0.0.1",
                "--port",
                str(port),
            ]
        )
        with self.paths.web_log_file.open("a", encoding="utf-8") as log_file:
            os.chmod(log_file.name, 0o600)
            return subprocess.Popen(
                command,
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
