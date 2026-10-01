"""Tests for reusable Oracle EPM Inbox uploads."""

from __future__ import annotations

from unittest.mock import Mock

import pytest

from app.clients.epm_client import EPMClient
from app.services.file_service import FileService
from app.utils.exceptions import FileUploadError


def test_upload_to_inbox_streams_valid_csv(tmp_path) -> None:
    metadata_file = tmp_path / "Account Metadata.csv"
    metadata_file.write_bytes(b"Account,Parent\nA100,Total")
    client = Mock(spec=EPMClient)
    client.post_binary.return_value = {"status": 0, "details": None}

    result = FileService(client).upload_to_inbox(metadata_file)

    assert result.is_successful
    assert result.file_name == "Account Metadata.csv"
    endpoint = client.post_binary.call_args.args[0]
    assert endpoint == (
        "interop/rest/11.1.2.3.600/applicationsnapshots/"
        "Account%20Metadata.csv/contents"
    )


@pytest.mark.parametrize("extension", [".CSV", ".zip", ".ZIP"])
def test_upload_to_inbox_accepts_supported_extensions(
    tmp_path,
    extension: str,
) -> None:
    metadata_file = tmp_path / f"metadata{extension}"
    metadata_file.write_bytes(b"content")
    client = Mock(spec=EPMClient)
    client.post_binary.return_value = {"status": "0"}

    result = FileService(client).upload_to_inbox(metadata_file)

    assert result.status == 0


def test_upload_to_inbox_rejects_missing_file(tmp_path) -> None:
    client = Mock(spec=EPMClient)

    with pytest.raises(FileUploadError, match="does not exist"):
        FileService(client).upload_to_inbox(tmp_path / "missing.csv")

    client.post_binary.assert_not_called()


def test_upload_to_inbox_rejects_unsupported_extension(tmp_path) -> None:
    metadata_file = tmp_path / "metadata.txt"
    metadata_file.write_text("content", encoding="utf-8")
    client = Mock(spec=EPMClient)

    with pytest.raises(FileUploadError, match="Unsupported"):
        FileService(client).upload_to_inbox(metadata_file)


def test_upload_to_inbox_can_allow_administrator_defined_extension(
    tmp_path,
) -> None:
    source_file = tmp_path / "FlexibleSource.dat"
    source_file.write_bytes(b"dimension|amount")
    client = Mock(spec=EPMClient)
    client.post_binary.return_value = {"status": 0}

    result = FileService(
        client,
        allow_any_extension=True,
    ).upload_to_inbox(source_file)

    assert result.file_name == "FlexibleSource.dat"


def test_upload_to_inbox_replaces_existing_file(tmp_path) -> None:
    metadata_file = tmp_path / "metadata.zip"
    metadata_file.write_bytes(b"content")
    client = Mock(spec=EPMClient)
    client.post_binary.side_effect = [
        {
            "status": 1,
            "details": "A file or folder exists with this name",
        },
        {"status": 0, "details": None},
    ]
    client.delete.return_value = {"status": 0, "details": None}

    result = FileService(client).upload_to_inbox(metadata_file)

    assert result.is_successful
    assert result.replaced_existing is True
    assert client.post_binary.call_count == 2
    client.delete.assert_called_once_with(
        (
            "interop/rest/11.1.2.3.600/applicationsnapshots/"
            "metadata.zip"
        )
    )


def test_upload_to_inbox_does_not_delete_for_unrelated_error(
    tmp_path,
) -> None:
    metadata_file = tmp_path / "metadata.zip"
    metadata_file.write_bytes(b"content")
    client = Mock(spec=EPMClient)
    client.post_binary.return_value = {
        "status": 1,
        "details": "User does not have sufficient access",
    }

    with pytest.raises(FileUploadError, match="sufficient access"):
        FileService(client).upload_to_inbox(metadata_file)

    client.delete.assert_not_called()


def test_upload_to_inbox_can_disable_replacement(tmp_path) -> None:
    metadata_file = tmp_path / "metadata.csv"
    metadata_file.write_bytes(b"content")
    client = Mock(spec=EPMClient)
    client.post_binary.return_value = {
        "status": 1,
        "details": "A file with this name already exists",
    }

    with pytest.raises(FileUploadError, match="already exists"):
        FileService(client).upload_to_inbox(
            metadata_file,
            replace_existing=False,
        )

    client.delete.assert_not_called()


