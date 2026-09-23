"""Explicit diagnostics, separate from application bootstrap and repair operations."""

import ast
import errno
import importlib.metadata
import json
import os
import platform
import shlex
import socket
import sqlite3
import subprocess
import sys
from collections.abc import Callable
from contextlib import closing
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field, computed_field
from sqlalchemy.engine import make_url

import app
from app.cli.paths import GardenPaths
from app.core.settings import Settings
from app.redaction.service import RedactionService


class DoctorCheck(BaseModel):
    id: str
    status: Literal["ok", "action_required", "unknown"]
    message: str
    next_step: str | None = None


class DoctorReport(BaseModel):
    schema_version: int = 1
    checks: list[DoctorCheck] = Field(default_factory=list)

    @computed_field
    @property
    def exit_code(self) -> int:
        if any(check.status == "action_required" for check in self.checks):
            return 1
        return 2 if any(check.status == "unknown" for check in self.checks) else 0


def diagnose(ui_port: int | None = None) -> DoctorReport:
    report = DoctorReport()
    report.checks.append(safe_check("installation", check_installation))
    try:
        paths = GardenPaths.from_environment()
    except (ValueError, OSError, RuntimeError):
        report.checks.append(
            DoctorCheck(
                id="configuration",
                status="action_required",
                message="无法解析 Garden 数据目录。",
                next_step="检查 GARDEN_HOME 是否为有效的本地目录路径。",
            )
        )
        report.checks.extend(
            DoctorCheck(id=name, status="unknown", message="数据目录无效，未执行检查。")
            for name in (
                "database",
                "service",
                "directory_reports",
                "directory_storage",
                "directory_runtime",
            )
        )
        report.checks.append(safe_check("browser", check_browser))
        return report
    formal = os.environ.get("GARDEN_CLI_RUNTIME") == "1"
    config = paths.config_file if formal else Path.cwd() / ".env"
    source = "正式安装 config.env" if formal else "当前工作目录 .env"
    source += "（环境变量优先；不显示配置内容）"

    try:
        settings = Settings()
    except (ValueError, OSError, RuntimeError):
        report.checks.append(
            DoctorCheck(
                id="configuration",
                status="action_required",
                message=f"{source}：配置无法读取或校验失败；不显示原始值及异常。",
                next_step="检查环境变量和当前模式的配置文件，再运行 garden doctor。",
            )
        )
        report.checks.extend(
            [
                DoctorCheck(id="database", status="unknown", message="配置无效，未检查数据库。"),
                DoctorCheck(id="service", status="unknown", message="配置无效，未探测本地端口。"),
            ]
        )
    else:
        report.checks.append(
            DoctorCheck(
                id="configuration",
                status="ok",
                message=f"{source}：{display_path(config)}；配置校验通过，不代表目标可访问。",
            )
        )
        report.checks.append(safe_check("database", lambda: check_database(settings.database_url)))
        report.checks.append(
            safe_check("service", lambda: check_service(paths, ui_port, settings.api_port))
        )
    report.checks.append(safe_check("browser", check_browser))
    directories = {
        "reports": paths.reports_dir if formal else Path.cwd() / "exports" / "scan-reports",
        "storage": paths.storage_dir if formal else Path.cwd() / "data",
        "runtime": paths.runtime_dir,
    }
    for name, path in directories.items():
        report.checks.append(
            safe_check(
                f"directory_{name}", lambda name=name, path=path: check_directory(name, path)
            )
        )
    return report


