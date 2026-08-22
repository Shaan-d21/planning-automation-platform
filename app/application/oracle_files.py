"""Application use case for mouse-first Oracle Inbox file selection."""

from __future__ import annotations

import logging
from dataclasses import dataclass, replace
from enum import StrEnum
from pathlib import PurePosixPath

from app.clients.epm_client import EPMClient
from app.config.settings import Settings
from app.models.file_transfer import OracleRepositoryFile
from app.services.file_catalog_service import FileCatalogService


class OracleFilePurpose(StrEnum):
    """Supported operation-specific file catalogs."""

    DATA_IMPORT = "data-import"
    METADATA_IMPORT = "metadata-import"
    DATA_INTEGRATION = "data-integration"
    PIPELINE = "pipeline"


@dataclass(frozen=True, slots=True)
class OracleFileCatalog:
    """Filtered live files available for one operation purpose."""

    purpose: OracleFilePurpose
    files: tuple[OracleRepositoryFile, ...]


class OracleFileCatalogApplicationService:
    """Authenticate, discover, filter, and sort selectable Oracle files."""

    _EXTENSIONS = {
        OracleFilePurpose.DATA_IMPORT: frozenset({".csv", ".txt", ".zip"}),
        OracleFilePurpose.METADATA_IMPORT: frozenset({".csv", ".zip"}),
        OracleFilePurpose.DATA_INTEGRATION: frozenset(
            {".csv", ".txt", ".zip", ".dat"}
        ),
        OracleFilePurpose.PIPELINE: frozenset(
            {".csv", ".txt", ".zip", ".dat"}
        ),
    }
    _EXCLUDED_ROOTS = frozenset(
        {"outbox", "profitoutbox", "reports", "scheduler output"}
    )

    def __init__(
        self,
        settings: Settings | None = None,
        *,
        client: EPMClient | None = None,
        logger: logging.Logger | None = None,
    ) -> None:
        if settings is None and client is None:
            raise ValueError("Settings or an EPM client is required.")
        self._settings = settings
        self._client = client
        self._logger = logger or logging.getLogger(__name__)

    def discover(self, purpose: OracleFilePurpose) -> OracleFileCatalog:
        """Return compatible Inbox files for the requested operation."""
        if self._client is not None:
            discovered = FileCatalogService(
                self._client,
                logger=self._logger.getChild("catalog"),
            ).list_files()
        else:
            if self._settings is None:
                raise RuntimeError("Oracle file catalog settings are unavailable.")
            with EPMClient(
                self._settings,
                logger=self._logger.getChild("client"),
            ) as client:
                client.authenticate()
                discovered = FileCatalogService(
                    client,
                    logger=self._logger.getChild("catalog"),
                ).list_files()

        extensions = self._EXTENSIONS[purpose]
        files = tuple(
            sorted(
                (
                    self._operation_reference(item, purpose)
                    for item in discovered
                    if self._is_inbox_file(item.name)
                    and PurePosixPath(item.name).suffix.casefold()
                    in extensions
                ),
                key=lambda item: (
                    -(item.last_modified_epoch_ms or 0),
                    item.name.casefold(),
                ),
            )
        )
        return OracleFileCatalog(purpose=purpose, files=files)

    @staticmethod
    def _operation_reference(
        item: OracleRepositoryFile,
        purpose: OracleFilePurpose,
    ) -> OracleRepositoryFile:
        if purpose is not OracleFilePurpose.DATA_INTEGRATION:
            return item
        normalized = item.name.strip().replace("\\", "/")
        if normalized.casefold().startswith(("inbox/", "#epminbox/")):
            return item
        return replace(item, name=f"#epminbox/{normalized}")

    @classmethod
    def _is_inbox_file(cls, name: str) -> bool:
        normalized = name.strip().replace("\\", "/")
        if not normalized:
            return False
        root = normalized.split("/", 1)[0].casefold()
        return root not in cls._EXCLUDED_ROOTS
