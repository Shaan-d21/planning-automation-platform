"""Tests for configurable Planning process definitions."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.models.planning_process import (
    PlanningProcessStepType,
    ProcessContextMode,
)
from app.services.planning_process_catalog_service import (
    PlanningProcessCatalogService,
)
from app.utils.exceptions import ConfigurationError


def _catalog(path: Path) -> None:
    path.write_text(
        json.dumps(
            {
                "processes": [
                    {
                        "code": "MONTHLY",
                        "displayName": "Monthly Forecast",
                        "cycleCode": "MONTHLY_FORECAST",
                        "contextMode": "PIPELINE_DEFAULTS",
                        "steps": [
                            {"type": "PREFLIGHT", "name": "Validate"},
                            {
                                "type": "REFRESH_CUBE",
                                "name": "Refresh",
                                "enabled": False,
                                "parameters": {"jobName": "Refresh_Cube"},
                            },
                            {
                                "type": "RUN_PIPELINE",
                                "name": "Pipeline",
                            },
                            {
                                "type": "GENERATE_REPORT",
                                "name": "Report",
                                "parameters": {
                                    "reportName": "Revenue Report"
                                },
                            },
                        ],
                    }
                ]
            }
        ),
        encoding="utf-8",
    )


def test_catalog_loads_ordered_process_steps(tmp_path: Path) -> None:
    path = tmp_path / "processes.json"
    _catalog(path)

    definition = PlanningProcessCatalogService().get(path, "monthly")

    assert definition.display_name == "Monthly Forecast"
    assert definition.cycle_code == "MONTHLY_FORECAST"
    assert (
        definition.context_mode is ProcessContextMode.PIPELINE_DEFAULTS
    )
    assert tuple(step.step_type for step in definition.steps) == (
        PlanningProcessStepType.PREFLIGHT,
        PlanningProcessStepType.REFRESH_CUBE,
        PlanningProcessStepType.RUN_PIPELINE,
        PlanningProcessStepType.GENERATE_REPORT,
    )
    assert definition.steps[1].enabled_by_default is False


def test_catalog_rejects_unsupported_step_type(tmp_path: Path) -> None:
    path = tmp_path / "processes.json"
    _catalog(path)
    document = json.loads(path.read_text(encoding="utf-8"))
    document["processes"][0]["steps"][0]["type"] = "UNKNOWN"
    path.write_text(json.dumps(document), encoding="utf-8")

    with pytest.raises(ConfigurationError, match="unsupported type"):
        PlanningProcessCatalogService().load(path)


def test_catalog_rejects_unsupported_context_mode(tmp_path: Path) -> None:
    path = tmp_path / "processes.json"
    _catalog(path)
    document = json.loads(path.read_text(encoding="utf-8"))
    document["processes"][0]["contextMode"] = "AUTOMATIC_MAGIC"
    path.write_text(json.dumps(document), encoding="utf-8")

    with pytest.raises(ConfigurationError, match="contextMode"):
        PlanningProcessCatalogService().load(path)