def check_database(database_url: str) -> DoctorCheck:
    def result(status, message, next_step=None):
        return DoctorCheck(id="database", status=status, message=message, next_step=next_step)

    try:
        url = make_url(database_url)
        if url.get_backend_name() != "sqlite":
            return result("unknown", "非本地 SQLite 数据库未连接；本检查不访问远程数据库。")
        if not url.database or url.database == ":memory:" or url.host or url.query:
            return result("unknown", "内存数据库或带 URI 选项的数据库无法进行只读文件诊断。")
        path = Path(url.database).absolute()
        if not path.exists():
            return result("action_required", "数据库文件不存在；尚未创建。", "gardenctl db upgrade")
        if not path.is_file():
            return result(
                "action_required", "数据库路径不是普通文件。", "检查 GARDEN_DATABASE_URL。"
            )
        if any(Path(str(path) + suffix).exists() for suffix in ("-wal", "-journal")):
            return result(
                "unknown",
                "数据库存在 WAL 或事务日志；为避免修改文件或读取过期状态，跳过检查。",
                "等待数据库停止写入后重新运行 garden doctor。",
            )
        before = path.stat()
        # immutable avoids creating SQLite sidecars; active WAL databases are excluded above.
        with closing(
            sqlite3.connect(path.as_uri() + "?mode=ro&immutable=1", uri=True, timeout=1)
        ) as connection:
            connection.execute("PRAGMA query_only=ON")
            connection.execute("PRAGMA trusted_schema=OFF")
            tables = {
                row[0]
                for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")
            }
            revisions = (
                connection.execute("SELECT version_num FROM alembic_version LIMIT 2").fetchall()
                if "alembic_version" in tables
                else []
            )
        after = path.stat()
        if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns) or any(
            Path(str(path) + suffix).exists() for suffix in ("-wal", "-journal")
        ):
            return result("unknown", "检查期间数据库发生变化，请稍后重试。")
        if not revisions:
            if tables - {"alembic_version"}:
                return result(
                    "action_required",
                    "现有数据库没有迁移版本，不能确认历史基线。",
                    "先备份并确认这是 Garden 历史数据库；"
                    "核对基线后再考虑 gardenctl db stamp-existing。",
                )
            return result("action_required", "数据库尚未初始化。", "gardenctl db upgrade")
        package = Path(app.__file__).resolve().parent
        migrations = package / "db" / "migration_assets" / "migrations"
        if not migrations.is_dir():
            migrations = package.parent / "migrations"
        head, known = migration_metadata(migrations)
        if len(revisions) != 1 or revisions[0][0] not in known:
            return result(
                "unknown",
                "数据库版本不属于当前安装的已知迁移；可能由其他版本创建。",
                "核对当前 Garden 安装版本；不要盲目升级或降级数据库。",
            )
        revision = revisions[0][0]
        if revision != head:
            return result(
                "action_required",
                f"数据库版本 {revision}，当前安装要求 {head}。",
                "先备份数据库，然后运行 gardenctl db upgrade。",
            )
        return result("ok", f"数据库可只读打开，迁移版本 {head} 已匹配；未执行完整性修复。")
    except Exception:  # noqa: BLE001 - untrusted database/config errors must never print credentials
        return result(
            "unknown",
            "无法安全读取数据库或迁移信息；未执行修复。",
            "检查文件权限、数据库格式及当前安装的迁移文件。",
        )


def display_path(path: Path) -> str:
    value = str(path.absolute())
    home = str(Path.home())
    if value == home or value.startswith(home + os.sep):
        value = "~" + value[len(home) :]
    return RedactionService().redact_text(value, limit=500)


def check_installation() -> DoctorCheck:
    try:
        version = importlib.metadata.version("garden")
    except importlib.metadata.PackageNotFoundError:
        version = "未找到发行包元数据"
    entries = []
    for directory in os.get_exec_path():
        path = Path(directory) / ("garden.exe" if os.name == "nt" else "garden")
        if path.is_file() and os.access(path, os.X_OK):
            resolved = path.resolve()
            if resolved not in entries:
                entries.append(resolved)
    message = (
        f"发行包版本：{RedactionService().redact_text(version)}；"
        f"Python {platform.python_version()}；"
        f"解释器：{display_path(Path(sys.executable))}；模块：{display_path(Path(app.__file__))}。"
    )
    if entries:
        message += " PATH 中的 Garden 入口：" + "；".join(
            display_path(path) for path in entries[:3]
        )
    else:
        message += " PATH 中未找到 garden；当前可能通过 Python 模块运行。"
    return DoctorCheck(
        id="installation",
        status="unknown" if len(entries) != 1 else "ok",
        message=message,
        next_step="核对上方解释器、模块和 PATH 首个入口；多份入口不等于同一安装。",
    )


