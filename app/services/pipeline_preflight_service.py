"""Discover and prepare generic external file inputs for Oracle pipelines."""

from __future__ import annotations

import logging
import re
from collections import OrderedDict
from collections.abc import Mapping, Sequence
from pathlib import Path, PurePath

from app.clients.epm_client import EPMClient
from app.models.pipeline import (
    PipelineDetails,
    PipelineJob,
    PipelineStage,
    PipelineVariable,
)
from app.models.pipeline_input import (
    PipelineFileConsumer,
    PipelineFileRequirement,
    PipelineFileSelection,
    PipelineFileSource,
)
from app.services.file_service import FileService
from app.utils.exceptions import ConfigurationError

_EPM_INBOX_PREFIX = "#epminbox/"


class PipelinePreflightService:
    """Discover, validate, and stage files required by any pipeline."""

    _VARIABLE_PATTERN = re.compile(
        r"^\s*(?:\$|\$\{)([A-Za-z0-9_]+)\}?\s*$"
    )
    _INPUT_FILE_PARAMETERS = frozenset(
        {
            "filename",
            "inputfilename",
            "importfilename",
            "importzipfilename",
            "sourcefilename",
            "zipfilename",
        }
    )
    _FILE_CONSUMER_JOB_TYPES = frozenset(
        {
            "integration",
            "importdata",
            "importmetadata",
            "importmapping",
            "importsecurity",
            "importexchangerates",
            "importvalidintersections",
            "importcelllevelsecurity",
        }
    )
    _METADATA_JOB_TYPES = frozenset({"importmetadata"})
    _DATA_JOB_TYPES = frozenset(
        {
            "integration",
            "importdata",
            "importmapping",
            "importsecurity",
            "importexchangerates",
            "importvalidintersections",
            "importcelllevelsecurity",
        }
    )

    def __init__(
        self,
        *,
        logger: logging.Logger | None = None,
    ) -> None:
        """Initialize the reusable pre-flight planner."""
        self._logger = logger or logging.getLogger(__name__)

    def discover_file_requirements(
        self,
        details: PipelineDetails,
    ) -> tuple[PipelineFileRequirement, ...]:
        """Discover FILE variables and fixed file-consuming job parameters."""
        requirements: OrderedDict[str, PipelineFileRequirement] = (
            OrderedDict()
        )
        variables = {
            variable.name.casefold(): variable
            for variable in details.variables
        }
        file_variables = {
            variable.name.casefold(): variable
            for variable in details.variables
            if (variable.variable_type or "").casefold() == "file"
        }

        for variable in sorted(
            file_variables.values(),
            key=lambda item: item.sequence,
        ):
            requirements[f"variable:{variable.name.casefold()}"] = (
                PipelineFileRequirement(
                    key=variable.name,
                    display_name=variable.display_name,
                    variable_name=variable.name,
                    configured_reference=variable.default_value,
                    allowed_extensions=frozenset(),
                    consumers=(),
                    required=variable.requires_value,
                )
            )

        for stage in details.stages:
            for job in stage.jobs:
                self._add_job_file_requirements(
                    requirements,
                    variables=variables,
                    stage=stage,
                    job=job,
                )

        discovered = tuple(requirements.values())
        self._logger.info(
            "Pipeline pre-flight discovered %s external file input(s) for "
            "pipeline '%s'.",
            len(discovered),
            details.code,
        )
        return discovered

    def stage_uploads(
        self,
        client: EPMClient,
        selections: Sequence[PipelineFileSelection],
    ) -> None:
        """Upload every selected local file after confirmation."""
        for selection in selections:
            if not selection.requires_upload:
                self._logger.info(
                    "Using existing Oracle Inbox file for pipeline input "
                    "'%s': '%s'.",
                    selection.requirement.key,
                    selection.oracle_reference,
                )
                continue

            if selection.local_path is None:
                raise ConfigurationError(
                    f"Pipeline input '{selection.requirement.key}' has no "
                    "local upload path."
                )
            target_name = self._upload_target_name(
                selection.oracle_reference
            )
            service = FileService(
                client,
                supported_extensions=(
                    selection.requirement.allowed_extensions or None
                ),
                allow_any_extension=(
                    selection.requirement.allows_any_extension
                ),
                logger=self._logger.getChild("file_service"),
            )
            service.upload_to_inbox(
                selection.local_path,
                target_file_name=target_name,
            )

    def _add_job_file_requirements(
        self,
        requirements: OrderedDict[str, PipelineFileRequirement],
        *,
        variables: Mapping[str, PipelineVariable],
        stage: PipelineStage,
        job: PipelineJob,
    ) -> None:
        normalized_job_type = self._normalize_token(job.job_type or "")
        if normalized_job_type not in self._FILE_CONSUMER_JOB_TYPES:
            return

        for parameter in job.parameters:
            if (
                self._normalize_token(parameter.name)
                not in self._INPUT_FILE_PARAMETERS
                or not parameter.value
            ):
                continue
            consumer = PipelineFileConsumer(
                stage_name=stage.name,
                job_name=job.name,
                job_type=job.job_type or "Unknown",
                parameter_name=parameter.name,
            )
            variable_name = self._referenced_variable(parameter.value)
            variable = (
                variables.get(variable_name.casefold())
                if variable_name
                else None
            )
            if variable_name and variable is not None:
                key = f"variable:{variable_name.casefold()}"
                existing = requirements.get(key)
                requirements[key] = PipelineFileRequirement(
                    key=(existing.key if existing else variable.name),
                    display_name=(
                        existing.display_name
                        if existing
                        else variable.display_name
                    ),
                    variable_name=(
                        existing.variable_name
                        if existing
                        else variable.name
                    ),
                    configured_reference=(
                        existing.configured_reference
                        if existing
                        else variable.default_value
                    ),
                    allowed_extensions=self._extensions_for_job_type(
                        normalized_job_type
                    ),
                    consumers=(
                        existing.consumers + (consumer,)
                        if existing
                        else (consumer,)
                    ),
                    required=True,
                )
                continue
            if variable_name:
                continue

            reference = parameter.value.strip()
            key = f"fixed:{reference.casefold()}"
            existing = requirements.get(key)
            consumers = (
                existing.consumers + (consumer,)
                if existing
                else (consumer,)
            )
            requirements[key] = PipelineFileRequirement(
                key=reference,
                display_name=f"File for {job.name}",
                variable_name=None,
                configured_reference=reference,
                allowed_extensions=self._extensions_for_job_type(
                    normalized_job_type
                ),
                consumers=consumers,
                required=True,
            )

    @classmethod
    def _extensions_for_job_type(
        cls,
        normalized_job_type: str,
    ) -> frozenset[str]:
        if normalized_job_type in cls._METADATA_JOB_TYPES:
            return frozenset({".csv", ".zip"})
        if normalized_job_type in cls._DATA_JOB_TYPES:
            return frozenset({".csv", ".txt", ".zip"})
        return frozenset()

    @classmethod
    def _referenced_variable(cls, value: str) -> str | None:
        match = cls._VARIABLE_PATTERN.fullmatch(value)
        return match.group(1) if match else None

    @staticmethod
    def _normalize_token(value: str) -> str:
        return "".join(character for character in value.casefold() if character.isalnum())

    @staticmethod
    def _upload_target_name(reference: str) -> str:
        normalized = reference.strip().replace("\\", "/")
        target_name = PurePath(normalized).name
        if not target_name:
            raise ConfigurationError(
                f"Invalid Oracle Inbox file reference '{reference}'."
            )
        return target_name


