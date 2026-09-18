"""Versioned JSON contracts for durable execution jobs."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from app.application.operations import (
    BusinessRuleOperationInput,
    CubeRefreshOperationInput,
    DataImportOperationInput,
    DataIntegrationOperationInput,
    DataMapOperationInput,
    MetadataImportOperationInput,
    OperationInput,
    OperationKind,
    PipelineOperationInput,
)
from app.application.planning_process import PlanningProcessInput
from app.application.standalone_flow import (
    StandaloneFlowInput,
    StandaloneFlowStepInput,
)
from app.application.reports import ReportGenerationOperationInput
from app.application.substitution_variables import (
    SubstitutionVariableAction,
    SubstitutionVariableOperationInput,
)
from app.application.user_variables import UserVariableOperationInput
from app.models.access_control import ExecutionActor, TriggerSource
from app.models.substitution_variable import RequestedSubstitutionVariableUpdate
from app.utils.exceptions import ExecutionQueueError


PAYLOAD_VERSION = 1


def operation_payload(
    operation_input: OperationInput,
    actor: ExecutionActor | None,
) -> dict[str, Any]:
    """Serialize one approved standalone operation without credentials."""
    kind, values = _operation_values(operation_input)
    return {
        "version": PAYLOAD_VERSION,
        "operation_kind": kind.value,
        "input": values,
        "actor": _actor_values(actor),
    }


def process_payload(
    process_input: PlanningProcessInput,
    actor: ExecutionActor | None,
) -> dict[str, Any]:
    """Serialize one approved Planning process without credentials."""
    return {
        "version": PAYLOAD_VERSION,
        "input": {
            "process_code": process_input.process_code,
            "year": process_input.year,
            "start_period": process_input.start_period,
            "end_period": process_input.end_period,
            "scenario": process_input.scenario,
            "version": process_input.version,
            "run_data_map": process_input.run_data_map,
            "clear_target": process_input.clear_target,
            "run_refresh": process_input.run_refresh,
            "run_report": process_input.run_report,
            "pipeline_variables": dict(process_input.pipeline_variables),
            "pipeline_uploads": {
                key: str(path)
                for key, path in process_input.pipeline_uploads.items()
            },
            "pipeline_inbox_files": dict(
                process_input.pipeline_inbox_files
            ),
            "variable_updates": [
                {
                    "scope": item.scope,
                    "name": item.name,
                    "expected_current_value": item.expected_current_value,
                    "new_value": item.new_value,
                }
                for item in process_input.variable_updates
            ],
        },
        "actor": _actor_values(actor),
    }


def standalone_flow_payload(
    flow_input: StandaloneFlowInput,
    actor: ExecutionActor | None,
) -> dict[str, Any]:
    """Serialize a fully resolved standalone flow without credentials."""
    return {
        "version": PAYLOAD_VERSION,
        "flow_name": flow_input.name,
        "objective": flow_input.objective,
        "flow_steps": [
            {
                "operation_code": step.operation_code,
                "display_name": step.display_name,
                "artifact_name": step.artifact_name,
                "source_sequence": step.source_sequence,
                "operation": operation_payload(step.operation_input, None),
            }
            for step in flow_input.steps
        ],
        "recovery_source_execution_id": (
            flow_input.recovery_source_execution_id
        ),
        "recovery_from_sequence": flow_input.recovery_from_sequence,
        "actor": _actor_values(actor),
    }


def operation_from_payload(payload: dict[str, Any]) -> tuple[OperationInput, ExecutionActor | None]:
    _require_version(payload)
    try:
        kind = OperationKind(str(payload["operation_kind"]))
        values = _mapping(payload["input"], "operation input")
    except (KeyError, ValueError) as exc:
        raise ExecutionQueueError("Durable operation payload is invalid.") from exc

    if kind is OperationKind.BUSINESS_RULE:
        result: OperationInput = BusinessRuleOperationInput(
            rule_name=_text(values, "rule_name"),
            runtime_prompts=_string_map(values.get("runtime_prompts")),
        )
    elif kind is OperationKind.DATA_MAP:
        result = DataMapOperationInput(
            data_map_name=_text(values, "data_map_name"),
            clear_target=bool(values.get("clear_target", False)),
            member_overrides=_string_map(values.get("member_overrides")),
            exclusion_overrides=_string_map(
                values.get("exclusion_overrides")
            ),
        )
    elif kind is OperationKind.PIPELINE:
        result = PipelineOperationInput(
            pipeline_code=_text(values, "pipeline_code"),
            variables=_string_map(values.get("variables")),
            uploads=_path_map(values.get("uploads")),
            inbox_files=_string_map(values.get("inbox_files")),
        )
    elif kind is OperationKind.DATA_INTEGRATION:
        result = DataIntegrationOperationInput(
            integration_name=_text(values, "integration_name"),
            start_period=_text(values, "start_period"),
            end_period=_text(values, "end_period"),
            import_mode=_text(values, "import_mode"),
            export_mode=_text(values, "export_mode"),
            upload_path=_optional_path(values.get("upload_path")),
            upload_target=_optional_text(values.get("upload_target")),
            inbox_file=_optional_text(values.get("inbox_file")),
            use_configured_file=bool(values.get("use_configured_file", False)),
        )
    elif kind is OperationKind.METADATA_IMPORT:
        result = MetadataImportOperationInput(
            job_name=_text(values, "job_name"),
            upload_path=_optional_path(values.get("upload_path")),
            inbox_file=_optional_text(values.get("inbox_file")),
            use_configured_file=bool(values.get("use_configured_file", False)),
            error_file_name=_optional_text(values.get("error_file_name")),
            refresh_job_name=_optional_text(values.get("refresh_job_name")),
        )
    elif kind is OperationKind.DATA_IMPORT:
        result = DataImportOperationInput(
            job_name=_text(values, "job_name"),
            upload_path=_optional_path(values.get("upload_path")),
            inbox_file=_optional_text(values.get("inbox_file")),
            use_configured_file=bool(values.get("use_configured_file", False)),
            error_file_name=_optional_text(values.get("error_file_name")),
        )
    elif kind is OperationKind.SUBSTITUTION_VARIABLE:
        result = SubstitutionVariableOperationInput(
            action=SubstitutionVariableAction(_text(values, "action")),
            scope=_text(values, "scope"),
            name=_text(values, "name"),
            value=_text(values, "value", allow_empty=True),
            expected_current_value=_optional_text(
                values.get("expected_current_value"),
                allow_empty=True,
            ),
        )
    elif kind is OperationKind.USER_VARIABLE:
        result = UserVariableOperationInput(
            user_name=_text(values, "user_name"),
            name=_text(values, "name"),
            dimension=_text(values, "dimension"),
            member=_text(values, "member"),
            expected_current_member=_optional_text(
                values.get("expected_current_member"), allow_empty=True
            ),
        )
    elif kind is OperationKind.CUBE_REFRESH:
        result = CubeRefreshOperationInput(job_name=_text(values, "job_name"))
    elif kind is OperationKind.REPORT_GENERATION:
        result = ReportGenerationOperationInput(
            form_name=_text(values, "form_name"),
            title=_text(values, "title"),
            page_member_overrides=tuple(
                (_text_pair(item, 0), _text_pair(item, 1))
                for item in _list(values.get("page_member_overrides"))
            ),
        )
    else:  # pragma: no cover - exhaustive enum protection.
        raise ExecutionQueueError(
            f"Unsupported durable operation kind '{kind.value}'."
        )
    return result, _actor_from_values(payload.get("actor"))


def process_from_payload(payload: dict[str, Any]) -> tuple[PlanningProcessInput, ExecutionActor | None]:
    _require_version(payload)
    values = _mapping(payload.get("input"), "Planning process input")
    updates = tuple(
        RequestedSubstitutionVariableUpdate(
            scope=_text(item, "scope"),
            name=_text(item, "name"),
            expected_current_value=_text(
                item,
                "expected_current_value",
                allow_empty=True,
            ),
            new_value=_text(item, "new_value", allow_empty=True),
        )
        for raw in _list(values.get("variable_updates"))
        for item in [_mapping(raw, "substitution-variable update")]
    )
    return (
        PlanningProcessInput(
            process_code=_text(values, "process_code"),
            year=_text(values, "year", allow_empty=True),
            start_period=_text(values, "start_period", allow_empty=True),
            end_period=_text(values, "end_period", allow_empty=True),
            scenario=_optional_text(values.get("scenario")),
            version=_optional_text(values.get("version")),
            run_data_map=_optional_bool(values.get("run_data_map")),
            clear_target=bool(values.get("clear_target", False)),
            run_refresh=_optional_bool(values.get("run_refresh")),
            run_report=_optional_bool(values.get("run_report")),
            pipeline_variables=_string_map(values.get("pipeline_variables")),
            pipeline_uploads=_path_map(values.get("pipeline_uploads")),
            pipeline_inbox_files=_string_map(
                values.get("pipeline_inbox_files")
            ),
            variable_updates=updates,
        ),
        _actor_from_values(payload.get("actor")),
    )


def standalone_flow_from_payload(
    payload: dict[str, Any],
) -> tuple[StandaloneFlowInput, ExecutionActor | None]:
    """Deserialize a durable standalone-flow job."""
    _require_version(payload)
    steps: list[StandaloneFlowStepInput] = []
    for raw in _list(payload.get("flow_steps")):
        item = _mapping(raw, "standalone flow step")
        operation_payload_value = _mapping(
            item.get("operation"), "standalone flow operation"
        )
        operation_input, _unused_actor = operation_from_payload(
            operation_payload_value
        )
        steps.append(
            StandaloneFlowStepInput(
                operation_code=_text(item, "operation_code"),
                display_name=_text(item, "display_name"),
                artifact_name=_text(item, "artifact_name"),
                operation_input=operation_input,
                source_sequence=(
                    int(item["source_sequence"])
                    if item.get("source_sequence") is not None
                    else None
                ),
            )
        )
    if not steps:
        raise ExecutionQueueError(
            "Durable standalone flow contains no configured steps."
        )
    return (
        StandaloneFlowInput(
            name=_text(payload, "flow_name"),
            objective=_text(payload, "objective"),
            steps=tuple(steps),
            recovery_source_execution_id=_optional_text(
                payload.get("recovery_source_execution_id")
            ),
            recovery_from_sequence=(
                int(payload["recovery_from_sequence"])
                if payload.get("recovery_from_sequence") is not None
                else None
            ),
        ),
        _actor_from_values(payload.get("actor")),
    )


def upload_paths(payload: dict[str, Any]) -> tuple[Path, ...]:
    """Return local uploads referenced by a serialized job."""
    if "flow_steps" in payload:
        return tuple(
            path
            for raw in _list(payload.get("flow_steps"))
            for item in [_mapping(raw, "standalone flow step")]
            for operation in [
                _mapping(item.get("operation"), "standalone flow operation")
            ]
            for path in upload_paths(operation)
        )
    if payload.get("operation_kind") == OperationKind.PIPELINE.value:
        values = _mapping(payload.get("input"), "operation input")
        return tuple(_path_map(values.get("uploads")).values())
    if payload.get("operation_kind") in {
        OperationKind.DATA_INTEGRATION.value,
        OperationKind.METADATA_IMPORT.value,
        OperationKind.DATA_IMPORT.value,
    }:
        values = _mapping(payload.get("input"), "operation input")
        path = _optional_path(values.get("upload_path"))
        return (path,) if path else ()
    if "operation_kind" not in payload:
        values = _mapping(payload.get("input"), "Planning process input")
        return tuple(_path_map(values.get("pipeline_uploads")).values())
    return ()


def _operation_values(operation_input: OperationInput) -> tuple[OperationKind, dict[str, Any]]:
    if isinstance(operation_input, BusinessRuleOperationInput):
        return OperationKind.BUSINESS_RULE, {
            "rule_name": operation_input.rule_name,
            "runtime_prompts": dict(operation_input.runtime_prompts),
        }
    if isinstance(operation_input, DataMapOperationInput):
        return OperationKind.DATA_MAP, {
            "data_map_name": operation_input.data_map_name,
            "clear_target": operation_input.clear_target,
            "member_overrides": dict(operation_input.member_overrides),
            "exclusion_overrides": dict(operation_input.exclusion_overrides),
        }
    if isinstance(operation_input, PipelineOperationInput):
        return OperationKind.PIPELINE, {
            "pipeline_code": operation_input.pipeline_code,
            "variables": dict(operation_input.variables),
            "uploads": {
                key: str(path) for key, path in operation_input.uploads.items()
            },
            "inbox_files": dict(operation_input.inbox_files),
        }
    if isinstance(operation_input, DataIntegrationOperationInput):
        return OperationKind.DATA_INTEGRATION, {
            "integration_name": operation_input.integration_name,
            "start_period": operation_input.start_period,
            "end_period": operation_input.end_period,
            "import_mode": operation_input.import_mode,
            "export_mode": operation_input.export_mode,
            "upload_path": _path_text(operation_input.upload_path),
            "upload_target": operation_input.upload_target,
            "inbox_file": operation_input.inbox_file,
            "use_configured_file": operation_input.use_configured_file,
        }
    if isinstance(operation_input, MetadataImportOperationInput):
        return OperationKind.METADATA_IMPORT, {
            "job_name": operation_input.job_name,
            "upload_path": _path_text(operation_input.upload_path),
            "inbox_file": operation_input.inbox_file,
            "use_configured_file": operation_input.use_configured_file,
            "error_file_name": operation_input.error_file_name,
            "refresh_job_name": operation_input.refresh_job_name,
        }
    if isinstance(operation_input, DataImportOperationInput):
        return OperationKind.DATA_IMPORT, {
            "job_name": operation_input.job_name,
            "upload_path": _path_text(operation_input.upload_path),
            "inbox_file": operation_input.inbox_file,
            "use_configured_file": operation_input.use_configured_file,
            "error_file_name": operation_input.error_file_name,
        }
    if isinstance(operation_input, SubstitutionVariableOperationInput):
        return OperationKind.SUBSTITUTION_VARIABLE, {
            "action": operation_input.action.value,
            "scope": operation_input.scope,
            "name": operation_input.name,
            "value": operation_input.value,
            "expected_current_value": operation_input.expected_current_value,
        }
    if isinstance(operation_input, UserVariableOperationInput):
        return OperationKind.USER_VARIABLE, {
            "user_name": operation_input.user_name,
            "name": operation_input.name,
            "dimension": operation_input.dimension,
            "member": operation_input.member,
            "expected_current_member": operation_input.expected_current_member,
        }
    if isinstance(operation_input, CubeRefreshOperationInput):
        return OperationKind.CUBE_REFRESH, {"job_name": operation_input.job_name}
    if isinstance(operation_input, ReportGenerationOperationInput):
        return OperationKind.REPORT_GENERATION, {
            "form_name": operation_input.form_name,
            "title": operation_input.title,
            "page_member_overrides": [
                list(item) for item in operation_input.page_member_overrides
            ],
        }
    raise ExecutionQueueError(
        f"Unsupported durable operation input '{type(operation_input).__name__}'."
    )


def _actor_values(actor: ExecutionActor | None) -> dict[str, str] | None:
    if actor is None:
        return None
    return {
        "username": actor.username,
        "display_name": actor.display_name,
        "trigger_source": actor.trigger_source.value,
    }


def _actor_from_values(value: object) -> ExecutionActor | None:
    if value is None:
        return None
    item = _mapping(value, "execution actor")
    try:
        source = TriggerSource(_text(item, "trigger_source"))
    except ValueError as exc:
        raise ExecutionQueueError("Durable execution actor is invalid.") from exc
    return ExecutionActor(
        username=_text(item, "username"),
        display_name=_text(item, "display_name"),
        trigger_source=source,
    )


def _require_version(payload: dict[str, Any]) -> None:
    if payload.get("version") != PAYLOAD_VERSION:
        raise ExecutionQueueError(
            "Durable execution payload version is unsupported."
        )


def _mapping(value: object, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ExecutionQueueError(f"Durable {label} must be an object.")
    return dict(value)


def _list(value: object) -> list[Any]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise ExecutionQueueError("Durable execution list value is invalid.")
    return value


def _text(
    values: dict[str, Any],
    key: str,
    *,
    allow_empty: bool = False,
) -> str:
    value = str(values.get(key, "")).strip()
    if not value and not allow_empty:
        raise ExecutionQueueError(
            f"Durable execution field '{key}' is required."
        )
    return value


def _optional_text(value: object, *, allow_empty: bool = False) -> str | None:
    if value is None:
        return None
    normalized = str(value).strip()
    return normalized if normalized or allow_empty else None


def _string_map(value: object) -> dict[str, str]:
    if value is None:
        return {}
    item = _mapping(value, "mapping")
    return {str(key): str(item_value) for key, item_value in item.items()}


def _path_map(value: object) -> dict[str, Path]:
    return {key: Path(item) for key, item in _string_map(value).items()}


def _optional_path(value: object) -> Path | None:
    normalized = _optional_text(value)
    return Path(normalized) if normalized else None


def _path_text(value: Path | None) -> str | None:
    return str(value) if value is not None else None


def _optional_bool(value: object) -> bool | None:
    return value if isinstance(value, bool) else None


def _text_pair(value: object, index: int) -> str:
    if not isinstance(value, list) or len(value) != 2:
        raise ExecutionQueueError("Report member override is invalid.")
    return str(value[index]).strip()
