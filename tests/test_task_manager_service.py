"""Task Manager report synchronization contracts."""

from __future__ import annotations

from unittest.mock import Mock, patch

import pytest

from app.config.settings import Settings
from app.services.task_manager_service import (
    OracleTaskManagerReportGateway,
    TaskManagerReportParser,
)
from app.utils.exceptions import APIRequestError, ConfigurationError


def test_parser_finds_header_after_report_preamble_and_normalizes_tasks() -> None:
    content = (
        "Task Manager task report\n"
        "Generated,2026-10-05\n"
        "Schedule Name,Task ID,Task Name,Status,Assignee,Approver,Period,"
        "Due Date,Parent Task\n"
        "FY27 Close,101,Load Actuals,In Progress,planner@example.com,"
        "controller@example.com,Sep,10/05/2026 05:30 PM,Data Collection\n"
        "FY27 Close,102,Calculate Results,Not Started,planner@example.com,"
        "controller@example.com,Sep,10/06/2026,Calculation\n"
    ).encode("utf-8")

    tasks = TaskManagerReportParser.parse(content)

    assert len(tasks) == 2
    assert tasks[0].schedule_name == "FY27 Close"
    assert tasks[0].external_id == "101"
    assert tasks[0].name == "Load Actuals"
    assert tasks[0].assignee == "planner@example.com"
    assert tasks[0].due_at is not None
    assert tasks[0].attributes["Parent Task"] == "Data Collection"


def test_parser_requires_task_identity_column() -> None:
    with pytest.raises(ConfigurationError, match="Task Name or Task ID"):
        TaskManagerReportParser.parse(b"Schedule Name,Status\nClose,Open\n")


def test_gateway_downloads_report_content_in_memory_from_same_oracle_host() -> None:
    settings = Settings(
        epm_base_url="https://example.oraclecloud.com",
        epm_username="service.user",
        epm_password="secret",
        application_name="Vision",
    )
    client = Mock()
    client.fcm_report_endpoint = "HyperionPlanning/rest/fcmapi/v1/report"
    client.post.return_value = {
        "links": [
            {
                "rel": "report-content",
                "href": (
                    "https://example.oraclecloud.com/HyperionPlanning/rest/"
                    "fcmapi/v1/epm-ai-task-manager.csv"
                ),
            }
        ]
    }
    client.get_binary.return_value = b"Task Name,Status\nLoad Actuals,Open\n"
    context = Mock()
    context.__enter__ = Mock(return_value=client)
    context.__exit__ = Mock(return_value=False)

    with patch(
        "app.services.task_manager_service.EPMClient",
        return_value=context,
    ):
        content = OracleTaskManagerReportGateway(settings).generate_csv(
            report_group="Task Manager Reports",
            report_name="All Tasks",
            parameters={"Schedule Name": "FY27 Close"},
        )

    assert content.startswith(b"Task Name")
    client.post.assert_called_once()
    payload = client.post.call_args.kwargs["payload"]
    assert payload["module"] == "FCM"
    assert payload["format"] == "CSV"
    assert payload["runAsync"] is False
    client.get_binary.assert_called_once_with(
        "HyperionPlanning/rest/fcmapi/v1/epm-ai-task-manager.csv"
    )


def test_gateway_rebases_supported_report_content_link_from_another_host() -> None:
    settings = Settings(
        epm_base_url="https://example.oraclecloud.com",
        epm_username="service.user",
        epm_password="secret",
        application_name="Vision",
    )

    endpoint = OracleTaskManagerReportGateway(settings)._relative_oracle_endpoint(
        "https://internal-oracle-host/HyperionPlanning/rest/fcmapi/v1/report.csv"
    )

    assert endpoint == "HyperionPlanning/rest/fcmapi/v1/report.csv"


def test_gateway_rejects_an_unsupported_report_content_path() -> None:
    settings = Settings(
        epm_base_url="https://example.oraclecloud.com",
        epm_username="service.user",
        epm_password="secret",
        application_name="Vision",
    )

    with pytest.raises(APIRequestError, match="unsupported"):
        OracleTaskManagerReportGateway(settings)._relative_oracle_endpoint(
            "https://untrusted.example.com/report.csv"
        )
