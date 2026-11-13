"""Frozen Python entrypoint; all scanning remains in the shared Garden engine."""

import argparse
import json
import os
import platform
import sys
import tempfile
from pathlib import Path


def main():
    if getattr(sys, "frozen", False):
        os.environ["PLAYWRIGHT_BROWSERS_PATH"] = str(
            Path(sys.executable).resolve().parent.parent / "browsers"
        )
    if len(sys.argv) > 1 and sys.argv[1] == "cli":
        os.environ["GARDEN_CLI_RUNTIME"] = "1"
        from app.cli.main import app

        app(args=sys.argv[2:], prog_name="garden")
        return
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "action",
        choices=[
            "probe",
            "serve",
            "inspect",
            "connect",
            "upgrade",
            "control",
            "diagnose",
            "browser-info",
            "update-prepare",
            "recover",
            "restore-backup",
        ],
    )
    parser.add_argument("--home")
    parser.add_argument("--port", type=int)
    parser.add_argument("--app")
    parser.add_argument("--expected-version")
    parser.add_argument("--config")
    parser.add_argument("--plan")
    parser.add_argument("--instance")
    parser.add_argument("--initialize", action="store_true")
    parser.add_argument("--operation", choices=["activity", "stop", "quiesce", "backup", "restore"])
    args = parser.parse_args()
    if args.action == "browser-info":
        from playwright.sync_api import sync_playwright

        with sync_playwright() as pw:
            print(json.dumps(pw.chromium.executable_path))
        return
    if args.action == "probe":
        from playwright.sync_api import sync_playwright

        from app.core.version import project_version
        from app.db.bootstrap import _migration_assets_root

        with sync_playwright() as pw, tempfile.TemporaryDirectory() as output:
            browser = pw.chromium.launch(channel="chromium", headless=True)
            page = browser.new_page(accept_downloads=True)
            page.set_content(
                '<h1 id="ready">Garden</h1><a download="garden.txt" '
                'href="data:text/plain,Garden%20offline">Download</a>'
            )
            assert page.locator("#ready").inner_text() == "Garden"
            with page.expect_download() as download:
                page.locator("a").click()
            destination = Path(output) / "garden.txt"
            download.value.save_as(destination)
            assert destination.read_text() == "Garden offline"
            report = dict(
                application="garden",
                version=project_version(),
                python=platform.python_version(),
                architecture=platform.machine(),
                frozen=bool(getattr(sys, "frozen", False)),
                chromium=browser.version,
                chromium_path=pw.chromium.executable_path,
                offline_download=True,
                migration=(
                    _migration_assets_root() / "migrations/versions/0016_project_maintenance.py"
                ).is_file(),
                python_path=sys.path,
            )
            browser.close()
        print(json.dumps(report))
        return
    if args.action == "recover":
        from app.cli.desktop_recovery import recover

        recover(args.plan)
        return
    if not args.home:
        parser.error("operation requires --home")
    os.environ["GARDEN_HOME"] = str(Path(args.home).expanduser().resolve())
    os.environ["GARDEN_CLI_RUNTIME"] = "1"
    from app.cli.paths import GardenPaths

    paths = GardenPaths.from_environment()
    if args.action == "serve":
        if not args.port or not os.environ.get("GARDEN_RUNTIME_TOKEN"):
            parser.error("serve requires a managed instance session and port")
        import uvicorn

        uvicorn.run("app.main:app", host="127.0.0.1", port=args.port, access_log=False)
        return
    from app.cli.desktop import connect, control, inspect_home, upgrade

    if args.action == "restore-backup":
        from app.services.local_data import LocalDataService

        payload = json.load(sys.stdin)
        result = LocalDataService(
            database_url=paths.database_url, storage_directory=paths.storage_dir
        ).restore(payload["backup_directory"], paths.home)
    elif args.action == "update-prepare":
        from app.cli.desktop_recovery import prepare

        result = prepare(paths, args.app, args.expected_version, args.config, args.instance)
    elif args.action == "inspect":
        result = inspect_home(paths)
    elif args.action == "connect":
        result = connect(paths, args.initialize, expected_version=args.expected_version)
    elif args.action == "upgrade":
        result = upgrade(paths)
    elif args.action == "diagnose":
        from app.services.doctor import diagnose

        result = diagnose().model_dump(mode="json")
    else:
        result = control(
            paths,
            args.operation,
            json.load(sys.stdin) if args.operation in {"quiesce", "backup", "restore"} else None,
            expected_instance=args.instance,
        )
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        # Native entry boundary: surface an operation failure, never claim an empty success.
        print(str(error), file=sys.stderr)
        sys.exit(1)
