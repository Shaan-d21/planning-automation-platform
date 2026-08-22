"""Tests for form-based Data Map source-target validation."""

from __future__ import annotations

from unittest.mock import Mock

from app.clients.epm_client import EPMClient
from app.models.data_validation import (
    DataQualityRules,
    FormGrid,
    FormGridRow,
)
from app.services.data_validation_service import DataValidationService


def _grid(first_value: object, second_value: object) -> dict:
    return {
        "gridInfo": {
            "rowDimNames": ["Account"],
            "columnDimNames": ["Period"],
        },
        "pov": {"Scenario": "Forecast"},
        "rows": [
            {
                "headers": ["Vehicle_Revenue"],
                "data": [first_value, second_value],
            }
        ],
        "columns": [["Jan"], ["Feb"]],
    }


def _client(*responses: dict) -> Mock:
    client = Mock(spec=EPMClient)
    client.application_name = "Vision"
    client.planning_api_root = "HyperionPlanning/rest/v3"
    client.get.side_effect = responses
    return client


def test_matching_forms_pass_with_numeric_tolerance() -> None:
    client = _client(_grid("100.00", 200), _grid(100.004, "200"))

    result = DataValidationService(client).compare_forms(
        "VF_Source",
        "VF_Target",
        tolerance="0.01",
    )

    assert result.is_successful
    assert result.compared_cells == 2
    assert result.matched_cells == 2


def test_mismatch_reports_exact_intersection() -> None:
    client = _client(_grid(100, "#Missing"), _grid(95, 0))

    result = DataValidationService(client).compare_forms(
        "VF_Source",
        "VF_Target",
    )

    assert not result.is_successful
    assert len(result.mismatches) == 2
    assert result.mismatches[0].row_headers == ("Vehicle_Revenue",)
    assert result.mismatches[0].column_headers == ("Jan",)


def test_complete_comparison_cells_include_matches_and_differences() -> None:
    client = _client(_grid("100.00000001", 200), _grid(100, 200))

    result = DataValidationService(client).compare_forms(
        "VF_Source",
        "VF_Target",
        include_cells=True,
    )

    assert len(result.cells) == 2
    assert not result.cells[0].matches
    assert str(result.cells[0].difference) == "1E-8"
    assert result.cells[1].matches
    assert result.cells[1].difference == 0


def test_oracle_floating_point_noise_is_not_a_business_difference() -> None:
    client = _client(
        _grid("1070.0000000000002", "1950.0000000000002"),
        _grid(1070, 1950),
    )

    result = DataValidationService(client).compare_forms(
        "VF_Source",
        "VF_Target",
        tolerance="0",
        include_cells=True,
    )

    assert result.is_successful
    assert result.matched_cells == 2
    assert all(cell.matches for cell in result.cells)


def test_quality_checks_return_exact_business_exceptions() -> None:
    grid = FormGrid(
        row_dimensions=("Account",),
        column_dimensions=("Period",),
        columns=(("Jan",), ("Feb",), ("Mar",), ("Apr",)),
        rows=(
            FormGridRow(
                headers=("Revenue",),
                data=("#Missing", 0, 50, 250),
            ),
        ),
        pov=(("Scenario", "Forecast"), ("Year", "FY27")),
    )

    result = DataValidationService(_client()).validate_grid(
        grid,
        DataQualityRules(
            check_missing=True,
            check_zero=True,
            minimum=60,
            maximum=200,
        ),
    )

    assert result.status == "FAIL"
    assert result.checked_cells == 4
    assert result.issue_count == 4
    assert result.missing_count == 1
    assert result.below_minimum_count == 2
    assert result.above_maximum_count == 1
    assert result.zero_count == 0
    assert result.issues[0].pov == (
        ("Scenario", "Forecast"),
        ("Year", "FY27"),
    )
    assert result.issues[0].row_headers == ("Revenue",)
    assert result.issues[0].column_headers == ("Jan",)


def test_zero_only_quality_check_returns_warning() -> None:
    grid = FormGrid(
        row_dimensions=("Account",),
        column_dimensions=("Period",),
        columns=(("Jan",),),
        rows=(FormGridRow(headers=("Revenue",), data=(0,)),),
    )

    result = DataValidationService(_client()).validate_grid(
        grid,
        DataQualityRules(check_missing=False, check_zero=True),
    )

    assert result.status == "WARNING"
    assert result.zero_count == 1
    assert result.issues[0].severity == "WARNING"
