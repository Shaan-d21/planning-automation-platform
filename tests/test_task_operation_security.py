"""Access boundaries for operations launched from assigned Planning tasks."""

from app.models.access_control import Permission
from app.web.security import required_permissions


def test_task_operation_routes_accept_process_runner_or_operator() -> None:
    expected = {Permission.PROCESS_RUN, Permission.OPERATION_EXECUTE}

    assert set(
        required_permissions(
            "POST", "/api/operations/data-integrations/runs"
        )
    ) == expected
    assert set(
        required_permissions("GET", "/app/operations/cube-refresh")
    ) == expected
    assert Permission.PROCESS_RUN in required_permissions(
        "GET", "/api/operations/runs/execution-123"
    )
    assert Permission.PROCESS_RUN in required_permissions(
        "GET", "/api/operations/business-rules/catalog"
    )
    assert Permission.PROCESS_RUN in required_permissions(
        "GET", "/api/operations/data-integrations/catalog"
    )
    assert Permission.PROCESS_RUN in required_permissions(
        "GET", "/api/operations/pipelines/catalog"
    )
    assert Permission.PROCESS_RUN in required_permissions(
        "GET", "/api/operations/data-import/catalog"
    )
    assert Permission.PROCESS_RUN in required_permissions(
        "GET", "/api/operations/metadata-import/catalog"
    )


def test_standalone_administration_operations_remain_restricted() -> None:
    assert required_permissions("GET", "/api/v1/operations") == (
        Permission.OPERATION_EXECUTE,
    )
    assert required_permissions(
        "POST", "/api/operations/pipelines/register"
    ) == (Permission.CATALOG_MANAGE,)
    assert required_permissions(
        "POST", "/api/operations/data-integrations/register"
    ) == (Permission.CATALOG_MANAGE,)
    assert required_permissions(
        "POST", "/api/operations/oracle-catalog/sync"
    ) == (Permission.CATALOG_MANAGE,)
    assert required_permissions(
        "GET", "/api/operations/oracle-catalog"
    ) == (Permission.OPERATION_EXECUTE,)
    assert required_permissions(
        "POST", "/api/operations/substitution-variables/runs"
    ) == (Permission.VARIABLE_UPDATE,)
    assert required_permissions(
        "POST", "/api/operations/user-variables/runs"
    ) == (Permission.USER_VARIABLE_UPDATE,)


def test_data_explorer_separates_read_access_from_shared_view_changes() -> None:
    assert set(required_permissions("GET", "/api/data-explorer/views")) == {
        Permission.DATA_REVIEW,
        Permission.REPORT_GENERATE,
    }
    assert required_permissions("POST", "/api/data-explorer/views") == (
        Permission.REPORT_GENERATE,
    )
    assert required_permissions(
        "DELETE", "/api/data-explorer/views/monthly-forecast"
    ) == (Permission.REPORT_GENERATE,)
    assert required_permissions("POST", "/api/data-review/grid") == (
        Permission.DATA_REVIEW,
    )
