from pathlib import Path

from typer.testing import CliRunner

from app.cli.main import app
from app.core.settings import Settings
from app.schemas.scan import ScanOptions

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def test_v030_release_metadata_and_cli_version_are_aligned() -> None:
    pyproject = (PROJECT_ROOT / "pyproject.toml").read_text(encoding="utf-8")

    assert 'version = "0.3.0"' in pyproject
    assert Settings().project_version == "0.3.0"
    result = CliRunner().invoke(app, ["version"])
    assert result.exit_code == 0
    assert result.stdout.strip() == "Garden 0.3.0"


def test_v030_preserves_the_quick_scan_network_identity() -> None:
    assert ScanOptions().user_agent == "Garden-Authorized-Asset-Scanner/0.2"


def test_v030_documents_guided_and_automated_passive_coverage() -> None:
    readme = (PROJECT_ROOT / "README.md").read_text(encoding="utf-8")
    deployment = (PROJECT_ROOT / "docs" / "deployment.md").read_text(encoding="utf-8")

    for text in (readme, deployment):
        assert "garden coverage URL" in text
        assert "--non-interactive" in text
        assert "ephemeral-file://" in text
        assert "主动权限重放未执行" in text
        assert "覆盖差异不是已确认漏洞" in text
