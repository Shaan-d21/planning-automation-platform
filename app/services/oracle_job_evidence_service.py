"""Capture bounded, downloadable Oracle import rejection evidence."""

from __future__ import annotations

import csv
import hashlib
import io
import logging
import re
import zipfile
from pathlib import Path, PurePath
from typing import Any

from app.services.file_service import FileService
from app.utils.exceptions import EPMError


class OracleJobEvidenceService:
    """Persist Oracle error files and produce safe rejected-row previews."""

    _MAX_ARCHIVE_FILES = 100
    _MAX_EXTRACTED_BYTES = 50 * 1024 * 1024
    _MAX_PREVIEW_ROWS = 200
    _SAFE_NAME = re.compile(r"[^A-Za-z0-9._-]+")
    _DATA_INTEGRATION_COUNTERS = {
        "records_read": ("read",),
        "records_processed": ("processed", "loaded", "exported"),
        "records_rejected": ("rejected", "failed"),
    }

    def __init__(
        self,
        file_service: FileService,
        runtime_data_dir: Path,
        *,
        logger: logging.Logger | None = None,
    ) -> None:
        self._files = file_service
        self._root = Path(runtime_data_dir) / "job-evidence"
        self._logger = logger or logging.getLogger(__name__)

    def capture_error_file(
        self,
        *,
        execution_id: str,
        oracle_file_name: str | None,
    ) -> dict[str, Any]:
        """Download and inspect an Oracle-generated import error file."""
        if not oracle_file_name:
            return {"artifacts": [], "rejected_records": []}
        try:
            content = self._files.download_from_repository(oracle_file_name)
        except EPMError as exc:
            self._logger.info(
                "Oracle error file was not available: execution_id=%s, file=%s, error=%s",
                execution_id,
                oracle_file_name,
                exc,
            )
            return {
                "artifacts": [],
                "rejected_records": [],
                "artifact_message": (
                    "Oracle did not publish a rejected-record file for this run."
                ),
            }
        if not isinstance(content, (bytes, bytearray)):
            self._logger.warning(
                "Oracle repository returned non-binary error-file content: execution_id=%s, file=%s",
                execution_id,
                oracle_file_name,
            )
            return {
                "artifacts": [],
                "rejected_records": [],
                "artifact_message": "Oracle did not return a readable rejected-record artifact.",
            }
        content = bytes(content)

        directory = self._execution_directory(execution_id)
        stored_name = self._unique_name(PurePath(oracle_file_name).name)
        root_path = directory / stored_name
        root_path.write_bytes(content)
        artifacts = [self._artifact(stored_name, oracle_file_name, len(content), "ORACLE_ERROR_FILE")]
        rejected_records: list[dict[str, Any]] = []

        if zipfile.is_zipfile(io.BytesIO(content)):
            extracted_bytes = 0
            with zipfile.ZipFile(io.BytesIO(content)) as archive:
                members = [item for item in archive.infolist() if not item.is_dir()]
                for index, member in enumerate(members[: self._MAX_ARCHIVE_FILES], start=1):
                    if member.file_size < 0:
                        continue
                    extracted_bytes += member.file_size
                    if extracted_bytes > self._MAX_EXTRACTED_BYTES:
                        break
                    member_content = archive.read(member)
                    display_name = PurePath(member.filename).name or f"rejections-{index}.txt"
                    child_name = self._unique_name(f"{index:03d}-{display_name}")
                    (directory / child_name).write_bytes(member_content)
                    artifacts.append(
                        self._artifact(
                            child_name,
                            display_name,
                            len(member_content),
                            "REJECTED_RECORDS",
                        )
                    )
                    preview = self._preview(display_name, member_content)
                    if preview is not None:
                        rejected_records.append(preview)
        else:
            preview = self._preview(PurePath(oracle_file_name).name, content)
            if preview is not None:
                rejected_records.append(preview)

        return {
            "artifacts": artifacts,
            "rejected_records": rejected_records,
        }

    def capture_data_integration_log(
        self,
        *,
        execution_id: str,
        oracle_file_name: str | None,
    ) -> dict[str, Any]:
        """Retain a Data Integration log and extract explicit Oracle counts.

        The Data Integration status API exposes a log file rather than the
        Planning Job Details structure used by native data and metadata jobs.
        Counts are therefore returned only when the log explicitly supplies
        an unambiguous read, processed/loaded, and rejected/failed total.
        """
        if not oracle_file_name:
            return {}
        normalized_oracle_file = (
            str(oracle_file_name).strip().replace("\\", "/").lstrip("/")
        )
        if not normalized_oracle_file:
            return {}
        try:
            content = self._files.download_from_repository(
                normalized_oracle_file
            )
        except EPMError as exc:
            self._logger.info(
                "Oracle Data Integration log was not available: "
                "execution_id=%s, file=%s, error=%s",
                execution_id,
                normalized_oracle_file,
                exc,
            )
            return {
                "artifacts": [],
                "evidence_message": (
                    "Oracle reported a Data Integration log, but it could "
                    "not be downloaded from this environment."
                ),
            }
        if not isinstance(content, (bytes, bytearray)):
            return {
                "artifacts": [],
                "evidence_message": (
                    "Oracle did not return a readable Data Integration log."
                ),
            }

        content = bytes(content)
        directory = self._execution_directory(execution_id)
        display_name = PurePath(normalized_oracle_file).name
        stored_name = self._unique_name(display_name or "integration.log")
        (directory / stored_name).write_bytes(content)
        evidence: dict[str, Any] = {
            "artifacts": [
                self._artifact(
                    stored_name,
                    display_name or stored_name,
                    len(content),
                    "ORACLE_DATA_INTEGRATION_LOG",
                )
            ]
        }
        text = self._decode(content)
        statistics = (
            self._data_integration_statistics(text) if text is not None else None
        )
        if statistics is not None:
            evidence["record_statistics"] = statistics
        else:
            evidence["evidence_message"] = (
                "The Oracle Data Integration log was retained, but it did "
                "not expose one unambiguous read, processed, and rejected "
                "record total. No counts were estimated."
            )
        return evidence

    @classmethod
    def _data_integration_statistics(
        cls,
        text: str,
    ) -> dict[str, Any] | None:
        """Parse only explicit, internally consistent Data Integration totals."""
        selected: dict[str, int] = {}
        for key, actions in cls._DATA_INTEGRATION_COUNTERS.items():
            matches: list[tuple[int, bool]] = []
            action_pattern = "|".join(re.escape(action) for action in actions)
            patterns = (
                re.compile(
                    rf"(?i)(?P<label>\b(?:grand\s+total\s+|total\s+)?"
                    rf"(?:number\s+of\s+)?(?:data\s+)?(?:records?|rows?)\s+"
                    rf"(?:{action_pattern})\b)\s*(?:[:=]|is)?\s*"
                    rf"(?P<count>\d[\d,]*)"
                ),
                re.compile(
                    rf"(?i)(?P<label>\b(?:grand\s+total\s+|total\s+)?"
                    rf"(?:{action_pattern})\s+(?:data\s+)?(?:records?|rows?)\b)"
                    rf"\s*(?:[:=]|is)?\s*(?P<count>\d[\d,]*)"
                ),
            )
            for line in text.splitlines():
                for pattern in patterns:
                    for match in pattern.finditer(line):
                        count = int(match.group("count").replace(",", ""))
                        label = match.group("label").casefold()
                        matches.append((count, "total" in label))
            if not matches:
                return None
            explicit_totals = {
                count for count, is_total in matches if is_total
            }
            distinct = {count for count, _ in matches}
            if len(explicit_totals) == 1:
                selected[key] = next(iter(explicit_totals))
            elif not explicit_totals and len(distinct) == 1:
                selected[key] = next(iter(distinct))
            else:
                return None

        return {
            "source": "ORACLE_DATA_INTEGRATION_LOG",
            **selected,
            "details": [
                {
                    "dimension_name": None,
                    "load_type": "Data Integration",
                    **selected,
                }
            ],
        }

    def _execution_directory(self, execution_id: str) -> Path:
        safe_execution_id = self._unique_name(execution_id)
        directory = self._root / safe_execution_id
        directory.mkdir(parents=True, exist_ok=True)
        return directory

    def _preview(self, file_name: str, content: bytes) -> dict[str, Any] | None:
        text = self._decode(content)
        if text is None:
            return None
        lines = text.splitlines()
        if not lines:
            return None
        try:
            dialect = csv.Sniffer().sniff("\n".join(lines[:10]), delimiters=",|\t;")
            parsed = list(csv.reader(lines, dialect=dialect))
        except csv.Error:
            parsed = [[line] for line in lines]
        if not parsed:
            return None
        width = max(len(row) for row in parsed[: self._MAX_PREVIEW_ROWS + 1])
        first = parsed[0]
        has_header = self._looks_like_header(first)
        columns = (
            [value.strip() or f"Column {index + 1}" for index, value in enumerate(first)]
            if has_header
            else ["Message"] if width == 1 else [f"Column {index + 1}" for index in range(width)]
        )
        data_rows = parsed[1:] if has_header else parsed
        rows = [
            [value.strip() for value in row] + [""] * max(0, len(columns) - len(row))
            for row in data_rows[: self._MAX_PREVIEW_ROWS]
        ]
        return {
            "file_name": file_name,
            "dimension_name": self._dimension_from_name(file_name),
            "columns": columns,
            "rows": [row[: len(columns)] for row in rows],
            "preview_count": len(rows),
            "truncated": len(data_rows) > self._MAX_PREVIEW_ROWS,
        }

    @staticmethod
    def _decode(content: bytes) -> str | None:
        for encoding in ("utf-8-sig", "utf-8", "cp1252"):
            try:
                return content.decode(encoding)
            except UnicodeDecodeError:
                continue
        return None

    @staticmethod
    def _looks_like_header(row: list[str]) -> bool:
        joined = " ".join(row).casefold()
        return any(
            marker in joined
            for marker in ("error", "reason", "message", "dimension", "member", "record")
        )

    @staticmethod
    def _dimension_from_name(file_name: str) -> str | None:
        stem = Path(file_name).stem
        normalized = re.sub(r"(?i)(errors?|rejected?|records?)", " ", stem)
        normalized = " ".join(normalized.replace("_", " ").replace("-", " ").split())
        return normalized or None

    def _unique_name(self, value: str) -> str:
        base = self._SAFE_NAME.sub("-", PurePath(str(value)).name).strip(".-") or "artifact"
        if len(base) <= 120:
            return base
        suffix = Path(base).suffix
        digest = hashlib.sha256(base.encode("utf-8")).hexdigest()[:10]
        return f"{Path(base).stem[:90]}-{digest}{suffix}"

    @staticmethod
    def _artifact(stored_name: str, display_name: str, size_bytes: int, kind: str) -> dict[str, Any]:
        return {
            "artifact_id": hashlib.sha256(stored_name.encode("utf-8")).hexdigest()[:16],
            "stored_name": stored_name,
            "name": PurePath(display_name).name,
            "kind": kind,
            "size_bytes": size_bytes,
        }
