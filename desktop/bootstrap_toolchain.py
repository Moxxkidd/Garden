"""Install the pinned macOS Rust components inside the checkout, never globally."""

import hashlib
import json
import platform
import subprocess
import tarfile
from pathlib import Path
from urllib.request import urlopen

ROOT = Path(__file__).resolve().parents[1]


def main():
    if (platform.system(), platform.machine()) != ("Darwin", "arm64"):
        raise SystemExit("Desktop Beta builds require Apple Silicon macOS.")
    versions = json.loads((ROOT / "desktop/build-versions.json").read_text())
    work = ROOT / ".verification"
    downloads = work / "downloads"
    sources = work / "toolchain-source"
    downloads.mkdir(parents=True, exist_ok=True)
    sources.mkdir(exist_ok=True)
    for component in versions["rust_components"].values():
        archive = downloads / component["xz_url"].rsplit("/", 1)[1]
        if not archive.exists():
            with urlopen(component["xz_url"], timeout=60) as response, archive.open("wb") as target:  # noqa: S310 - pinned official URL
                for block in iter(lambda: response.read(1024 * 1024), b""):
                    target.write(block)
        if hashlib.sha256(archive.read_bytes()).hexdigest() != component["xz_hash"]:
            raise SystemExit(f"Component digest mismatch: {archive.name}")
        with tarfile.open(archive) as package:
            package.extractall(sources, filter="data")
        installer = sources / archive.name.removesuffix(".tar.xz") / "install.sh"
        subprocess.run(
            ["sh", str(installer), "--prefix=" + str(work / "toolchain"), "--disable-ldconfig"],
            check=True,
        )
    llvm = work / "toolchain/lib/rustlib/aarch64-apple-darwin/lib/libLLVM.dylib"
    if not llvm.exists():
        llvm.symlink_to("../../../libLLVM.dylib")


if __name__ == "__main__":
    main()
