"""Tests for Planning cycle configuration and pre-flight translation."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import Mock

from app.clients.epm_client import EPMClient
from app.models.pipeline import PipelineDetails, PipelineVariable
from app.models.planning_cycle import (
    CycleValueRole,
    CycleVariableBinding,
    PlanningCycle,
)
from app.models.substitution_variable import SubstitutionVariable
from app.services.planning_cycle_catalog_service import (
    PlanningCycleCatalogService,
)
from app.services.planning_cycle_preflight_service import (
    PlanningCyclePreflightService,
)
from app.services.substitution_variable_service import (
    SubstitutionVariableService,
)


def _catalog(path: Path) -> None:
    path.write_text(
        json.dumps(
            {
                "version": 1,
                "cycles": [
                    {
                        "code": "MONTHLY_FORECAST",
                        "displayName": "Monthly Forecast",
                        "pipelineCode": "PIPE01",
                        "dataMapName": "Revenue Map",
                        "variableBindings": [],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )


def test_catalog_persists_scoped_variable_bindings(tmp_path: Path) -> None:
    path = tmp_path / "cycles.json"
    _catalog(path)
    service = PlanningCycleCatalogService()

    updated = service.save_bindings(
        path,
        "MONTHLY_FORECAST",
        (
            CycleVariableBinding(
                CycleValueRole.YEAR,
                "CurYr",
                "ALL",
            ),
        ),
    )

    assert updated.variable_bindings[0].variable_name == "CurYr"
    loaded = service.get(path, "monthly_forecast")
    assert loaded.variable_bindings == updated.variable_bindings


def test_preflight_builds_updates_and_live_pipeline_variables(
    tmp_path: Path,
) -> None:
    path = tmp_path / "cycles.json"
    _catalog(path)
    catalog = PlanningCycleCatalogService()
    definition = catalog.save_bindings(
        path,
        "MONTHLY_FORECAST",
        (
            CycleVariableBinding(
                CycleValueRole.YEAR,
                "CurYr",
                "ALL",
            ),
            CycleVariableBinding(
                CycleValueRole.START_PERIOD,
                "CurMonth",
                "ALL",
            ),
        ),
    )
    details = PipelineDetails(
        code="PIPE01",
        display_name="Forecast",
        parallel_jobs=None,
        variables=(
            PipelineVariable(
                name="STARTPERIOD",
                display_name="Start",
                default_value=None,
                variable_type="TEXT",
                value_object=None,
                sequence=1,
                is_default_parameter=False,
                is_required=True,
            ),
            PipelineVariable(
                name="ENDPERIOD",
                display_name="End",
                default_value=None,
                variable_type="TEXT",
                value_object=None,
                sequence=2,
                is_default_parameter=False,
                is_required=True,
            ),
        ),
        stages=(),
    )
    variables = (
        SubstitutionVariable("CurYr", "FY25", "ALL"),
        SubstitutionVariable("CurMonth", "Dec", "ALL"),
    )
    client = Mock(spec=EPMClient)
    client.application_name = "Vision"
    client.planning_api_root = "HyperionPlanning/rest/v3"
    variable_service = SubstitutionVariableService(client)

    result = PlanningCyclePreflightService().validate(
        definition,
        PlanningCycle("FY26", "Jan-26", "Mar-26"),
        variables=variables,
        pipeline_details=details,
        available_data_maps=("Revenue Map",),
        variable_service=variable_service,
    )

    assert [(item.name, item.new_value) for item in result.updates] == [
        ("CurMonth", "Jan"),
        ("CurYr", "FY26"),
    ]
    assert dict(result.pipeline_variables) == {
        "STARTPERIOD": "Jan-26",
        "ENDPERIOD": "Mar-26",
    }


def test_cycle_separates_member_period_from_pipeline_period() -> None:
    cycle = PlanningCycle("FY26", "Jan", "Mar")

    assert cycle.value_for(CycleValueRole.START_PERIOD) == "Jan"
    assert cycle.pipeline_start_period == "Jan-26"
    assert cycle.pipeline_end_period == "Mar-26"