def check_browser() -> DoctorCheck:
    # Ask the installed Playwright driver for its exact revision-specific path.
    # No browser is launched; -B disables Python bytecode writes, -I isolates cwd/PYTHONPATH.
    script = (
        "import json; from playwright.sync_api import sync_playwright; "
        "p=sync_playwright().start(); "
        "print(json.dumps(p.chromium.executable_path)); p.stop()"
    )
    try:
        process = subprocess.run(
            [sys.executable, "-I", "-B", "-c", script],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
        if process.returncode != 0:
            raise ValueError("driver unavailable")
        value = json.loads(process.stdout)
        if not isinstance(value, str) or not value or not Path(value).is_absolute():
            raise ValueError("invalid browser path")
        path = Path(value)
        if not path.is_file():
            return DoctorCheck(
                id="browser",
                status="action_required",
                message="当前 Playwright 所需 Chromium 文件缺失。",
                next_step=(
                    "使用当前解释器安装："
                    f"{shlex.quote(sys.executable)} -m playwright install chromium"
                ),
            )
        if not os.access(path, os.X_OK):
            return DoctorCheck(
                id="browser",
                status="action_required",
                message="Chromium 文件缺少执行权限。",
                next_step="检查浏览器安装目录权限；必要时用当前解释器重新安装 Chromium。",
            )
        return DoctorCheck(
            id="browser",
            status="ok",
            message=(
                f"Chromium 文件存在且可执行：{display_path(path)}；"
                "未启动验证系统依赖或实际运行能力。"
            ),
        )
    except (OSError, ValueError, subprocess.SubprocessError):
        return DoctorCheck(
            id="browser",
            status="unknown",
            message="无法取得 Playwright 浏览器路径；未启动浏览器。",
            next_step="检查当前解释器中的 Playwright 安装；错误原文已省略。",
        )


def check_directory(name: str, path: Path) -> DoctorCheck:
    try:
        if not path.exists():
            return DoctorCheck(
                id=f"directory_{name}",
                status="unknown",
                message=f"目录尚不存在：{display_path(path)}；未创建，实际写入能力未确认。",
                next_step="核对上级目录权限；目录会在实际使用时按原流程创建。",
            )
        if not path.is_dir() or not os.access(path, os.R_OK | os.W_OK | os.X_OK):
            return DoctorCheck(
                id=f"directory_{name}",
                status="action_required",
                message=f"路径不是目录或当前进程权限不足：{display_path(path)}。",
                next_step="核对该路径及所属用户权限后重新运行 garden doctor。",
            )
        return DoctorCheck(
            id=f"directory_{name}",
            status="ok",
            message=f"目录权限检查通过：{display_path(path)}；未写入测试文件，不保证磁盘空间充足。",
        )
    except OSError:
        return DoctorCheck(
            id=f"directory_{name}", status="unknown", message="无法读取目录权限信息。"
        )


def check_service(paths: GardenPaths, ui_port: int | None, default_port: int) -> DoctorCheck:
    def result(status, message, next_step=None):
        return DoctorCheck(id="service", status=status, message=message, next_step=next_step)

    try:
        state = None
        if paths.server_state_file.exists():
            if not paths.server_state_file.is_file():
                raise ValueError("state is not a regular file")
            with paths.server_state_file.open(encoding="utf-8") as stream:
                raw = stream.read(8193)
            if len(raw) > 8192:
                raise ValueError("oversized state")
            state = json.loads(raw)
            if not isinstance(state, dict) or state.get("home") != str(paths.home):
                raise ValueError("invalid state home")
            if type(state.get("port")) is not int or not 1 <= state["port"] <= 65535:
                raise ValueError("invalid state port")
            if type(state.get("pid")) is not int or not 0 < state["pid"] <= 2**31 - 1:
                raise ValueError("invalid state pid")
        port = ui_port if ui_port is not None else state["port"] if state else default_port
        if not 1 <= port <= 65535:
            return result(
                "action_required", "本地端口超出有效范围。", "检查 GARDEN_API_PORT 或 --ui-port。"
            )
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
            probe.settimeout(0.3)
            code = probe.connect_ex(("127.0.0.1", port))
        if code == errno.ECONNREFUSED:
            if state and state["port"] == port:
                return result(
                    "action_required",
                    f"状态文件记录了端口 {port}，但未检测到监听；状态可能已过期。",
                    "核对本地运行进程和日志；doctor 不删除状态文件。",
                )
            return result(
                "ok", f"127.0.0.1:{port} 未检测到监听；扫描可按原流程启动服务，端口状态可能变化。"
            )
        if code != 0:
            return result("unknown", f"无法确认 127.0.0.1:{port} 的监听状态。")
        if state and state["port"] == port:
            try:
                os.kill(state["pid"], 0)
            except ProcessLookupError:
                return result(
                    "action_required",
                    f"端口 {port} 接受连接，但状态文件的进程已不存在。",
                    "检查端口占用；可用 garden scan <已授权入口> --ui-port <其他端口>。",
                )
            except OSError:
                return result("unknown", "端口接受连接，但无法确认状态文件记录的进程。")
            return result(
                "unknown",
                f"端口 {port} 接受连接且记录的进程存在；未核实服务身份及健康状态。",
                "核对本地服务日志；doctor 不发送 HTTP 健康请求或停止进程。",
            )
        return result(
            "unknown",
            f"端口 {port} 已有监听，但没有匹配的 Garden 状态记录，不能确认所属程序。",
            "检查端口占用；可用 garden scan <已授权入口> --ui-port <其他端口>。",
        )
    except (OSError, ValueError, TypeError, UnicodeError):
        return result(
            "unknown",
            "无法读取有效的本地服务状态；原文件保留。",
            "检查运行目录内的 server.json 和本地进程，不要依据未知状态停止其他程序。",
        )


def migration_metadata(migrations: Path) -> tuple[str, set[str]]:
    """Read literal Alembic metadata without importing or executing migration modules."""
    revisions: dict[str, str | None] = {}
    for path in sorted((migrations / "versions").glob("*.py")):
        if path.name == "__init__.py":
            continue
        values = {}
        for node in ast.parse(path.read_text(encoding="utf-8")).body:
            if isinstance(node, ast.Assign):
                for target in node.targets:
                    if isinstance(target, ast.Name) and target.id in {"revision", "down_revision"}:
                        values[target.id] = ast.literal_eval(node.value)
            elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
                if node.target.id in {"revision", "down_revision"}:
                    values[node.target.id] = ast.literal_eval(node.value)
        revision = values["revision"]
        parent = values["down_revision"]
        if not isinstance(revision, str) or (parent is not None and not isinstance(parent, str)):
            raise ValueError("unsupported migration graph")
        if revision in revisions:
            raise ValueError("duplicate revision")
        revisions[revision] = parent
    parents = {parent for parent in revisions.values() if parent is not None}
    heads = set(revisions) - parents
    if len(heads) != 1 or not parents <= revisions.keys():
        raise ValueError("unsupported migration graph")
    head = next(iter(heads))
    # Reject disconnected cycles and graphs we cannot establish without executing code.
    visited = set()
    current = head
    while current is not None and current not in visited:
        visited.add(current)
        current = revisions[current]
    if current is not None or visited != revisions.keys():
        raise ValueError("invalid migration graph")
    return head, set(revisions)


def safe_check(name: str, probe: Callable[[], DoctorCheck]) -> DoctorCheck:
    try:
        return probe()
    except Exception:  # noqa: BLE001 - keep independent diagnostics available, never echo errors
        return DoctorCheck(
            id=name,
            status="unknown",
            message="检查未能完成，原始错误已隐藏。",
            next_step="核对对应的本地配置和读取权限，再运行 garden doctor。",
        )
