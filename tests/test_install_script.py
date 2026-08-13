import subprocess
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def test_install_script_explains_user_level_installation():
    script = PROJECT_ROOT / "install.sh"

    result = subprocess.run(
        ["bash", str(script), "--help"],
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0
    assert "~/.local/bin/garden" in result.stdout
    assert "~/.local/share/garden/runtime" in result.stdout
    assert "sudo" in result.stdout


def test_install_script_uses_launchers_without_mutating_shell_configuration():
    script = (PROJECT_ROOT / "install.sh").read_text(encoding="utf-8")

    assert "GARDEN_CLI_RUNTIME=1" in script
    assert "GARDEN_CLI_NAME" in script
    assert "gardenctl" in script
    assert ">>" not in script
    assert 'source "$GARDEN_SHELL_CONFIG"' not in script


def _fake_installer_environment(tmp_path):
    fake_bin = tmp_path / "fake-bin"
    fake_bin.mkdir()
    fake_python = fake_bin / "python3"
    fake_python.write_text(
        """#!/bin/sh
set -eu
if [ "${1:-}" = "-" ]; then
  exit 0
fi
if [ "${1:-}" = "-m" ] && [ "${2:-}" = "venv" ]; then
  target="$3"
  mkdir -p "$target/bin"
  cp "$0" "$target/bin/python"
  chmod 0755 "$target/bin/python"
  printf '#!%s/bin/python\\n' "$target" > "$target/bin/gardenctl"
  chmod 0755 "$target/bin/gardenctl"
  exit 0
fi
if [ "${1:-}" = "-m" ] && [ "${2:-}" = "pip" ]; then
  proxy_is_set=0
  if [ -n "${ALL_PROXY+x}" ] || [ -n "${all_proxy+x}" ]; then
    proxy_is_set=1
  fi
  if [ "${REJECT_SOCKS_PROXY:-0}" = "1" ] && [ "$proxy_is_set" = "1" ]; then
    echo "SOCKS proxy leaked into isolated pip" >&2
    exit 42
  fi
  exit 0
fi
if [ "${1:-}" = "-m" ] && [ "${2:-}" = "app.cli.main" ]; then
  if [ "${3:-}" = "version" ] || [ "${3:-}" = "--version" ]; then
    echo "Garden 0.1.0"
  fi
  exit 0
fi
exit 0
""",
        encoding="utf-8",
    )
    fake_python.chmod(0o755)
    home = tmp_path / "home"
    home.mkdir()
    install_root = tmp_path / "install"
    bin_dir = tmp_path / "bin"
    environment = {
        "HOME": str(home),
        "PATH": f"{fake_bin}:/usr/bin:/bin",
        "GARDEN_HOME": str(tmp_path / "garden-home"),
        "GARDEN_INSTALL_ROOT": str(install_root),
        "GARDEN_BIN_DIR": str(bin_dir),
    }
    return environment, bin_dir


def test_install_ignores_socks_all_proxy_for_isolated_pip(tmp_path):
    environment, _ = _fake_installer_environment(tmp_path)
    environment.update(
        {
            "ALL_PROXY": "socks5://127.0.0.1:1080",
            "all_proxy": "socks5://127.0.0.1:1080",
            "REJECT_SOCKS_PROXY": "1",
        }
    )

    installed = subprocess.run(
        ["bash", str(PROJECT_ROOT / "install.sh")],
        cwd=PROJECT_ROOT,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )

    assert installed.returncode == 0, installed.stderr


def test_installed_launchers_survive_runtime_move(tmp_path):
    environment, bin_dir = _fake_installer_environment(tmp_path)
    installed = subprocess.run(
        ["bash", str(PROJECT_ROOT / "install.sh")],
        cwd=PROJECT_ROOT,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )
    assert installed.returncode == 0, installed.stderr

    garden = subprocess.run(
        [str(bin_dir / "garden"), "version"],
        cwd=tmp_path,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )
    gardenctl = subprocess.run(
        [str(bin_dir / "gardenctl"), "--version"],
        cwd=tmp_path,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )

    assert garden.returncode == 0, garden.stderr
    assert garden.stdout.strip() == "Garden 0.1.0"
    assert gardenctl.returncode == 0, gardenctl.stderr
    assert gardenctl.stdout.strip() == "Garden 0.1.0"
