"""Tests for live Oracle repository file discovery and filtering."""

from __future__ import annotations

from unittest.mock import Mock, call

import pytest

from app.application.oracle_files import (
    OracleFileCatalogApplicationService,
    OracleFilePurpose,
)
from app.services.file_catalog_service import FileCatalogService
from app.utils.exceptions import APIRequestError, FileCatalogError


def test_list_files_normalizes_external_v2_items() -> None:
    client = Mock()
    client.get.return_value = {
        "status": 0,
        "items": [
            {
                "name": "inbox/Forecast.csv",
                "type": "EXTERNAL",
                "size": "2048",
                "lastmodifiedtime": "1786420800000",
            },
            {
                "name": "Artifact Snapshot",
                "type": "LCM",
                "size": "100",
            },
            "invalid entry",
        ],
    }

    files = FileCatalogService(client).list_files()

    assert len(files) == 1
    assert files[0].name == "inbox/Forecast.csv"
    assert files[0].folder == "inbox"
    assert files[0].size_bytes == 2048
    assert files[0].last_modified_epoch_ms == 1786420800000
    client.get.assert_called_once_with("interop/rest/v2/files/list")


def test_list_files_falls_back_when_v2_is_not_supported() -> None:
    client = Mock()
    client.get.side_effect = [
        APIRequestError("Not Found", status_code=404),
        {
            "status": 0,
            "items": [
                {"name": "Legacy.csv", "type": "EXTERNAL", "size": 12}
            ],
        },
    ]

    files = FileCatalogService(client).list_files()

    assert [item.name for item in files] == ["Legacy.csv"]
    assert client.get.call_args_list == [
        call("interop/rest/v2/files/list"),
        call("interop/rest/11.1.2.3.600/applicationsnapshots"),
    ]


def test_catalog_filters_by_purpose_and_orders_newest_first() -> None:
    client = Mock()
    client.get.return_value = {
        "status": 0,
        "items": [
            {
                "name": "Old.csv",
                "type": "EXTERNAL",
                "lastmodifiedtime": 100,
            },
            {
                "name": "inbox/Newest.zip",
                "type": "EXTERNAL",
                "lastmodifiedtime": 300,
            },
            {
                "name": "Notes.pdf",
                "type": "EXTERNAL",
                "lastmodifiedtime": 400,
            },
            {
                "name": "outbox/Export.csv",
                "type": "EXTERNAL",
                "lastmodifiedtime": 500,
            },
            {
                "name": "inbox/folder/Current.txt",
                "type": "EXTERNAL",
                "lastmodifiedtime": 200,
            },
        ],
    }

    catalog = OracleFileCatalogApplicationService(client=client).discover(
        OracleFilePurpose.DATA_IMPORT
    )

    assert catalog.purpose is OracleFilePurpose.DATA_IMPORT
    assert [item.name for item in catalog.files] == [
        "inbox/Newest.zip",
        "inbox/folder/Current.txt",
        "Old.csv",
    ]

    metadata_catalog = OracleFileCatalogApplicationService(
        client=client
    ).discover(OracleFilePurpose.METADATA_IMPORT)
    assert [item.name for item in metadata_catalog.files] == [
        "inbox/Newest.zip",
        "Old.csv",
    ]

    integration_catalog = OracleFileCatalogApplicationService(
        client=client
    ).discover(OracleFilePurpose.DATA_INTEGRATION)
    assert [item.name for item in integration_catalog.files] == [
        "inbox/Newest.zip",
        "inbox/folder/Current.txt",
        "#epminbox/Old.csv",
    ]


def test_list_files_rejects_unsuccessful_catalog_response() -> None:
    client = Mock()
    client.get.return_value = {"status": 1, "details": "Access denied"}

    with pytest.raises(FileCatalogError, match="Access denied"):
        FileCatalogService(client).list_files()
