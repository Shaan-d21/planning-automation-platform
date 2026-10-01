"""Tests for retained Oracle import error artifacts."""

from __future__ import annotations

import io
import zipfile
from unittest.mock import Mock

from app.services.oracle_job_evidence_service import OracleJobEvidenceService


def test_capture_error_zip_extracts_rejected_record_preview(tmp_path) -> None:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr(
            "Account_Errors.csv",
            "Member,Parent,Error\nBad Account,Total Account,Invalid parent\n",
        )
    files = Mock()
    files.download_from_repository.return_value = buffer.getvalue()

    evidence = OracleJobEvidenceService(files, tmp_path).capture_error_file(
        execution_id="4fb405b1-8caf-4e96-8434-717a1dce89d4",
        oracle_file_name="metadata-errors.zip",
    )

    assert len(evidence["artifacts"]) == 2
    assert evidence["rejected_records"][0]["dimension_name"] == "Account"
    assert evidence["rejected_records"][0]["columns"] == [
        "Member",
        "Parent",
        "Error",
    ]
    assert evidence["rejected_records"][0]["rows"][0] == [
        "Bad Account",
        "Total Account",
        "Invalid parent",
    ]


def test_capture_data_integration_log_retains_file_and_explicit_counts(
    tmp_path,
) -> None:
    files = Mock()
    files.download_from_repository.return_value = (
        b"Data Integration execution summary\n"
        b"Total records read: 1,250\n"
        b"Total records loaded: 1,245\n"
        b"Total records rejected: 5\n"
    )

    evidence = OracleJobEvidenceService(
        files,
        tmp_path,
    ).capture_data_integration_log(
        execution_id="integration-run-1",
        oracle_file_name=r"\outbox\logs\Forecast_Load_101.log",
    )

    assert evidence["record_statistics"] == {
        "source": "ORACLE_DATA_INTEGRATION_LOG",
        "records_read": 1250,
        "records_processed": 1245,
        "records_rejected": 5,
        "details": [
            {
                "dimension_name": None,
                "load_type": "Data Integration",
                "records_read": 1250,
                "records_processed": 1245,
                "records_rejected": 5,
            }
        ],
    }
    assert evidence["artifacts"][0]["kind"] == (
        "ORACLE_DATA_INTEGRATION_LOG"
    )
    files.download_from_repository.assert_called_once_with(
        "outbox/logs/Forecast_Load_101.log"
    )
    assert (
        tmp_path
        / "job-evidence"
        / "integration-run-1"
        / "Forecast_Load_101.log"
    ).read_bytes().startswith(b"Data Integration")


def test_data_integration_log_does_not_guess_ambiguous_stage_counts(
    tmp_path,
) -> None:
    files = Mock()
    files.download_from_repository.return_value = (
        b"Records read: 10\n"
        b"Records read: 20\n"
        b"Records processed: 30\n"
        b"Records rejected: 0\n"
    )

    evidence = OracleJobEvidenceService(
        files,
        tmp_path,
    ).capture_data_integration_log(
        execution_id="integration-run-2",
        oracle_file_name="outbox/logs/ambiguous.log",
    )

    assert "record_statistics" not in evidence
    assert "No counts were estimated" in evidence["evidence_message"]