def test_upload_to_inbox_accepts_custom_data_extension(tmp_path) -> None:
    data_file = tmp_path / "PlanData.txt"
    data_file.write_bytes(b"Essbase data")
    client = Mock(spec=EPMClient)
    client.post_binary.return_value = {"status": 0}

    result = FileService(
        client,
        supported_extensions={".csv", ".txt", ".zip"},
    ).upload_to_inbox(data_file)

    assert result.is_successful
    assert result.file_name == "PlanData.txt"


def test_upload_to_inbox_can_use_a_different_safe_target_name(
    tmp_path,
) -> None:
    source_file = tmp_path / "latest.csv"
    source_file.write_bytes(b"data")
    client = Mock(spec=EPMClient)
    client.post_binary.return_value = {"status": 0}

    result = FileService(client).upload_to_inbox(
        source_file,
        target_file_name="ConfiguredPipelineFile.csv",
    )

    assert result.file_name == "ConfiguredPipelineFile.csv"
    assert client.post_binary.call_args.args[0].endswith(
        "ConfiguredPipelineFile.csv/contents"
    )


def test_upload_to_data_integration_subfolder_replaces_exact_target(
    tmp_path,
) -> None:
    source_file = tmp_path / "latest.csv"
    source_file.write_bytes(b"data")
    client = Mock(spec=EPMClient)
    client.post_binary.side_effect = [
        {"status": 1, "details": "A file with this name already exists"},
        {"status": 0},
    ]
    client.delete.return_value = {"status": 0}

    result = FileService(client).upload_to_inbox(
        source_file,
        target_file_name="Configured.csv",
        upload_directory="inbox/monthly",
    )

    assert result.replaced_existing is True
    assert client.post_binary.call_args.kwargs == {
        "params": {"extDirPath": "inbox/monthly"}
    }
    client.delete.assert_called_once_with(
        "interop/rest/11.1.2.3.600/applicationsnapshots/"
        "inbox%2Fmonthly%2FConfigured.csv"
    )


def test_upload_rejects_non_inbox_destination(tmp_path) -> None:
    source_file = tmp_path / "latest.csv"
    source_file.write_bytes(b"data")
    client = Mock(spec=EPMClient)

    with pytest.raises(FileUploadError, match="must be 'inbox'"):
        FileService(client).upload_to_inbox(
            source_file,
            upload_directory="outbox/monthly",
        )


def test_upload_to_inbox_rejects_target_directories(tmp_path) -> None:
    source_file = tmp_path / "latest.csv"
    source_file.write_bytes(b"data")
    client = Mock(spec=EPMClient)

    with pytest.raises(FileUploadError, match="without directories"):
        FileService(client).upload_to_inbox(
            source_file,
            target_file_name="inbox/latest.csv",
        )

    client.post_binary.assert_not_called()


def test_download_from_repository_returns_exact_binary_content() -> None:
    client = Mock(spec=EPMClient)
    client.get_binary.return_value = b"<rtp name='Year'/>"

    content = FileService(client).download_from_repository(
        "inbox/Calc Manager Export.xml"
    )

    assert content == b"<rtp name='Year'/>"
    client.get_binary.assert_called_once_with(
        "interop/rest/11.1.2.3.600/applicationsnapshots/"
        "inbox%2FCalc%20Manager%20Export.xml/contents"
    )


def test_download_from_repository_rejects_traversal() -> None:
    client = Mock(spec=EPMClient)

    with pytest.raises(FileUploadError, match="exact file name"):
        FileService(client).download_from_repository("../export.zip")

    client.get_binary.assert_not_called()


def test_delete_from_repository_removes_generated_snapshot() -> None:
    client = Mock(spec=EPMClient)
    client.delete.return_value = {"status": 0, "details": None}

    FileService(client).delete_from_repository("BISP_RTP_20270101")

    client.delete.assert_called_once_with(
        "interop/rest/11.1.2.3.600/applicationsnapshots/"
        "BISP_RTP_20270101"
    )
