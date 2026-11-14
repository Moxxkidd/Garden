"""Build the fixed macOS arm64 local Beta and record artifact digests."""

import hashlib
import json
import os
import platform
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main():
    pins = json.loads((ROOT / "desktop/build-versions.json").read_text())
    if (platform.system(), platform.machine()) != ("Darwin", "arm64"):
        raise SystemExit("Only Apple Silicon macOS is supported by this Beta.")
    version = subprocess.check_output(["rustc", "--version"], text=True).split()[1]
    if version != pins["rust"]:
        raise SystemExit(f"Use the pinned Rust {pins['rust']}; found {version}.")
    # No signing credentials are read by this local builder; '-' is ad-hoc signing.
    env = {k: v for k, v in os.environ.items() if not k.startswith(("APPLE_", "TAURI_SIGNING_"))}
    env["MACOSX_DEPLOYMENT_TARGET"] = pins["macos_minimum"]
    subprocess.run(
        [
            str(ROOT / "desktop/node_modules/.bin/tauri"),
            "build",
            "--target",
            pins["target"],
            "--bundles",
            "app",
        ],
        cwd=ROOT / "desktop",
        env=env,
        check=True,
    )
    target = Path(env.get("CARGO_TARGET_DIR", ROOT / "desktop/src-tauri/target"))
    bundle = target / pins["target"] / "release/bundle"
    app = bundle / "macos/Garden.app"
    subprocess.run(["codesign", "--verify", "--deep", "--strict", str(app)], check=True)
    # Native DMG creation avoids Finder automation; this is the single local packaging path.
    import tempfile

    app_version = json.loads((ROOT / "desktop/package.json").read_text())["version"]
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

    artifacts = []
    for file in [dmg]:
        digest = hashlib.sha256()
        with file.open("rb") as handle:
            for block in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(block)
        artifacts.append(dict(file=str(file), bytes=file.stat().st_size, sha256=digest.hexdigest()))
    source_head = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
    source_dirty = bool(subprocess.check_output(["git", "status", "--porcelain"], cwd=ROOT))
    report = dict(
        source_head=source_head,
        source_dirty=source_dirty,
        platform=platform.platform(),
        pins=pins,
        app_bytes=sum(
            p.stat().st_size for p in app.rglob("*") if p.is_file() and not p.is_symlink()
        ),
        artifacts=artifacts,
    )
    (ROOT / ".verification/package-report.json").write_text(json.dumps(report, indent=2))
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
