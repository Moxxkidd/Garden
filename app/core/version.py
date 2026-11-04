"""Version facts: source pyproject in a checkout, distribution metadata in an install."""

import re
from importlib.metadata import version
from pathlib import Path


def project_version() -> str:
    manifest = Path(__file__).resolve().parents[2] / "pyproject.toml"
    if manifest.is_file():
        project = manifest.read_text(encoding="utf-8").split("[project]", 1)[1].split("\n[", 1)[0]
        match = re.search(r'^version\s*=\s*"([^"]+)"', project, re.MULTILINE)
        if match is None:
            raise ValueError("Missing project.version in pyproject.toml")
        return match.group(1)
    return version("garden")