def validate_local_pipeline_file(
    requirement: PipelineFileRequirement,
    value: str | Path,
) -> Path:
    """Validate a local selection before the user confirms execution."""
    path = Path(value).expanduser()
    if not path.is_file():
        raise ConfigurationError(
            f"Local pipeline input file does not exist: '{path}'."
        )
    if (
        requirement.allowed_extensions
        and path.suffix.casefold() not in requirement.allowed_extensions
    ):
        supported = ", ".join(sorted(requirement.allowed_extensions))
        raise ConfigurationError(
            f"Pipeline input '{requirement.display_name}' requires one of: "
            f"{supported}."
        )
    return path


def local_upload_oracle_reference(
    requirement: PipelineFileRequirement,
    local_path: str | Path,
) -> str:
    """Build the Oracle reference for a framework-uploaded local file.

    The shared file service uploads into Applications Inbox/Outbox. Data
    Integration must address that repository through ``#epminbox/``.
    Planning import jobs consume the uploaded file name directly.
    """
    file_name = Path(local_path).name
    if not file_name:
        raise ConfigurationError(
            f"Pipeline input '{requirement.display_name}' has an invalid "
            "local file name."
        )

    if requirement.variable_name is None:
        reference = requirement.configured_reference
        if not reference:
            raise ConfigurationError(
                f"Pipeline input '{requirement.display_name}' has no fixed "
                "Oracle file reference."
            )
        return reference

    if (
        requirement.is_data_integration_input
        and requirement.has_non_data_integration_consumer
    ):
        raise ConfigurationError(
            f"Pipeline variable '{requirement.variable_name}' is shared by "
            "Data Integration and Planning import jobs. Configure separate "
            "file variables because these jobs use different Inbox "
            "references."
        )
    if requirement.is_data_integration_input:
        return f"{_EPM_INBOX_PREFIX}{file_name}"
    return file_name


def build_pipeline_file_selection(
    requirement: PipelineFileRequirement,
    *,
    source: PipelineFileSource,
    oracle_reference: str,
    local_path: Path | None = None,
) -> PipelineFileSelection:
    """Build one validated external file selection."""
    normalized_reference = oracle_reference.strip()
    if not normalized_reference:
        raise ConfigurationError(
            f"Pipeline input '{requirement.display_name}' requires an "
            "Oracle Inbox file reference."
        )
    if source is PipelineFileSource.LOCAL_UPLOAD:
        if local_path is None:
            raise ConfigurationError(
                f"Pipeline input '{requirement.display_name}' requires a "
                "local file."
            )
        local_path = validate_local_pipeline_file(requirement, local_path)
        _validate_local_upload_repository(
            requirement,
            normalized_reference,
        )
    return PipelineFileSelection(
        requirement=requirement,
        source=source,
        oracle_reference=normalized_reference,
        local_path=local_path,
    )


def _validate_local_upload_repository(
    requirement: PipelineFileRequirement,
    oracle_reference: str,
) -> None:
    """Prevent an upload into one Inbox being read from another Inbox."""
    if not requirement.is_data_integration_input:
        return

    normalized = oracle_reference.replace("\\", "/").casefold()
    if normalized.startswith(_EPM_INBOX_PREFIX):
        return

    if requirement.variable_name is None:
        raise ConfigurationError(
            f"Pipeline job input '{requirement.configured_reference}' is a "
            "fixed Data Integration file reference. Local files are uploaded "
            "to Applications Inbox, but a plain fixed reference can read "
            "from Data Integration home instead. Configure a pipeline FILE "
            "variable and use it as the Integration job's File Name."
        )

    raise ConfigurationError(
        f"Data Integration pipeline variable "
        f"'{requirement.variable_name}' must use a '#epminbox/' reference "
        "for a local file uploaded by this framework."
    )
