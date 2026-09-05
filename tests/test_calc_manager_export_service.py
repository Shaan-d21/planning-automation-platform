"""Tests for fully automated Calculation Manager snapshot exports."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, Mock, patch

import pytest

from app.config.settings import Settings
from app.services.calc_manager_export_service import CalcManagerExportService
from app.utils.exceptions import APIRequestError, BusinessRuleError


def _settings(tmp_path: Path) -> Settings:
    return Settings(
        epm_base_url="https://example.oraclecloud.com",
        epm_username="administrator",
        epm_password="secret",
        application_name="Vision",
        workflow_database_file=tmp_path / "platform.sqlite3",
        default_poll_interval=0.01,
        default_job_timeout=10,
    )


def _client_context(client: Mock) -> MagicMock:
    context = MagicMock()
    context.__enter__.return_value = client
    context.__exit__.return_value = None
    return context


def test_capability_requires_calculation_manager_category(tmp_path: Path) -> None:
    client = Mock()
    client.get.return_value = {
        "status": 0,
        "items": [
            {"categoryName": "Core"},
            {"categoryName": "Calculation Manager"},
        ],
    }

    with patch(
        "app.services.calc_manager_export_service.EPMClient",
        return_value=_client_context(client),
    ):
        CalcManagerExportService(_settings(tmp_path)).ensure_supported()

    client.authenticate.assert_called_once()
    client.get.assert_called_once_with(
        "interop/rest/v2/migration/categories/list"
    )


def test_capability_rejects_environment_without_export_v2(tmp_path: Path) -> None:
    client = Mock()
    client.get.side_effect = APIRequestError("Not found", status_code=404)

    with patch(
        "app.services.calc_manager_export_service.EPMClient",
        return_value=_client_context(client),
    ), pytest.raises(BusinessRuleError, match="does not expose"):
        CalcManagerExportService(_settings(tmp_path)).ensure_supported()


def test_generate_exports_waits_and_downloads_fresh_snapshot(
    tmp_path: Path,
) -> None:
    client = Mock()
    client.post.return_value = {
        "status": -1,
        "links": [
            {
                "rel": "Job Status",
                "href": (
                    "https://example.oraclecloud.com/interop/rest/v2/"
                    "status/migration/42"
                ),
            }
        ],
    }
    client.get.return_value = {"status": 0, "details": None}
    files = Mock()
    files.download_from_repository.return_value = b"PK\x03\x04snapshot"
    service = CalcManagerExportService(_settings(tmp_path))

    with patch(
        "app.services.calc_manager_export_service.EPMClient",
        return_value=_client_context(client),
    ), patch(
        "app.services.calc_manager_export_service.FileService",
        return_value=files,
    ), patch(
        "app.services.calc_manager_export_service.time.sleep"
    ), patch.object(
        service,
        "_unique_snapshot_name",
        return_value="BISP_RTP_20270101",
    ):
        result = service.generate("BISP RTP")

    assert result.snapshot_name == "BISP_RTP_20270101"
    assert result.content == b"PK\x03\x04snapshot"
    client.post.assert_called_once_with(
        "interop/rest/v2/migration/categories/artifacts/export",
        payload={
            "snapshotName": "BISP_RTP_20270101",
            "categories": [
                {
                    "categoryName": "Calculation Manager",
                    "selectedArtifacts": ["//"],
                }
            ],
        },
    )
    client.get.assert_called_once_with(
        "interop/rest/v2/status/migration/42"
    )
    files.download_from_repository.assert_called_once_with(
        "BISP_RTP_20270101"
    )


def test_generate_polls_canonical_oracle_host_through_configured_client(
    tmp_path: Path,
) -> None:
    client = Mock()
    client.post.return_value = {
        "status": -1,
        "links": [
            {
                "rel": "Job Status",
                "href": (
                    "https://canonical.oraclecloud.com/interop/rest/v2/"
                    "status/migration/42?source=export"
                ),
            }
        ],
    }
    client.get.return_value = {"status": 0, "details": None}
    files = Mock()
    files.download_from_repository.return_value = b"PK\x03\x04snapshot"
    service = CalcManagerExportService(_settings(tmp_path))

    with patch(
        "app.services.calc_manager_export_service.EPMClient",
        return_value=_client_context(client),
    ), patch(
        "app.services.calc_manager_export_service.FileService",
        return_value=files,
    ), patch(
        "app.services.calc_manager_export_service.time.sleep"
    ):
        service.generate("BISP_RTP")

    client.get.assert_called_once_with(
        "interop/rest/v2/status/migration/42?source=export"
    )


def test_generate_rejects_non_migration_status_path(tmp_path: Path) -> None:
    client = Mock()
    client.post.return_value = {
        "status": -1,
        "links": [
            {
                "rel": "Job Status",
                "href": "https://canonical.oraclecloud.com/not-migration/42",
            }
        ],
    }
    service = CalcManagerExportService(_settings(tmp_path))

    with patch(
        "app.services.calc_manager_export_service.EPMClient",
        return_value=_client_context(client),
    ), pytest.raises(BusinessRuleError, match="invalid migration status link"):
        service.generate("BISP_RTP")


def test_snapshot_prefix_is_bounded_and_safe() -> None:
    assert (
        CalcManagerExportService.normalize_snapshot_prefix("BISP RTP / Registry")
        == "BISP_RTP_Registry"
    )
    with pytest.raises(BusinessRuleError, match="cannot exceed"):
        CalcManagerExportService.normalize_snapshot_prefix("x" * 49)
