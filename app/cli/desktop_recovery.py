"""One macOS program rollback guardian. Data is never automatically downgraded."""

import json
import os
import signal
import subprocess
import time
from pathlib import Path

from app.cli.desktop import control
from app.cli.paths import GardenPaths
from app.cli.web_runtime import WebRuntimeError
from app.core.version import project_version


def prepare(paths, bundle, expected_version, configuration, instance):
    from app.services.local_data import private_directory

    if Path(bundle).stat().st_dev != paths.home.stat().st_dev:
        raise WebRuntimeError(
            "本地 Beta 更新要求程序与私有备份位于同一文件系统；未暂停引擎或替换程序。"
        )
    root = paths.runtime_dir / "updates" / str(time.time_ns())
    private_directory(root)
    control(paths, "quiesce", dict(cancel=False), expected_instance=instance)
    backup = control(
        paths, "backup", dict(destination=str(root / "data")), expected_instance=instance
    )
    previous = root / "Garden.app"
    subprocess.run(
        ["/usr/bin/ditto", "--noextattr", "--norsrc", str(bundle), str(previous)], check=True
    )
    plan = root / "plan.json"
    payload = dict(
        schema="garden-app-update-v1",
        previous_version=project_version(),
        expected_version=expected_version,
        bundle=str(Path(bundle).resolve()),
        previous=str(previous),
        data_backup=str(root / "data"),
        home=str(paths.home.resolve()),
        configuration=configuration,
        parent_pid=os.getppid(),
        note="只回退程序；新结构数据库不会被旧程序直接打开。需要数据恢复时，显式选择已验证备份到空目录；备份后修改不会保留。",
    )
    plan.write_text(json.dumps(payload, ensure_ascii=False))
    plan.chmod(0o600)
    return dict(
        plan=str(plan),
        guardian=str(previous / "Contents/Resources/engine/garden-engine"),
        data_backup=backup,
    )


def recover(plan_path):
    plan_path = Path(plan_path)
    plan = json.loads(plan_path.read_text())
    installed = Path(str(plan_path) + ".installed")
    cancelled = Path(str(plan_path) + ".cancelled")
    deadline = time.monotonic() + 300
    while not installed.exists():
        if cancelled.exists():
            return
        if time.monotonic() >= deadline:
            raise RuntimeError("updater_install_not_acknowledged; backup retained")
        time.sleep(0.2)
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        try:
            os.kill(plan["parent_pid"], 0)
        except ProcessLookupError:
            break
        time.sleep(0.1)
    else:
        raise RuntimeError("previous_app_still_running; program not replaced by guardian")
    ready = Path(str(plan_path) + ".ready")
    executable = Path(plan["bundle"]) / "Contents/MacOS/garden-desktop"
    log_path = plan_path.parent / "recovery.log"
    with log_path.open("a") as log:
        os.chmod(log_path, 0o600)
        try:
            child = subprocess.Popen(
                [
                    str(executable),
                    "--config",
                    plan["configuration"],
                    "--update-plan",
                    str(plan_path),
                ],
                stdout=log,
                stderr=log,
                start_new_session=True,
            )
        except OSError as error:
            log.write(f"New program launch failed: {error}\n")
            child = None
        deadline = time.monotonic() + 60
        while child is not None and time.monotonic() < deadline:
            if ready.exists() and ready.read_text() == plan["expected_version"]:
                Path(str(plan_path) + ".result").write_text("updated")
                return
            if child.poll() is not None:
                break
            time.sleep(0.2)
        if child is not None and child.poll() is None:
            child.send_signal(signal.SIGTERM)
            child.wait(timeout=10)
        marker = Path(str(plan_path) + ".engine")
        if marker.exists():
            owned = json.loads(marker.read_text())
            if owned["owned"]:
                try:
                    control(
                        GardenPaths(Path(plan["home"])),
                        "stop",
                        expected_instance=owned["instance_id"],
                    )
                except WebRuntimeError as error:
                    log.write(f"New engine no longer owned: {error}\n")
        # Keep the failed program for diagnosis, leave data completely untouched.
        failed = plan_path.parent / "failed.app"
        os.rename(plan["bundle"], failed)
        subprocess.run(
            ["/usr/bin/ditto", "--noextattr", "--norsrc", plan["previous"], plan["bundle"]],
            check=True,
        )
        Path(str(plan_path) + ".result").write_text("program_rolled_back; data_not_reverted")
        subprocess.Popen(
            [str(executable), "--config", plan["configuration"]],
            stdout=log,
            stderr=log,
            start_new_session=True,
        )
