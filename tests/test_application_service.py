"""Tests for Oracle Planning application metadata discovery."""

from __future__ import annotations

from unittest.mock import Mock

import pytest

from app.clients.epm_client import EPMClient
from app.services.application_service import ApplicationService
from app.utils.exceptions import APIRequestError


def _client() -> Mock:
    client = Mock(spec=EPMClient)
    client.application_name = "Vision"
    client.planning_api_root = "HyperionPlanning/rest/v3"
    return client


def test_get_configured_application_returns_cloud_metadata() -> None:
    client = _client()
    client.get.return_value = {
        "name": "Vision",
        "type": "HP",
        "appType": "PBCS",
        "appStorage": "Multidim",
        "adminMode": "false",
        "hybrid": "true",
        "unicode": True,
        "theme": "REDWOOD_LIGHT_R13",
    }

    result = ApplicationService(client).get_configured_application()

    assert result.name == "Vision"
    assert result.application_type == "PBCS"
    assert result.storage == "Multidim"
    assert result.hybrid is True
    assert result.admin_mode is False
    client.get.assert_called_once_with(
        "HyperionPlanning/rest/v3/applications/Vision"
    )


def test_get_plan_types_can_include_live_dimensions() -> None:
    client = _client()
    client.get.side_effect = (
        {
            "items": [
                {
                    "planTypeName": "Plan1",
                    "cubeName": "Plan1",
                    "planType": 1,
                    "cubeType": 0,
                    "numDimensions": 2,
                }
            ]
        },
        {
            "items": [
                {"name": "Account", "dimType": "Account"},
                {"name": "Period", "dimType": "Period"},
            ]
        },
    )

    result = ApplicationService(client).get_plan_types(
        include_dimensions=True
    )

    assert result[0].cube_name == "Plan1"
    assert [item.name for item in result[0].dimensions] == [
        "Account",
        "Period",
    ]
    assert client.get.call_args_list[1].kwargs["params"] == {
        "limit": -1,
        "fields": "name,dimType",
    }


def test_get_plan_types_uses_live_legacy_application_sources() -> None:
    client = _client()
    client.get.side_effect = (
        APIRequestError("Not Found", status_code=404),
        {
            "items": [
                {"name": "CurYr", "planType": "ALL"},
                {"name": "CurYr", "planType": "Plan1"},
                {"name": "CurYr", "planType": "OEP_REP"},
            ]
        },
        {
            "items": [
                {
                    "jobName": "Refresh",
                    "jobType": "CUBE_REFRESH",
                    "planTypeName": "OEP_FS",
                },
                {
                    "jobName": "Rule",
                    "jobType": "RULES",
                    "planTypeName": "Plan1",
                },
                {
                    "jobName": "Application export",
                    "jobType": "EXPORT_METADATA",
                    "planTypeName": "Vision",
                },
            ]
        },
    )

    result = ApplicationService(client).get_plan_types()

    assert [item.name for item in result] == [
        "OEP_FS",
        "OEP_REP",
        "Plan1",
    ]
    assert all(item.dimension_count is None for item in result)
    assert client.get.call_args_list[1].kwargs["params"] == {"limit": -1}


def test_on_prem_plan_types_support_legacy_status_and_response_shapes() -> None:
    client = _client()
    client.is_cloud_environment = False
    client.get.side_effect = (
        APIRequestError("Method Not Allowed", status_code=405),
        APIRequestError("Unsupported query parameter", status_code=400),
        {
            "substitutionVariables": [
                {"name": "CurYr", "planType": "Plan2"},
            ]
        },
        {
            "jobDefinitions": [
                {"jobName": "Rule", "planTypeName": "Plan1"},
                {"jobName": "Legacy", "planType": 1},
            ]
        },
    )

    result = ApplicationService(client).get_plan_types()

    assert [item.name for item in result] == ["Plan1", "Plan2"]
    assert client.get.call_args_list[1].kwargs["params"] == {"limit": -1}
    assert client.get.call_args_list[2].kwargs == {}


def test_cloud_plan_type_permission_or_method_errors_are_not_hidden() -> None:
    client = _client()
    client.is_cloud_environment = True
    client.get.side_effect = APIRequestError(
        "Method Not Allowed",
        status_code=405,
    )

    with pytest.raises(APIRequestError, match="Method Not Allowed"):
        ApplicationService(client).get_plan_types()

    client.get.assert_called_once()


def test_get_dimension_members_flattens_oracle_hierarchy() -> None:
    client = _client()
    client.get.return_value = {
        "name": "Product",
        "children": [
            {
                "name": "Total Products",
                "path": "/Product/Total Products",
                "children": [
                    {
                        "name": "iPhone 15",
                        "alias": "iPhone Fifteen",
                        "path": "/Product/Total Products/iPhone 15",
                    }
                ],
            }
        ],
    }

    result = ApplicationService(client).get_dimension_members(
        "Plan1",
        "Product",
    )

    assert [item.name for item in result] == [
        "Total Products",
        "iPhone 15",
    ]
    assert result[0].has_children is True
    assert result[1].alias == "iPhone Fifteen"
    assert result[1].parent_name == "Total Products"
    client.get.assert_called_once_with(
        "HyperionPlanning/rest/v3/applications/Vision/plantypes/Plan1/"
        "dimensions/Product",
        params={
            "fields": "name,alias,path,parentName,children",
            "aliasTableName": "Default",
        },
    )
