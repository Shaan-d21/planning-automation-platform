"""Read-only discovery of files stored in an Oracle EPM repository."""

from __future__ import annotations

import logging
from collections.abc import Mapping, Sequence
from typing import Any

from app.clients.epm_client import EPMClient
from app.models.file_transfer import OracleRepositoryFile
from app.utils.exceptions import APIRequestError, FileCatalogError


class FileCatalogService:
    """Retrieve and normalize Oracle repository files through public REST APIs."""

    _V2_ENDPOINT = "interop/rest/v2/files/list"
    _V1_ENDPOINT = (
        "interop/rest/11.1.2.3.600/applicationsnapshots"
    )

    def __init__(
        self,
        client: EPMClient,
        *,
        logger: logging.Logger | None = None,
    ) -> None:
        self._client = client
        self._logger = logger or logging.getLogger(__name__)

    def list_files(self) -> tuple[OracleRepositoryFile, ...]:
        """Return external repository files, preferring Oracle's v2 API."""
        try:
            response = self._client.get(self._V2_ENDPOINT)
        except APIRequestError as exc:
            if exc.status_code not in {404, 405}:
                raise
            self._logger.warning(
                "Oracle List Files v2 is unavailable; using the compatible "
                "repository endpoint."
            )
            response = self._client.get(self._V1_ENDPOINT)
        return self._parse_response(response)

    def _parse_response(
        self,
        response: Any,
    ) -> tuple[OracleRepositoryFile, ...]:
        if not isinstance(response, Mapping):
            raise FileCatalogError(
                "Oracle EPM returned an unexpected file catalog response."
            )
        status = self._integer(response.get("status"))
        if status != 0:
            details = response.get("details")
            raise FileCatalogError(
                "Oracle EPM could not list repository files: "
                f"{details or 'No details were returned.'}"
            )
        items = response.get("items", [])
        if not isinstance(items, Sequence) or isinstance(items, (str, bytes)):
            raise FileCatalogError(
                "Oracle EPM returned an invalid repository file collection."
            )

        files: list[OracleRepositoryFile] = []
        for item in items:
            parsed = self._parse_item(item)
            if parsed is not None:
                files.append(parsed)
        return tuple(files)

    def _parse_item(self, item: object) -> OracleRepositoryFile | None:
        if not isinstance(item, Mapping):
            self._logger.warning("Ignored an invalid Oracle file catalog entry.")
            return None
        name = str(item.get("name") or "").strip().replace("\\", "/")
        file_type = str(item.get("type") or "").strip().upper()
        if not name or file_type != "EXTERNAL":
            return None
        folder = name.rpartition("/")[0] or "Inbox"
        return OracleRepositoryFile(
            name=name,
            folder=folder,
            file_type=file_type,
            size_bytes=self._integer(item.get("size")),
            last_modified_epoch_ms=self._integer(
                item.get("lastmodifiedtime")
            ),
        )

    @staticmethod
    def _integer(value: object) -> int | None:
        if value is None or value == "":
            return None
        try:
            parsed = int(value)
        except (TypeError, ValueError):
            return None
        return parsed if parsed >= 0 else None
