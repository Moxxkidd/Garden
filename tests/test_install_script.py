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
    echo "Garden 0.2.0"
  fi
  exit 0
fi
if [ "${1:-}" = "-I" ] && [ "$(basename "${2:-}")" = "launcher.py" ]; then
  if [ ! -f "$2" ]; then
    echo "launcher does not exist: $2" >&2
    exit 44
  fi
  if [ "${3:-}" = "version" ] || [ "${3:-}" = "--version" ]; then
    echo "Garden 0.2.0"
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


def _add_python312_beside_compatible_old_python3(fake_bin):
    generic_python = fake_bin / "python3"
    versioned_python = fake_bin / "python3.12"
    versioned_python.write_text(generic_python.read_text(encoding="utf-8"), encoding="utf-8")
    versioned_python.chmod(0o755)
    generic_python.write_text(
        f"""#!/bin/sh
set -eu
if [ "${{1:-}}" = "-" ]; then
  script="$(cat)"
  case "$script" in
    *"(3, 12)"*) exit 1 ;;
    *) exit 0 ;;
  esac
fi
exec "{versioned_python}" "$@"
""",
        encoding="utf-8",
    )
    generic_python.chmod(0o755)
    return generic_python, versioned_python


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

    legacy_launcher = Path(environment["GARDEN_INSTALL_ROOT"]) / "launcher.py"
    legacy_launcher.unlink(missing_ok=True)

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
    assert garden.stdout.strip() == "Garden 0.2.0"
    assert gardenctl.returncode == 0, gardenctl.stderr
    assert gardenctl.stdout.strip() == "Garden 0.2.0"


def test_installer_discovers_usable_versioned_python_after_old_python3(tmp_path):
    environment, _ = _fake_installer_environment(tmp_path)
    fake_bin = Path(environment["PATH"].split(":", 1)[0])
    usable_python = fake_bin / "python3.12"
    (fake_bin / "python3").replace(usable_python)
    old_python = fake_bin / "python3"
    old_python.write_text(
        """#!/bin/sh
if [ "${1:-}" = "-" ]; then
  exit 1
fi
exit 99
""",
        encoding="utf-8",
    )
    old_python.chmod(0o755)

    installed = subprocess.run(
        ["bash", str(PROJECT_ROOT / "install.sh")],
        cwd=PROJECT_ROOT,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )

    assert installed.returncode == 0, installed.stderr
    assert "python3.12" in installed.stdout


def test_installer_prefers_python312_over_compatible_old_python3(tmp_path):
    environment, _ = _fake_installer_environment(tmp_path)
    fake_bin = Path(environment["PATH"].split(":", 1)[0])
    _, versioned_python = _add_python312_beside_compatible_old_python3(fake_bin)

    installed = subprocess.run(
        ["bash", str(PROJECT_ROOT / "install.sh")],
        cwd=PROJECT_ROOT,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )

    assert installed.returncode == 0, installed.stderr
    assert f"使用 Python：{versioned_python}" in installed.stdout


def test_explicit_garden_python_wins_over_discovered_python312(tmp_path):
    environment, _ = _fake_installer_environment(tmp_path)
    fake_bin = Path(environment["PATH"].split(":", 1)[0])
    generic_python, _ = _add_python312_beside_compatible_old_python3(fake_bin)
    environment["GARDEN_PYTHON"] = str(generic_python)

    installed = subprocess.run(
        ["bash", str(PROJECT_ROOT / "install.sh")],
        cwd=PROJECT_ROOT,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )

    assert installed.returncode == 0, installed.stderr
    assert f"使用 Python：{generic_python}" in installed.stdout


def test_runtime_swap_already_contains_isolated_launcher(tmp_path):
    environment, _ = _fake_installer_environment(tmp_path)
    fake_bin = Path(environment["PATH"].split(":", 1)[0])
    fake_mv = fake_bin / "mv"
    fake_mv.write_text(
        """#!/bin/sh
set -eu
destination=""
for argument do
  destination="$argument"
done
/bin/mv "$@"
if [ "$destination" = "$GARDEN_INSTALL_ROOT/runtime" ]; then
  exit 41
fi
""",
        encoding="utf-8",
    )
    fake_mv.chmod(0o755)

    installed = subprocess.run(
        ["bash", str(PROJECT_ROOT / "install.sh")],
        cwd=PROJECT_ROOT,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )

    assert installed.returncode == 41
    runtime_launcher = Path(environment["GARDEN_INSTALL_ROOT"]) / "runtime" / "launcher.py"
    assert runtime_launcher.is_file()
    assert runtime_launcher.stat().st_mode & 0o777 == 0o644


def test_upgrade_migrates_legacy_installer_generated_public_target_policy(tmp_path):
    environment, _ = _fake_installer_environment(tmp_path)
    config_path = Path(environment["GARDEN_HOME"]) / "config.env"
    config_path.parent.mkdir(parents=True)
    config_path.write_text(
        """# Garden local demo defaults
# Copy this file to .env for local development.
# Safe-by-default guardrail: keep false for local/demo use only
GARDEN_ALLOW_NON_LOCAL_TARGETS=false
GARDEN_ALLOW_PRIVATE_TARGETS=false
""",
        encoding="utf-8",
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
    migrated = config_path.read_text(encoding="utf-8")
    assert "GARDEN_ALLOW_NON_LOCAL_TARGETS=true" in migrated
    assert "GARDEN_INSTALLER_MANAGED_CONFIG_VERSION=2" in migrated
    assert "已迁移旧版自动生成的公网目标策略" in installed.stdout


def test_upgrade_preserves_explicit_custom_local_only_policy(tmp_path):
    environment, _ = _fake_installer_environment(tmp_path)
    config_path = Path(environment["GARDEN_HOME"]) / "config.env"
    config_path.parent.mkdir(parents=True)
    config_path.write_text(
        "GARDEN_ALLOW_NON_LOCAL_TARGETS=false\nGARDEN_ALLOW_PRIVATE_TARGETS=false\n",
        encoding="utf-8",
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
    assert "GARDEN_ALLOW_NON_LOCAL_TARGETS=false" in config_path.read_text(encoding="utf-8")


def test_installed_launcher_does_not_use_module_mode_from_repository_cwd(tmp_path):
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

    repository_cwd = tmp_path / "repository"
    (repository_cwd / "app" / "cli").mkdir(parents=True)
    (repository_cwd / "app" / "cli" / "main.py").write_text(
        "raise RuntimeError('shadowed source tree')\n", encoding="utf-8"
    )
    launched = subprocess.run(
        [str(bin_dir / "garden"), "version"],
        cwd=repository_cwd,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )

    assert launched.returncode == 0, launched.stderr
    assert launched.stdout.strip() == "Garden 0.2.0"
    launcher = (bin_dir / "garden").read_text(encoding="utf-8")
    assert "-m app.cli.main" not in launcher
    assert "launcher.py" in launcher
