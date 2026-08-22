"""Tests for reusable Planning form retrieval."""

from __future__ import annotations

from unittest.mock import Mock

from app.clients.epm_client import EPMClient
from app.services.form_service import PlanningFormService


def _client(response: dict) -> Mock:
    client = Mock(spec=EPMClient)
    client.application_name = "Vision"
    client.planning_api_root = "HyperionPlanning/rest/v3"
    client.get.return_value = response
    return client


def test_get_form_layout_discovers_axes_and_page_members() -> None:
    client = _client(
        {
            "gridInfo": {
                "pageDimNames": ["Scenario", "Version"],
                "rowDimNames": ["Account"],
                "columnDimNames": ["Period"],
                "allowedPageMembersByDim": {
                    "Scenario": ["Forecast", "Plan"],
                    "Version": ["Working", "Final"],
                },
            },
            "pov": {
                "Scenario": "Forecast",
                "Version": "Working",
            },
        }
    )

    layout = PlanningFormService(client).get_form_layout(
        "Revenue Report"
    )

    assert layout.page_dimensions == ("Scenario", "Version")
    assert layout.row_dimensions == ("Account",)
    assert layout.allowed_page_members[0] == (
        "Scenario",
        ("Forecast", "Plan"),
    )
    client.get.assert_called_once_with(
        "HyperionPlanning/rest/v3/applications/Vision/forms/"
        "Revenue%20Report/data",
        params={
            "displayMemberAs": "MEMBER_NAME",
            "fields": "gridInfo,pov",
        },
    )


def test_export_form_passes_page_and_filter_members() -> None:
    client = _client(
        {
            "gridInfo": {
                "rowDimNames": ["Account"],
                "columnDimNames": ["Period"],
            },
            "pov": {"Scenario": "Plan"},
            "rows": [{"headers": ["Revenue"], "data": [100]}],
            "columns": [["Jan"]],
        }
    )

    grid = PlanningFormService(client).export_form(
        "Revenue Report",
        page_members=("Plan", "Working"),
        filter_members=("Products",),
    )

    assert grid.rows[0].data == (100,)
    assert client.get.call_args.kwargs["params"]["pageMbrList"] == [
        "Plan",
        "Working",
    ]
    assert client.get.call_args.kwargs["params"]["filterMembers"] == [
        "Products"
    ]

