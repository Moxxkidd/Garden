"""Build the native desktop installer and record its digest."""

import hashlib
import json
import os
import platform
import subprocess
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main():
    pins = json.loads((ROOT / "desktop/build-versions.json").read_text())
    host = platform.system(), platform.machine().lower()
    if host == ("Windows", "amd64"):
        pins.update(pins.pop("windows"))
        bundle_type = "nsis"
    elif host == ("Darwin", "arm64"):
        bundle_type = "app"
    else:
        raise SystemExit(f"No native installer build for {host}.")
    version = subprocess.check_output(["rustc", "--version"], text=True).split()[1]
    if version != pins["rust"]:
        raise SystemExit(f"Use the pinned Rust {pins['rust']}; found {version}.")
    env = {k: v for k, v in os.environ.items() if not k.startswith(("APPLE_", "TAURI_SIGNING_"))}
    if bundle_type == "app":
        env["MACOSX_DEPLOYMENT_TARGET"] = pins["macos_minimum"]
    subprocess.run(
        [
            "node",
            str(ROOT / "desktop/node_modules/@tauri-apps/cli/tauri.js"),
            "build",
            "--target",
            pins["target"],
            "--bundles",
            bundle_type,
            *(["--no-sign"] if bundle_type == "nsis" else []),
            "--",
            "--locked",
        ],
        cwd=ROOT / "desktop",
        env=env,
        check=True,
    )
    target = Path(env.get("CARGO_TARGET_DIR", ROOT / "desktop/src-tauri/target"))
    if not target.is_absolute():
        target = ROOT / "desktop/src-tauri" / target
    bundle = target / pins["target"] / "release/bundle"
    app_version = json.loads((ROOT / "desktop/package.json").read_text())["version"]
    if bundle_type == "app":
        artifact = build_dmg(bundle, app_version)
    else:
        artifact = bundle / f"nsis/Garden_{app_version}_x64-setup.exe"

    with artifact.open("rb") as handle:
        sha256 = hashlib.file_digest(handle, "sha256").hexdigest()
    source_head = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
    source_dirty = bool(subprocess.check_output(["git", "status", "--porcelain"], cwd=ROOT))
    report = dict(
        source_head=source_head,
        source_dirty=source_dirty,
        platform=platform.platform(),
        pins=pins,
        artifacts=[dict(file=str(artifact), bytes=artifact.stat().st_size, sha256=sha256)],
    )
    if bundle_type == "app":
        report["app_bytes"] = sum(
            p.stat().st_size
            for p in (bundle / "macos/Garden.app").rglob("*")
            if p.is_file() and not p.is_symlink()
        )
    (ROOT / ".verification/package-report.json").write_text(json.dumps(report, indent=2))
    print(json.dumps(report, indent=2))


def build_dmg(bundle, app_version):
    app = bundle / "macos/Garden.app"
    subprocess.run(["codesign", "--verify", "--deep", "--strict", str(app)], check=True)
    # Native DMG creation avoids Finder automation; this is the single local packaging path.
    dmg = bundle / f"dmg/Garden_{app_version}_aarch64.dmg"
    dmg.parent.mkdir(exist_ok=True)
    # File Provider can add FinderInfo after verification in Documents. Stage
    # outside that managed tree so the image preserves the verified signature.
    with tempfile.TemporaryDirectory(prefix="garden-dmg-", dir="/private/tmp") as directory:
        stage = Path(directory)
        subprocess.run(
            ["/usr/bin/ditto", "--noextattr", "--norsrc", str(app), str(stage / "Garden.app")],
            check=True,
        )
        subprocess.run(
            ["codesign", "--verify", "--deep", "--strict", str(stage / "Garden.app")], check=True
        )
        (stage / "Applications").symlink_to("/Applications")
        subprocess.run(
            [
                "hdiutil",
                "create",
                "-volname",
                "Garden",
                "-srcfolder",
                str(stage),
                "-format",
                "UDZO",
                "-ov",
                str(dmg),
            ],
            check=True,
        )

    return dmg


if __name__ == "__main__":
    main()
