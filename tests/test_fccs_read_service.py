"""Contract tests for the FCCS read-only foundation."""

from __future__ import annotations

import json

import pytest

from app.agent.capabilities import AgentCapabilityGateway
from app.agent.models import AgentToolCall
from app.application.execution_payloads import (
    operation_from_payload,
    operation_payload,
)
from app.config.settings import Settings
from app.models.job import JobDefinition
from app.products.contracts import BusinessProcessType
from app.products.registry import PRODUCT_PROVIDER_REGISTRY
from app.products.fccs.read_service import FCCSReadService
from app.products.fccs.journal_actions import (
    FCCSJournalAction,
    FCCSJournalActionInput,
    FCCSJournalActionService,
)
from app.products.fccs.consolidation import (
    FCCSConsolidationInput,
    FCCSConsolidationService,
)
from app.utils.exceptions import ConfigurationError, OperationError


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

    def post(self, endpoint: str, *, payload=None, params=None):
        self.calls.append((endpoint, payload))
        if endpoint.endswith("/Consolidation/jobs"):
            assert payload == {
                "jobType": "RULES",
                "jobName": "Consolidate",
                "parameters": {
                    "Scenario": "Actual",
                    "Year": "FY27",
                    "Period": "Jan",
                    "Entity": "FCCS_Total Geography",
                },
            }
            return {
                "jobId": 55,
                "status": -1,
                "jobName": "Consolidate",
                "jobType": "RULES",
            }
        assert endpoint.endswith(
            "/Consolidation/journals/Close%20Adjustment/actions"
        )
        assert payload == {
            "scenario": "Actual",
            "year": "FY27",
            "period": "Jan",
            "action": "SUBMIT",
        }
        return {"actionStatus": "Submitted"}


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


def test_fccs_journal_action_rechecks_status_and_uses_documented_endpoint() -> None:
    client = _FCCSClient()
    result = FCCSJournalActionService(client).execute(
        FCCSJournalActionInput(
            journal_label="Close Adjustment",
            scenario="Actual",
            year="FY27",
            period="Jan",
            action=FCCSJournalAction.SUBMIT,
            expected_status="Working",
        )
    )

    assert result["action"] == "SUBMIT"
    assert result["previous_status"] == "Working"
    assert result["status"] == "Submitted"


def test_fccs_consolidation_requires_live_rule_and_submits_exact_pov() -> None:
    client = _FCCSClient()

    result = FCCSConsolidationService(client).start(
        FCCSConsolidationInput(
            rule_name="Consolidate",
            scenario="Actual",
            year="FY27",
            period="Jan",
            entity="FCCS_Total Geography",
        )
    )

    assert result.job_id == 55
    assert result.rule_name == "Consolidate"


def test_fccs_consolidation_is_safe_for_durable_queue_round_trip() -> None:
    operation = FCCSConsolidationInput(
        rule_name="Consolidate",
        scenario="Actual",
        year="FY27",
        period="Jan",
        entity="FCCS_Total Geography",
    )

    restored, actor = operation_from_payload(operation_payload(operation, None))

    assert restored == operation
    assert actor is None


def test_fccs_consolidation_rejects_non_consolidation_rules() -> None:
    with pytest.raises(OperationError, match="not identified as a consolidation"):
        FCCSConsolidationService.normalize(
            FCCSConsolidationInput(
                rule_name="Translate",
                scenario="Actual",
                year="FY27",
                period="Jan",
                entity="FCCS_Total Geography",
            )
        )


def test_fccs_journal_action_is_safe_for_durable_queue_round_trip() -> None:
    operation = FCCSJournalActionInput(
        journal_label="Close Adjustment",
        scenario="Actual",
        year="FY27",
        period="Jan",
        action=FCCSJournalAction.APPROVE,
        consolidation="FCCS_Entity Input",
        expected_status="Submitted",
    )

    restored, actor = operation_from_payload(operation_payload(operation, None))

    assert restored == operation
    assert actor is None


def test_fccs_agent_exposes_product_reads_without_planning_tools(tmp_path) -> None:
    class _Catalog:
        @staticmethod
        def active_definitions():
            return PRODUCT_PROVIDER_REGISTRY.operations_for(
                BusinessProcessType.FCCS
            )

        @staticmethod
        def active_agent_tool_names():
            return PRODUCT_PROVIDER_REGISTRY.agent_tool_names_for(
                BusinessProcessType.FCCS
            )

    class _Read:
        @staticmethod
        def job_definitions(*, job_type=None):
            assert job_type == "RULES"
            return (JobDefinition("Consolidate", "RULES"),)

    gateway = AgentCapabilityGateway(
        Settings(
            epm_base_url="https://example.oraclecloud.com",
            epm_username="service-user",
            epm_password="secret",
            application_name="Close",
            workflow_database_file=tmp_path / "fccs-agent.sqlite3",
        ),
        control_center=object(),
        data_review=object(),
        operation_catalog=_Catalog(),
        fccs_read=_Read(),
    )

    names = {item.name for item in gateway.tool_definitions()}
    consolidation_rules = gateway.artifact_choices("fccs-consolidation")
    result = gateway.execute(
        AgentToolCall("list_fccs_jobs", {"job_type": "RULES"})
    )

    assert "list_fccs_jobs" in names
    assert "list_planning_cubes" not in names
    assert consolidation_rules == ("Consolidate",)
    assert result == {
        "count": 1,
        "jobs": [{"job_name": "Consolidate", "job_type": "RULES"}],
    }
