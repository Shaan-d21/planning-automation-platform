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
