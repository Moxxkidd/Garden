"""用户级 Garden CLI 的运行目录约定。"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class GardenPaths:
    """集中解析正式 CLI 的用户数据目录。"""

    home: Path

    @classmethod
    def from_environment(cls) -> GardenPaths:
        configured = os.environ.get("GARDEN_HOME")
        home = Path(configured).expanduser() if configured else Path.home() / ".garden"
        return cls(home=home)

    @property
    def config_file(self) -> Path:
        return self.home / "config.env"

    @property
    def database_file(self) -> Path:
        return self.home / "garden.db"

    @property
    def database_url(self) -> str:
        return f"sqlite+pysqlite:///{self.database_file}"

    @property
    def reports_dir(self) -> Path:
        return self.home / "reports"

    @property
    def storage_dir(self) -> Path:
        return self.home / "storage"

    @property
    def logs_dir(self) -> Path:
        return self.home / "logs"

    @property
    def runtime_dir(self) -> Path:
        return self.home / "runtime"

    @property
    def server_state_file(self) -> Path:
        return self.runtime_dir / "server.json"

    @property
    def server_pid_file(self) -> Path:
        return self.runtime_dir / "server.pid"

    @property
    def web_log_file(self) -> Path:
        return self.logs_dir / "web.log"

    def ensure_directories(self) -> None:
        for directory in (
            self.home,
            self.reports_dir,
            self.storage_dir,
            self.logs_dir,
            self.runtime_dir,
        ):
            directory.mkdir(parents=True, exist_ok=True)


def formal_runtime_paths() -> GardenPaths | None:
    """正式 CLI 运行时返回用户目录约定；仓库开发模式保持原路径。"""

    if os.environ.get("GARDEN_CLI_RUNTIME") != "1":
        return None
    return GardenPaths.from_environment()
