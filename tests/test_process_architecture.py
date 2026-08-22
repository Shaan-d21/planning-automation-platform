"""Tests for offline Process ownership and migration assessment."""

from __future__ import annotations

import json
from pathlib import Path

from app.application.process_designer import (
    ProcessDesignerApplicationService,
)
from app.config.settings import Settings
from app.models.pipeline_process_design import ProcessStepOwnership
from app.models.planning_process import PlanningProcessStepType


def _settings(tmp_path: Path) -> Settings:
    process_catalog = tmp_path / "processes.json"
    process_catalog.write_text(
        json.dumps(
            {
                "processes": [
                    {
                        "code": "FORECAST",
                        "displayName": "Monthly Forecast",
                        "cycleCode": "FORECAST_CYCLE",
                        "steps": [
                            {"type": "PREFLIGHT", "name": "Validate"},
                            {
                                "type": "UPDATE_VARIABLES",
                                "name": "Set Forecast Variables",
                            },
                            {
                                "type": "RUN_PIPELINE",
                                "name": "Run Oracle Pipeline",
                                "parameters": {
                                    "runtimeVariableNames": [
                                        "YEAR",
                                        "STARTPERIOD",
                                    ],
                                    "pipelineStages": [
                                        {
                                            "name": "LOAD",
                                            "displayName": "Load Data",
                                        }
                                    ],
                                },
                            },
                            {
                                "type": "RUN_DATA_MAP",
                                "name": "Push Reporting Data",
                                "enabled": False,
                            },
                            {
                                "type": "VALIDATE_DATA",
                                "name": "Reconcile Source and Target",
                                "enabled": False,
                            },
                        ],
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    cycle_catalog = tmp_path / "cycles.json"
    cycle_catalog.write_text(
        json.dumps(
            {
                "cycles": [
                    {
                        "code": "FORECAST_CYCLE",
                        "displayName": "Monthly Forecast",
                        "pipelineCode": "PIPE01",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    return Settings(
        epm_base_url="https://example.oraclecloud.com",
        epm_username="administrator",
        epm_password="secret",
        application_name="Vision",
        planning_process_catalog_file=process_catalog,
        planning_cycle_catalog_file=cycle_catalog,
        workflow_database_file=tmp_path / "workflow.sqlite3",
    )


def test_architecture_review_classifies_legacy_wrapper_steps(
    tmp_path: Path,
) -> None:
    service = ProcessDesignerApplicationService(_settings(tmp_path))
    review = service.architecture_review("FORECAST")

    ownership = {
        finding.step_type: finding.ownership
        for finding in review.findings
    }
    assert review.requires_review is True
    assert review.pipeline_accepts_year is True
    assert review.pipeline_stages == ("Load Data",)
    assert ownership[PlanningProcessStepType.PREFLIGHT] is (
        ProcessStepOwnership.PLATFORM_GATEWAY
    )
    assert ownership[PlanningProcessStepType.UPDATE_VARIABLES] is (
        ProcessStepOwnership.ORACLE_PIPELINE
    )
    assert ownership[PlanningProcessStepType.RUN_DATA_MAP] is (
        ProcessStepOwnership.REVIEW_REQUIRED
    )
    assert ownership[PlanningProcessStepType.VALIDATE_DATA] is (
        ProcessStepOwnership.PLATFORM_EXTENSION
    )

    draft = service.prepare_migration_draft("FORECAST")
    reused = service.prepare_migration_draft("FORECAST")
    migrated_review = service.architecture_review("FORECAST")

    assert draft.version == 1
    assert reused.version == draft.version
    assert [step.step_type for step in draft.process.steps] == [
        PlanningProcessStepType.PREFLIGHT,
        PlanningProcessStepType.RUN_PIPELINE,
    ]
    assert draft.process.steps[0].parameters == {"pipelineOnly": True}
    assert draft.process.steps[1].parameters["pipelineStages"][0][
        "displayName"
    ] == "Load Data"
    assert draft.cycle.data_map_name is None
    assert draft.cycle.variable_bindings == ()
    assert len(service.list_versions("FORECAST")) == 1
    assert migrated_review.aligned is True
    assert migrated_review.source_version == 1
