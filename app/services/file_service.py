"""Reusable file operations for the Oracle EPM Inbox."""

from __future__ import annotations

import logging
from collections.abc import Collection, Mapping
from pathlib import Path
from typing import Any
from urllib.parse import quote

from app.clients.epm_client import EPMClient
from app.models.file_transfer import FileUploadResult
from app.utils.exceptions import EPMError, FileUploadError


class FileService:
    """Validate, upload, and download Oracle EPM repository files."""

    _DEFAULT_SUPPORTED_EXTENSIONS = frozenset({".csv", ".zip"})
    _UPLOAD_API_VERSION = "11.1.2.3.600"
    _MAX_DOWNLOAD_BYTES = 50 * 1024 * 1024

    def __init__(
        self,
        client: EPMClient,
        *,
        supported_extensions: Collection[str] | None = None,
        allow_any_extension: bool = False,
        logger: logging.Logger | None = None,
    ) -> None:
        """Initialize the service with the shared EPM client."""
        self._client = client
        self._allow_any_extension = allow_any_extension
        extensions = (
            supported_extensions
            if supported_extensions is not None
            else self._DEFAULT_SUPPORTED_EXTENSIONS
        )
        self._supported_extensions = frozenset(
            extension.lower()
            if extension.startswith(".")
            else f".{extension.lower()}"
            for extension in extensions
        )
        if not self._supported_extensions and not allow_any_extension:
            raise ValueError("supported_extensions cannot be empty.")
        self._logger = logger or logging.getLogger(__name__)

    def upload_to_inbox(
        self,
        file_path: str | Path,
        *,
        replace_existing: bool = True,
        target_file_name: str | None = None,
    ) -> FileUploadResult:
        """Upload a supported file, optionally replacing an existing file.

        The Oracle Interop non-chunked upload API accepts a binary stream and
        stores it in the environment repository used by Planning jobs.
        """
        path = self._validate_file(file_path)
        file_name = self._validate_target_file_name(
            target_file_name or path.name
        )
        encoded_file_name = quote(file_name, safe="")
        endpoint = (
            f"interop/rest/{self._UPLOAD_API_VERSION}/"
            f"applicationsnapshots/{encoded_file_name}/contents"
        )

        self._logger.info(
            "Inbox upload started: file='%s', size=%s bytes.",
            file_name,
            path.stat().st_size,
        )
        result = self._upload_once(path, endpoint, file_name)
        if (
            not result.is_successful
            and replace_existing
            and self._indicates_existing_file(result)
        ):
            self._logger.warning(
                "File already exists in the Oracle Inbox and will "
                "be replaced: '%s'.",
                file_name,
            )
            self._delete_from_inbox(file_name)
            retried_result = self._upload_once(
                path,
                endpoint,
                file_name,
            )
            result = FileUploadResult(
                file_name=retried_result.file_name,
                status=retried_result.status,
                details=retried_result.details,
                replaced_existing=True,
                raw_response=retried_result.raw_response,
            )

        if not result.is_successful:
            raise FileUploadError(
                f"Oracle EPM rejected Inbox upload '{file_name}' with "
                f"status {result.status}: "
                f"{result.details or 'No details returned.'}"
            )

        self._logger.info(
            "Inbox upload completed: file='%s', status=%s, replaced=%s.",
            file_name,
            result.status,
            result.replaced_existing,
        )
        return result

    def download_from_repository(
        self,
        file_name: str,
        *,
        maximum_bytes: int = _MAX_DOWNLOAD_BYTES,
    ) -> bytes:
        """Download one exact repository file without persisting it locally."""
        normalized = self._validate_repository_file_name(file_name)
        limit = int(maximum_bytes)
        if limit <= 0:
            raise FileUploadError("Repository download limit must be positive.")
        encoded_file_name = quote(normalized, safe="")
        endpoint = (
            f"interop/rest/{self._UPLOAD_API_VERSION}/"
            f"applicationsnapshots/{encoded_file_name}/contents"
        )
        try:
            content = self._client.get_binary(endpoint)
        except EPMError as exc:
            raise FileUploadError(
                f"Unable to download Oracle repository file '{normalized}': "
                f"{exc}"
            ) from exc
        if len(content) > limit:
            raise FileUploadError(
                f"Oracle repository file '{normalized}' exceeds the "
                f"{limit // (1024 * 1024)} MB download limit."
            )
        self._logger.info(
            "Oracle repository download completed: file='%s', size=%s bytes.",
            normalized,
            len(content),
        )
        return content

    def delete_from_repository(self, file_name: str) -> None:
        """Delete one exact repository file or snapshot."""
        normalized = self._validate_repository_file_name(file_name)
        self._delete_from_inbox(normalized)

    @staticmethod
    def _validate_repository_file_name(value: str) -> str:
        normalized = str(value).strip().replace("\\", "/")
        if (
            not normalized
            or normalized.startswith("/")
            or normalized.endswith("/")
            or any(part in {"", ".", ".."} for part in normalized.split("/"))
        ):
            raise FileUploadError(
                "Oracle repository source must be an exact file name or "
                "repository-relative path."
            )
        return normalized

    @staticmethod
    def _validate_target_file_name(value: str) -> str:
        """Return a safe root-Inbox target filename."""
        normalized = value.strip()
        if (
            not normalized
            or normalized in {".", ".."}
            or "/" in normalized
            or "\\" in normalized
        ):
            raise FileUploadError(
                "Inbox upload target must be a filename without directories."
            )
        return normalized

    def _upload_once(
        self,
        path: Path,
        endpoint: str,
        target_file_name: str,
    ) -> FileUploadResult:
        try:
            with path.open("rb") as content:
                response = self._client.post_binary(endpoint, content)
        except OSError as exc:
            raise FileUploadError(
                f"Unable to read upload file '{path}': {exc}"
            ) from exc
        except EPMError as exc:
            raise FileUploadError(
                f"Oracle EPM Inbox upload failed for '{path.name}': {exc}"
            ) from exc
        return self._parse_upload_response(target_file_name, response)

    def _delete_from_inbox(self, file_name: str) -> None:
        encoded_file_name = quote(file_name, safe="")
        endpoint = (
            f"interop/rest/{self._UPLOAD_API_VERSION}/"
            f"applicationsnapshots/{encoded_file_name}"
        )
        try:
            response = self._client.delete(endpoint)
        except EPMError as exc:
            raise FileUploadError(
                f"Unable to replace Inbox file '{file_name}' because deletion "
                f"failed: {exc}"
            ) from exc

        if not isinstance(response, Mapping):
            raise FileUploadError(
                f"Unable to replace Inbox file '{file_name}': Oracle EPM "
                "returned an unexpected delete response."
            )
        try:
            status = int(response.get("status"))
        except (TypeError, ValueError) as exc:
            raise FileUploadError(
                f"Unable to replace Inbox file '{file_name}': Oracle EPM "
                "did not return a valid delete status."
            ) from exc
        if status != 0:
            details = response.get("details")
            raise FileUploadError(
                f"Unable to replace Inbox file '{file_name}': Oracle EPM "
                f"delete returned status {status}: "
                f"{details or 'No details returned.'}"
            )

        self._logger.info(
            "Existing Oracle Inbox file deleted: '%s'.",
            file_name,
        )

    def _validate_file(self, file_path: str | Path) -> Path:
        path = Path(file_path).expanduser()
        if not path.exists():
            raise FileUploadError(
                f"Upload file does not exist: '{path}'."
            )
        if not path.is_file():
            raise FileUploadError(
                f"Upload path is not a file: '{path}'."
            )
        if (
            not self._allow_any_extension
            and path.suffix.lower() not in self._supported_extensions
        ):
            supported = ", ".join(sorted(self._supported_extensions))
            raise FileUploadError(
                f"Unsupported upload file extension '{path.suffix}'. "
                f"Supported extensions: {supported}."
            )
        return path

    @staticmethod
    def _parse_upload_response(
        file_name: str,
        response: Any,
    ) -> FileUploadResult:
        if not isinstance(response, Mapping):
            raise FileUploadError(
                "Oracle EPM returned an unexpected Inbox upload response."
            )

        status_value = response.get("status")
        try:
            status = int(status_value)
        except (TypeError, ValueError) as exc:
            raise FileUploadError(
                "Oracle EPM Inbox upload response did not contain a valid "
                "status."
            ) from exc

        details_value = response.get("details")
        return FileUploadResult(
            file_name=file_name,
            status=status,
            details=(
                str(details_value)
                if details_value is not None
                else None
            ),
            raw_response=response,
        )

    @staticmethod
    def _indicates_existing_file(result: FileUploadResult) -> bool:
        details = (result.details or "").casefold()
        return any(
            message in details
            for message in (
                "already exists",
                "file or folder exists",
                "file exists",
            )
        )
