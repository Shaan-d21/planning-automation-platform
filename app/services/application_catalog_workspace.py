"""Persistent application-owned JSON catalog locations."""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

from app.config.settings import Settings
from app.utils.exceptions import ConfigurationError


class ApplicationCatalogWorkspace:
    """Resolve legacy seed catalogs into one application's namespace.

    The configured JSON files remain immutable deployment seeds.  Runtime
    reads and writes use the persistent runtime-data volume so one Oracle
    application's allow-lists cannot become another application's catalog.
    """

    _CATALOGS = {
        "pipelines": ("pipelines.json", "pipelines"),
        "data_integrations": ("data_integrations.json", "integrations"),
        "planning_processes": ("planning_processes.json", "processes"),
        "planning_cycles": ("planning_cycles.json", "cycles"),
    }

    def __init__(
        self,
        settings: Settings,
        *,
        application_id: int | None,
        seed_from_configuration: bool = False,
    ) -> None:
        self._settings = settings
        self.application_id = application_id
        self._seed_from_configuration = seed_from_configuration

    @property
    def pipeline_catalog_file(self) -> Path:
        return self._resolve(
            "pipelines",
            self._settings.pipeline_catalog_file,
        )

    @property
    def data_integration_catalog_file(self) -> Path:
        return self._resolve(
            "data_integrations",
            self._settings.data_integration_catalog_file,
        )

    @property
    def planning_process_catalog_file(self) -> Path:
        return self._resolve(
            "planning_processes",
            self._settings.planning_process_catalog_file,
        )

    @property
    def planning_cycle_catalog_file(self) -> Path:
        return self._resolve(
            "planning_cycles",
            self._settings.planning_cycle_catalog_file,
        )

    def _resolve(self, catalog: str, configured_file: Path) -> Path:
        if self.application_id is None:
            return configured_file
        filename, root_key = self._CATALOGS[catalog]
        target = (
            self._settings.runtime_storage_dir
            / "application-catalogs"
            / f"application-{self.application_id}"
            / filename
        )
        if target.exists():
            return target
        document = (
            self._read_seed(configured_file, root_key)
            if self._seed_from_configuration
            else {"version": 1, root_key: []}
        )
        self._write_once(target, document)
        return target

    @staticmethod
    def _read_seed(path: Path, root_key: str) -> dict[str, object]:
        try:
            document = json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError as exc:
            raise ConfigurationError(
                f"Configured catalog seed does not exist: '{path}'."
            ) from exc
        except (OSError, json.JSONDecodeError) as exc:
            raise ConfigurationError(
                f"Configured catalog seed is not readable JSON: '{path}'."
            ) from exc
        if not isinstance(document, dict) or not isinstance(
            document.get(root_key), list
        ):
            raise ConfigurationError(
                f"Configured catalog seed '{path}' requires a "
                f"'{root_key}' array."
            )
        return document

    @staticmethod
    def _write_once(path: Path, document: dict[str, object]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.exists():
            return
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=f".{path.name}.",
            suffix=".tmp",
            dir=path.parent,
        )
        temporary = Path(temporary_name)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                json.dump(document, handle, indent=2)
                handle.write("\n")
            try:
                temporary.replace(path)
            except FileExistsError:
                pass
        finally:
            temporary.unlink(missing_ok=True)
