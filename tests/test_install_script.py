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
    assert "gardenctl" in script
    assert ">>" not in script
    assert 'source "$GARDEN_SHELL_CONFIG"' not in script
