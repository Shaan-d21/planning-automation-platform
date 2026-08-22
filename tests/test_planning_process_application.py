"""Tests for structured Planning process web preflight."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import Mock, patch

import pytest

from app.application.planning_process import (
    PlanningProcessApplicationService,
    PlanningProcessInput,
)
from app.config.settings import Settings
from app.models.job import JobDefinition
from app.models.pipeline import PipelineDetails, PipelineVariable
from app.models.substitution_variable import (
    SubstitutionVariable,
    SubstitutionVariableUpdate,
)
from app.utils.exceptions import PlanningProcessError


def _settings(tmp_path: Path) -> Settings:
    process_catalog = tmp_path / "processes.json"
    process_catalog.write_text(
        json.dumps(
            {
                "processes": [
                    {
                        "code": "FORECAST",
                        "displayName": "Monthly Forecast",
                        "cycleCode": "MONTHLY",
                        "steps": [
                            {"type": "PREFLIGHT", "name": "Validate"},
                            {
                                "type": "UPDATE_VARIABLES",
                                "name": "Update variables",
                            },
                            {
                                "type": "RUN_PIPELINE",
                                "name": "Run Pipeline",
                            },
                            {
                                "type": "RUN_DATA_MAP",
                                "name": "Publish data",
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
                        "code": "MONTHLY",
                        "displayName": "Monthly Forecast",
                        "pipelineCode": "PIPE01",
                        "dataMapName": "Revenue Map",
                        "variableBindings": [
                            {
                                "role": "YEAR",
                                "variableName": "CurYr",
                                "scope": "ALL",
                            }
                        ],
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
        workflow_database_file=tmp_path / "history.sqlite3",
        report_output_dir=tmp_path / "reports",
    )


def _pipeline() -> PipelineDetails:
    return PipelineDetails(
        code="PIPE01",
        display_name="Forecast Pipeline",
        parallel_jobs=None,
        variables=(
            PipelineVariable(
                name="STARTPERIOD",
                display_name="Start Period",
                default_value=None,
                variable_type="TEXT",
                value_object=None,
                sequence=1,
                is_default_parameter=False,
                is_required=True,
            ),
            PipelineVariable(
                name="ENDPERIOD",
                display_name="End Period",
                default_value=None,
                variable_type="TEXT",
                value_object=None,
                sequence=2,
                is_default_parameter=False,
                is_required=True,
            ),
            PipelineVariable(
                name="INPUT_FILE",
                display_name="Forecast File",
                default_value="forecast.csv",
                variable_type="FILE",
                value_object=None,
                sequence=3,
                is_default_parameter=False,
                is_required=True,
            ),
        ),
        stages=(),
    )


@patch("app.application.planning_process.JobService")
@patch("app.application.planning_process.PipelineService")
@patch("app.application.planning_process.SubstitutionVariableService")
@patch("app.application.planning_process.EPMClient")
def test_preflight_returns_structured_changes_runtime_and_files(
    client_class: Mock,
    variable_service_class: Mock,
    pipeline_service_class: Mock,
    job_service_class: Mock,
    tmp_path: Path,
) -> None:
    client_class.return_value.__enter__.return_value = Mock()
    variables = (
        SubstitutionVariable(name="CurYr", value="FY25", scope="ALL"),
    )
    variable_service = variable_service_class.return_value
    variable_service.get_all_variables.return_value = variables
    variable_service.build_updates.return_value = (
        SubstitutionVariableUpdate(
            name="CurYr",
            scope="ALL",
            old_value="FY25",
            new_value="FY26",
        ),
    )
    pipeline_service_class.return_value.get_pipeline_details.return_value = (
        _pipeline()
    )
    job_service_class.return_value.get_job_definitions.return_value = (
        JobDefinition(job_name="Revenue Map", job_type="PLAN_TYPE_MAP"),
    )
    service = PlanningProcessApplicationService(_settings(tmp_path))

    result = service.preflight(
        PlanningProcessInput(
            process_code="FORECAST",
            year="FY26",
            start_period="Jan",
            end_period="Mar",
            run_report=False,
        )
    )

    assert result.process_name == "Monthly Forecast"
    assert result.pipeline_code == "PIPE01"
    assert result.variable_changes[0].changed is True
    assert {
        item.name: item.value for item in result.runtime_variables
    } == {
        "STARTPERIOD": "Jan-26",
        "ENDPERIOD": "Mar-26",
    }
    assert result.file_requirements[0].key == "INPUT_FILE"
    assert result.file_requirements[0].configured_reference == "forecast.csv"


def test_pipeline_only_cycle_still_requires_planning_year() -> None:
    with pytest.raises(PlanningProcessError, match="year"):
        PlanningProcessApplicationService._cycle(
            PlanningProcessInput(
                process_code="FORECAST",
                year="",
                start_period="",
                end_period="",
            ),
            require_cycle_values=False,
        )
