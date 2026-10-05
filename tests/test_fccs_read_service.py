"""Contract tests for the FCCS read-only foundation."""

from __future__ import annotations

import json

import pytest

from app.products.fccs.read_service import FCCSReadService
from app.utils.exceptions import ConfigurationError


class _FCCSClient:
    application_name = "Consolidation"
    planning_api_root = "HyperionPlanning/rest/v3"
    is_cloud_environment = True

    def __init__(self, *, application_type: str = "FCCS") -> None:
        self.application_type = application_type
        self.calls: list[tuple[str, dict | None]] = []

    def authenticate(self):
        return {"version": "v3", "lifecycle": "active"}

    def get(self, endpoint: str, *, params=None):
        self.calls.append((endpoint, params))
        root = "HyperionPlanning/rest/v3/applications"
        if endpoint == f"{root}/Consolidation":
            return {
                "name": "Consolidation",
                "type": "HP",
                "appType": self.application_type,
            }
        if endpoint == root:
            return {
                "items": [
                    {
                        "name": "Consolidation",
                        "type": "HP",
                        "appType": "FCCS",
                    },
                    {
                        "name": "Plan",
                        "type": "HP",
                        "appType": "PBCS",
                    },
                ]
            }
        if endpoint == f"{root}/Consolidation/plantypes":
            return {
                "items": [
                    {
                        "planTypeName": "Consol",
                        "cubeName": "Consol",
                        "numDimensions": 2,
                    }
                ]
            }
        if endpoint == f"{root}/Consolidation/plantypes/Consol/dimensions":
            return {
                "items": [
                    {"name": "Account", "dimType": "Account"},
                    {"name": "Entity", "dimType": "Entity"},
                ]
            }
        if endpoint == f"{root}/Consolidation/jobdefinitions":
            return {
                "items": [
                    {"jobName": "Consolidate", "jobType": "RULES"}
                ]
            }
        if endpoint == f"{root}/Consolidation/jobs/42":
            return {
                "jobId": 42,
                "jobName": "Consolidate",
                "jobType": "RULES",
                "status": 0,
                "descriptiveStatus": "Completed",
            }
        if endpoint == f"{root}/Consolidation/journals":
            return {
                "items": [
                    {
                        "label": "J1",
                        "scenario": "Actual",
                        "year": "FY27",
                        "period": "Jan",
                        "status": "Posted",
                    }
                ]
            }
        if endpoint == f"{root}/Consolidation/journals/Close%20Adjustment":
            return {
                "label": "Close Adjustment",
                "scenario": "Actual",
                "year": "FY27",
                "period": "Jan",
                "status": "Working",
                "journalLineItems": [
                    {"amountType": "Debit", "amount": 100},
                    {"amountType": "Credit", "amount": 100},
                ],
            }
        raise AssertionError(f"Unexpected endpoint: {endpoint}")


def test_fccs_connection_and_application_discovery_are_verified() -> None:
    service = FCCSReadService(_FCCSClient())

    connection = service.verify_connection()
    applications = service.discover_applications()

    assert connection.application.name == "Consolidation"
    assert connection.application.application_type == "FCCS"
    assert connection.api_version["version"] == "v3"
    assert [item.name for item in applications] == ["Consolidation"]


def test_fccs_connection_rejects_a_non_fccs_application() -> None:
    service = FCCSReadService(_FCCSClient(application_type="PBCS"))

    with pytest.raises(ConfigurationError, match="not verified"):
        service.verify_connection()


def test_fccs_dimensions_and_jobs_reuse_supported_common_rest_contracts() -> None:
    service = FCCSReadService(_FCCSClient())

    plan_types = service.get_plan_types_with_dimensions()
    jobs = service.get_job_definitions()
    job = service.get_job(42)

    assert [item.name for item in plan_types] == ["Consol"]
    assert [item.name for item in plan_types[0].dimensions] == [
        "Account",
        "Entity",
    ]
    assert [(item.job_name, item.job_type) for item in jobs] == [
        ("Consolidate", "RULES")
    ]
    assert job.job_id == 42
    assert job.is_successful is True


def test_fccs_journal_reads_are_filtered_bounded_and_non_mutating() -> None:
    client = _FCCSClient()
    service = FCCSReadService(client)

    journals = service.get_journals(
        filters={
            "scenario": "Actual",
            "year": "FY27",
            "period": "Jan",
            "status": "Posted",
        },
        offset=5,
        limit=25,
    )
    detail = service.get_journal_detail(
        "Close Adjustment",
        scenario="Actual",
        year="FY27",
        period="Jan",
    )

    assert [(item.label, item.status) for item in journals] == [
        ("J1", "Posted")
    ]
    list_params = client.calls[-2][1]
    assert list_params is not None
    assert list_params["offset"] == 5
    assert list_params["limit"] == 25
    assert json.loads(str(list_params["q"])) == {
        "scenario": "Actual",
        "year": "FY27",
        "period": "Jan",
        "status": "Posted",
    }
    assert detail.journal.label == "Close Adjustment"
    assert len(detail.line_items) == 2
    assert all(call[0] for call in client.calls)


@pytest.mark.parametrize(
    ("filters", "offset", "limit", "message"),
    (
        ({"unsupported": "value"}, 0, 25, "Unsupported journal filters"),
        ({}, -1, 25, "offset cannot be negative"),
        ({}, 0, 201, "limit must be between"),
    ),
)
def test_fccs_journal_reads_reject_unsafe_queries(
    filters: dict[str, str],
    offset: int,
    limit: int,
    message: str,
) -> None:
    service = FCCSReadService(_FCCSClient())

    with pytest.raises(ValueError, match=message):
        service.get_journals(
            filters=filters,
            offset=offset,
            limit=limit,
        )
