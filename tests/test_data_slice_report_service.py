"""Tests for Planning data-slice report exports."""

from __future__ import annotations

from unittest.mock import Mock

from app.clients.epm_client import EPMClient
from app.models.report import DataSliceReportDefinition, ReportAxisSegment
from app.services.data_slice_report_service import DataSliceReportService


def _definition() -> DataSliceReportDefinition:
    return DataSliceReportDefinition(
        name="Revenue Report",
        title="Revenue",
        cube="VisASO",
        pov=(("Scenario", "Actual"), ("Year", "FY22")),
        columns=(
            ReportAxisSegment(
                dimensions=("Period", "Product"),
                members=(
                    ("Jan", "Feb"),
                    ("Phone", "Laptop"),
                ),
            ),
        ),
        rows=(
            ReportAxisSegment(
                dimensions=("Account",),
                members=(("Revenue", "Gross Profit"),),
            ),
        ),
    )


def test_data_slice_export_transposes_column_axes() -> None:
    client = Mock(spec=EPMClient)
    client.application_name = "Vision"
    client.planning_api_root = "HyperionPlanning/rest/v3"
    client.post.return_value = {
        "pov": ["Actual", "FY22"],
        "columns": [
            ["Jan", "Jan", "Feb", "Feb"],
            ["Phone", "Laptop", "Phone", "Laptop"],
        ],
        "rows": [
            {
                "headers": ["Revenue"],
                "data": ["100", "200", "110", "210"],
            }
        ],
    }

    grid = DataSliceReportService(client).export(
        _definition(),
        pov=(("Scenario", "Forecast"), ("Year", "FY23")),
    )

    assert grid.columns == (
        ("Jan", "Phone"),
        ("Jan", "Laptop"),
        ("Feb", "Phone"),
        ("Feb", "Laptop"),
    )
    assert grid.pov == (("Scenario", "Forecast"), ("Year", "FY23"))
    assert grid.rows[0].data == ("100", "200", "110", "210")
    endpoint = client.post.call_args.args[0]
    assert endpoint.endswith("/plantypes/VisASO/exportdataslice")
    payload = client.post.call_args.kwargs["payload"]
    assert payload["gridDefinition"]["pov"]["members"] == [
        ["Forecast"],
        ["FY23"],
    ]
