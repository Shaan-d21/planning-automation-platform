"""Typed models for Oracle Data Integration Pipeline execution."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from app.utils.exceptions import APIRequestError


@dataclass(frozen=True, slots=True)
class PipelineVariable:
    """One runtime variable configured on an Oracle pipeline."""

    name: str
    display_name: str
    default_value: str | None
    variable_type: str | None
    value_object: str | None
    sequence: int
    is_default_parameter: bool
    is_required: bool = False

    @classmethod
    def from_response(cls, response: Mapping[str, Any]) -> PipelineVariable:
        """Create a variable from the Get Pipeline Details response."""
        name = str(response.get("varName", "")).strip()
        if not name:
            raise APIRequestError(
                "Oracle returned a pipeline variable without a name."
            )
        display_name = (
            str(response.get("varDisplayName", "")).strip() or name
        )
        default_value = _optional_text(response.get("varDefaultValue"))
        variable_type = _optional_text(response.get("varType"))
        value_object = _optional_text(response.get("varValObject"))
        try:
            sequence = int(response.get("varSequence", 0))
        except (TypeError, ValueError) as exc:
            raise APIRequestError(
                f"Oracle returned an invalid sequence for pipeline "
                f"variable '{name}'."
            ) from exc

        return cls(
            name=name,
            display_name=display_name,
            default_value=default_value,
            variable_type=variable_type,
            value_object=value_object,
            sequence=sequence,
            is_default_parameter=(
                str(response.get("varDefaultParam", "N"))
                .strip()
                .casefold()
                in {"y", "yes", "true", "1"}
            ),
            is_required=_response_bool(
                response,
                "varRequired",
                "required",
                "isRequired",
                "varRequiredParam",
            ),
        )

    @property
    def requires_value(self) -> bool:
        """Return whether Oracle documents this standard variable as required."""
        return self.is_required or self.name.upper() in {
            "STARTPERIOD",
            "ENDPERIOD",
            "IMPORTMODE",
            "EXPORTMODE",
        }


@dataclass(frozen=True, slots=True)
class PipelineJobParameter:
    """One configured parameter belonging to a pipeline job."""

    name: str
    value: str | None
    level: str | None = None

    @classmethod
    def from_response(
        cls,
        response: Mapping[str, Any],
    ) -> PipelineJobParameter:
        """Create a job parameter from the Oracle response."""
        name = str(response.get("paramName", "")).strip()
        if not name:
            raise APIRequestError(
                "Oracle returned a pipeline job parameter without a name."
            )
        return cls(
            name=name,
            value=_optional_text(response.get("paramValue")),
            level=_optional_text(response.get("paramLevel")),
        )


@dataclass(frozen=True, slots=True)
class PipelineJob:
    """One job contained in a pipeline stage."""

    name: str
    job_type: str | None = None
    sequence: int = 0
    parameters: tuple[PipelineJobParameter, ...] = ()

    @classmethod
    def from_response(cls, response: Mapping[str, Any]) -> PipelineJob:
        """Create a compact job description from an Oracle response."""
        name = str(response.get("jobName", "")).strip()
        if not name:
            raise APIRequestError(
                "Oracle returned a pipeline stage job without a name."
            )
        raw_parameters = response.get("parameters") or ()
        if not isinstance(raw_parameters, Sequence) or isinstance(
            raw_parameters,
            (str, bytes),
        ):
            raise APIRequestError(
                f"Oracle returned invalid parameters for pipeline job "
                f"'{name}'."
            )
        try:
            sequence = int(response.get("jobSeq", 0))
        except (TypeError, ValueError) as exc:
            raise APIRequestError(
                f"Oracle returned an invalid sequence for pipeline job "
                f"'{name}'."
            ) from exc
        return cls(
            name=name,
            job_type=_optional_text(response.get("jobType")),
            sequence=sequence,
            parameters=tuple(
                PipelineJobParameter.from_response(item)
                for item in raw_parameters
                if isinstance(item, Mapping)
            ),
        )


@dataclass(frozen=True, slots=True)
class PipelineStage:
    """One ordered stage in an Oracle pipeline."""

    name: str
    display_name: str
    sequence: int
    runs_in_parallel: bool
    jobs: tuple[PipelineJob, ...]

    @classmethod
    def from_response(cls, response: Mapping[str, Any]) -> PipelineStage:
        """Create a stage and its jobs from an Oracle response."""
        name = str(response.get("stageName", "")).strip()
        if not name:
            raise APIRequestError(
                "Oracle returned a pipeline stage without a name."
            )
        raw_jobs = response.get("jobs") or ()
        if not isinstance(raw_jobs, Sequence) or isinstance(
            raw_jobs,
            (str, bytes),
        ):
            raise APIRequestError(
                f"Oracle returned invalid jobs for pipeline stage '{name}'."
            )
        jobs = tuple(
            sorted(
                (
                    PipelineJob.from_response(item)
                    for item in raw_jobs
                    if isinstance(item, Mapping)
                ),
                key=lambda item: item.sequence,
            )
        )
        try:
            sequence = int(response.get("stageSequence", 0))
        except (TypeError, ValueError) as exc:
            raise APIRequestError(
                f"Oracle returned an invalid sequence for pipeline stage "
                f"'{name}'."
            ) from exc

        return cls(
            name=name,
            display_name=(
                str(response.get("stageDisplayName", "")).strip() or name
            ),
            sequence=sequence,
            runs_in_parallel=(
                str(response.get("stageParallel", "N"))
                .strip()
                .casefold()
                in {"y", "yes", "true", "1"}
            ),
            jobs=jobs,
        )


@dataclass(frozen=True, slots=True)
class PipelineDetails:
    """Current definition returned for an Oracle pipeline code."""

    code: str
    display_name: str
    parallel_jobs: int | None
    variables: tuple[PipelineVariable, ...]
    stages: tuple[PipelineStage, ...]

    @classmethod
    def from_response(cls, response: Mapping[str, Any]) -> PipelineDetails:
        """Create pipeline details from the response payload."""
        code = str(response.get("name", "")).strip()
        if not code:
            raise APIRequestError(
                "Oracle returned pipeline details without a pipeline code."
            )

        raw_variables = response.get("variables") or ()
        raw_stages = response.get("stages") or ()
        if not isinstance(raw_variables, Sequence) or isinstance(
            raw_variables,
            (str, bytes),
        ):
            raise APIRequestError(
                f"Oracle returned invalid variables for pipeline '{code}'."
            )
        if not isinstance(raw_stages, Sequence) or isinstance(
            raw_stages,
            (str, bytes),
        ):
            raise APIRequestError(
                f"Oracle returned invalid stages for pipeline '{code}'."
            )

        variables = sorted(
            (
                PipelineVariable.from_response(item)
                for item in raw_variables
                if isinstance(item, Mapping)
            ),
            key=lambda item: item.sequence,
        )
        stages = sorted(
            (
                PipelineStage.from_response(item)
                for item in raw_stages
                if isinstance(item, Mapping)
            ),
            key=lambda item: item.sequence,
        )
        parallel_jobs_value = response.get("parallelJobs")
        try:
            parallel_jobs = (
                int(parallel_jobs_value)
                if parallel_jobs_value is not None
                else None
            )
        except (TypeError, ValueError) as exc:
            raise APIRequestError(
                f"Oracle returned invalid parallel job details for pipeline "
                f"'{code}'."
            ) from exc

        return cls(
            code=code,
            display_name=(
                str(response.get("displayName", "")).strip() or code
            ),
            parallel_jobs=parallel_jobs,
            variables=tuple(variables),
            stages=tuple(stages),
        )

    @property
    def job_count(self) -> int:
        """Return the total number of jobs across every stage."""
        return sum(len(stage.jobs) for stage in self.stages)


@dataclass(frozen=True, slots=True)
class PipelineSubmission:
    """Result returned after submitting a pipeline through REST."""

    job_id: int
    pipeline_code: str
    variables: tuple[tuple[str, str], ...]


@dataclass(frozen=True, slots=True)
class PipelineCommandResult:
    """Successful pipeline result returned by EPM Automate."""

    pipeline_code: str
    variables: tuple[tuple[str, str], ...]
    command_output: str


def _optional_text(value: Any) -> str | None:
    if value is None:
        return None
    normalized = str(value).strip()
    return normalized or None


def _response_bool(
    response: Mapping[str, Any],
    *keys: str,
) -> bool:
    for key in keys:
        if key not in response or response[key] is None:
            continue
        return str(response[key]).strip().casefold() in {
            "y",
            "yes",
            "true",
            "1",
        }
    return False
