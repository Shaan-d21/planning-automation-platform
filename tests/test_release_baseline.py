"""Repository-level contracts required for a releasable build."""

from __future__ import annotations

import json
from pathlib import Path

from app import __version__


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def test_backend_and_frontend_release_versions_match() -> None:
    package = json.loads(
        (PROJECT_ROOT / "frontend" / "package.json").read_text(
            encoding="utf-8"
        )
    )

    assert package["version"] == __version__


def test_release_automation_and_runbook_are_present() -> None:
    assert (PROJECT_ROOT / ".github" / "workflows" / "ci.yml").is_file()
    assert (PROJECT_ROOT / "docs" / "RELEASE_RUNBOOK.md").is_file()
    assert (PROJECT_ROOT / "pytest.ini").is_file()
