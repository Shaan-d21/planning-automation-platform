"""Automated Calculation Manager migration export through Oracle REST."""

from __future__ import annotations

import logging
import re
import time
import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from urllib.parse import urlparse

from app.clients.epm_client import EPMClient
from app.config.settings import Settings
from app.services.file_service import FileService
from app.utils.exceptions import APIRequestError, BusinessRuleError


@dataclass(frozen=True, slots=True)
class GeneratedCalcManagerSnapshot:
    """One fresh Oracle-generated Calculation Manager LCM package."""

    snapshot_name: str
    content: bytes


class CalcManagerExportService:
    """Create, monitor, download, and clean up Calc Manager snapshots."""

    _CATEGORY = "Calculation Manager"
    _CATEGORIES_ENDPOINT = "interop/rest/v2/migration/categories/list"
    _EXPORT_ENDPOINT = (
        "interop/rest/v2/migration/categories/artifacts/export"
    )
    _SAFE_PREFIX = re.compile(r"[^A-Za-z0-9_.-]+")

    def __init__(
        self,
        settings: Settings,
        *,
        logger: logging.Logger | None = None,
    ) -> None:
        self._settings = settings
        self._logger = logger or logging.getLogger(__name__)

    def ensure_supported(self) -> None:
        """Confirm this environment exposes Calculation Manager export v2."""
        try:
            with EPMClient(self._settings) as client:
                client.authenticate()
                response = client.get(self._CATEGORIES_ENDPOINT)
        except APIRequestError as exc:
            if exc.status_code in {404, 405}:
                raise BusinessRuleError(
                    "This Oracle environment does not expose the Migration v2 "
                    "category export API required for fully automated RTP "
                    "synchronization. No schedule was created."
                ) from exc
            raise
        self._require_success(response, "Calculation Manager category discovery")
        items = response.get("items") if isinstance(response, Mapping) else None
        if not isinstance(items, Sequence) or isinstance(items, (str, bytes)):
            raise BusinessRuleError(
                "Oracle returned an invalid Migration category catalog."
            )
        categories = {
            str(item.get("categoryName") or "").strip().casefold()
            for item in items
            if isinstance(item, Mapping)
        }
        if self._CATEGORY.casefold() not in categories:
            raise BusinessRuleError(
                "Calculation Manager is not available as an export category "
                "in this Oracle environment. No schedule was created."
            )

    def generate(self, snapshot_prefix: str) -> GeneratedCalcManagerSnapshot:
        """Create and download a fresh Calc Manager snapshot."""
        prefix = self.normalize_snapshot_prefix(snapshot_prefix)
        snapshot_name = self._unique_snapshot_name(prefix)
        with EPMClient(self._settings) as client:
            client.authenticate()
            response = client.post(
                self._EXPORT_ENDPOINT,
                payload={
                    "snapshotName": snapshot_name,
                    "categories": [
                        {
                            "categoryName": self._CATEGORY,
                            "selectedArtifacts": ["//"],
                        }
                    ],
                },
            )
            terminal = self._wait_for_export(client, response)
            self._require_success(terminal, "Calculation Manager export")
            content = FileService(
                client,
                allow_any_extension=True,
                logger=self._logger.getChild("files"),
            ).download_from_repository(snapshot_name)
        self._logger.info(
            "Calculation Manager snapshot generated: name='%s', size=%s bytes.",
            snapshot_name,
            len(content),
        )
        return GeneratedCalcManagerSnapshot(snapshot_name, content)

    def delete(self, snapshot_name: str) -> None:
        """Best-effort cleanup of a generated snapshot after registry import."""
        try:
            with EPMClient(self._settings) as client:
                client.authenticate()
                FileService(
                    client,
                    allow_any_extension=True,
                    logger=self._logger.getChild("cleanup"),
                ).delete_from_repository(snapshot_name)
        except Exception:
            self._logger.warning(
                "Generated Calc Manager snapshot cleanup failed: '%s'.",
                snapshot_name,
                exc_info=True,
            )

    @classmethod
    def normalize_snapshot_prefix(cls, value: str) -> str:
        normalized = cls._SAFE_PREFIX.sub("_", str(value).strip()).strip("_.-")
        if not normalized:
            raise BusinessRuleError("Snapshot name prefix is required.")
        if len(normalized) > 48:
            raise BusinessRuleError(
                "Snapshot name prefix cannot exceed 48 characters."
            )
        return normalized

    def _wait_for_export(
        self,
        client: EPMClient,
        response: object,
    ) -> Mapping[str, object]:
        current = self._mapping(response, "Calculation Manager export")
        status = self._status(current, "Calculation Manager export")
        if status != -1:
            return current
        status_endpoint = self._job_status_endpoint(current)
        deadline = time.monotonic() + self._settings.default_job_timeout
        while status == -1:
            if time.monotonic() >= deadline:
                raise BusinessRuleError(
                    "Calculation Manager export did not finish within "
                    f"{self._settings.default_job_timeout:g} seconds."
                )
            time.sleep(self._settings.default_poll_interval)
            current = self._mapping(
                client.get(status_endpoint),
                "Calculation Manager export status",
            )
            status = self._status(current, "Calculation Manager export status")
        return current

    def _job_status_endpoint(self, response: Mapping[str, object]) -> str:
        links = response.get("links")
        if not isinstance(links, Sequence) or isinstance(links, (str, bytes)):
            raise BusinessRuleError(
                "Oracle did not return a migration status link for the "
                "Calculation Manager export."
            )
        href = next(
            (
                str(item.get("href") or "").strip()
                for item in links
                if isinstance(item, Mapping)
                and str(item.get("rel") or "").strip().casefold()
                == "job status"
            ),
            "",
        )
        if not href:
            raise BusinessRuleError(
                "Oracle did not return a migration status link for the "
                "Calculation Manager export."
            )
        parsed = urlparse(href)
        endpoint = parsed.path.lstrip("/")
        if parsed.query:
            endpoint = f"{endpoint}?{parsed.query}"
        if not endpoint.startswith("interop/rest/"):
            raise BusinessRuleError(
                "Oracle returned an invalid migration status link."
            )
        # Oracle Cloud can return a canonical or load-balancer host that is
        # different from the customer-facing EPM_BASE_URL.  Keep only the
        # validated Migration API path so EPMClient sends credentials solely
        # to the configured environment host.
        return endpoint

    @staticmethod
    def _mapping(value: object, label: str) -> Mapping[str, object]:
        if not isinstance(value, Mapping):
            raise BusinessRuleError(f"Oracle returned an invalid {label} response.")
        return value

    @classmethod
    def _require_success(cls, value: object, label: str) -> None:
        response = cls._mapping(value, label)
        status = cls._status(response, label)
        if status != 0:
            details = str(response.get("details") or "No details were returned.")
            raise BusinessRuleError(f"{label} failed: {details}")

    @staticmethod
    def _status(response: Mapping[str, object], label: str) -> int:
        try:
            return int(response.get("status"))
        except (TypeError, ValueError) as exc:
            raise BusinessRuleError(
                f"Oracle did not return a valid {label} status."
            ) from exc

    @staticmethod
    def _unique_snapshot_name(prefix: str) -> str:
        timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
        return f"{prefix}_{timestamp}_{uuid.uuid4().hex[:8]}"
