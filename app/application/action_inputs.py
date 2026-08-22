"""Shared structured-input contracts for governed platform actions."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from app.utils.exceptions import ConfigurationError


class ActionInputKind(StrEnum):
    """UI- and provider-neutral input control types."""

    TEXT = "text"
    BOOLEAN = "boolean"
    CHOICE = "choice"
    KEY_VALUE = "key_value"
    FILE_REFERENCE = "file_reference"


@dataclass(frozen=True, slots=True)
class ActionInputField:
    """One reusable field in a governed action contract."""

    key: str
    label: str
    kind: ActionInputKind
    required: bool
    description: str
    placeholder: str = ""
    options: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class ActionInputSchema:
    """Input contract shared by agent and non-agent product surfaces."""

    action_type: str
    target_code: str
    fields: tuple[ActionInputField, ...]


def action_input_schema(action_type: str, target_code: str) -> ActionInputSchema:
    """Return the stable input contract for one action target."""
    normalized_type = str(action_type).strip().casefold()
    normalized_code = str(target_code).strip().casefold()
    if normalized_type == "process":
        return ActionInputSchema(
            action_type="process",
            target_code=str(target_code).strip(),
            fields=(
                _text(
                    "planning_year",
                    "Planning year",
                    True,
                    "Enter the exact Planning year used by this run.",
                    "FY26",
                ),
                _text(
                    "start_period",
                    "Start period",
                    False,
                    "First period to process, such as Jan. Leave blank only when the Pipeline supplies its own default.",
                    "Jan",
                ),
                _text(
                    "end_period",
                    "End period",
                    False,
                    "Last period to process, such as Mar. Leave blank only when the Pipeline supplies its own default.",
                    "Mar",
                ),
            ),
        )
    fields = _OPERATION_FIELDS.get(normalized_code)
    if fields is None:
        raise ConfigurationError(
            f"No action input schema is registered for '{target_code}'."
        )
    return ActionInputSchema(
        action_type="operation",
        target_code=normalized_code,
        fields=fields,
    )


def normalize_action_inputs(
    schema: ActionInputSchema,
    values: Mapping[str, Any],
) -> dict[str, Any]:
    """Validate and normalize an incomplete user-editable input set."""
    if not isinstance(values, Mapping):
        raise ConfigurationError("Action inputs must be a JSON object.")
    fields = {item.key: item for item in schema.fields}
    unexpected = set(values) - set(fields)
    if unexpected:
        raise ConfigurationError(
            "Unsupported action inputs: " + ", ".join(sorted(unexpected)) + "."
        )
    normalized: dict[str, Any] = {}
    for key, raw_value in values.items():
        field = fields[key]
        if field.kind is ActionInputKind.BOOLEAN:
            if not isinstance(raw_value, bool):
                raise ConfigurationError(f"{field.label} must be true or false.")
            normalized[key] = raw_value
            continue
        if field.kind is ActionInputKind.KEY_VALUE:
            normalized[key] = _normalize_pairs(field, raw_value)
            continue
        value = str(raw_value or "").strip()
        if len(value) > 1_000 or any(ord(character) < 32 for character in value):
            raise ConfigurationError(f"{field.label} contains an invalid value.")
        if field.kind is ActionInputKind.CHOICE and value:
            match = next(
                (item for item in field.options if item.casefold() == value.casefold()),
                None,
            )
            if match is None:
                raise ConfigurationError(
                    f"{field.label} must be one of: {', '.join(field.options)}."
                )
            value = match
        normalized[key] = value
    encoded_size = len(str(normalized))
    if encoded_size > 25_000:
        raise ConfigurationError("Action inputs exceed the 25,000 character limit.")
    return normalized


def missing_required_inputs(
    schema: ActionInputSchema,
    values: Mapping[str, Any],
) -> tuple[ActionInputField, ...]:
    """Return required fields that do not yet have usable values."""
    missing = []
    for field in schema.fields:
        if not field.required or field.key in values and (
            field.kind is ActionInputKind.BOOLEAN
            or bool(values.get(field.key))
        ):
            continue
        missing.append(field)
    if (
        values.get("file_source") == "Existing Oracle Inbox file"
        and not values.get("inbox_file")
    ):
        inbox_field = next(
            (field for field in schema.fields if field.key == "inbox_file"),
            None,
        )
        if inbox_field is not None and inbox_field not in missing:
            missing.append(inbox_field)
    if schema.action_type == "process":
        for key, counterpart in (
            ("start_period", "end_period"),
            ("end_period", "start_period"),
        ):
            if values.get(key) and not values.get(counterpart):
                field = next(
                    item for item in schema.fields if item.key == counterpart
                )
                if field not in missing:
                    missing.append(field)
    return tuple(missing)


def action_input_schema_payload(
    schema: ActionInputSchema,
) -> tuple[dict[str, Any], ...]:
    """Serialize a contract consistently for persistence and browser APIs."""
    return tuple(
        {
            "key": field.key,
            "label": field.label,
            "kind": field.kind.value,
            "required": field.required,
            "description": field.description,
            "placeholder": field.placeholder,
            "options": list(field.options),
        }
        for field in schema.fields
    )


def _normalize_pairs(
    field: ActionInputField,
    raw_value: Any,
) -> dict[str, str]:
    if raw_value in (None, ""):
        return {}
    if not isinstance(raw_value, Mapping):
        raise ConfigurationError(
            f"{field.label} must contain name and value pairs."
        )
    if len(raw_value) > 50:
        raise ConfigurationError(f"{field.label} cannot exceed 50 entries.")
    result: dict[str, str] = {}
    seen: set[str] = set()
    for raw_name, raw_item in raw_value.items():
        name = str(raw_name).strip()
        value = str(raw_item).strip()
        if not name or not value:
            raise ConfigurationError(
                f"Every {field.label.lower()} entry requires a name and value."
            )
        if len(name) > 250 or len(value) > 1_000:
            raise ConfigurationError(f"{field.label} contains an oversized entry.")
        normalized_name = name.casefold()
        if normalized_name in seen:
            raise ConfigurationError(
                f"{field.label} contains duplicate name '{name}'."
            )
        seen.add(normalized_name)
        result[name] = value
    return result


def _text(
    key: str,
    label: str,
    required: bool,
    description: str,
    placeholder: str = "",
    *,
    kind: ActionInputKind = ActionInputKind.TEXT,
) -> ActionInputField:
    return ActionInputField(
        key=key,
        label=label,
        kind=kind,
        required=required,
        description=description,
        placeholder=placeholder,
    )


def _boolean(
    key: str,
    label: str,
    required: bool,
    description: str,
) -> ActionInputField:
    return ActionInputField(
        key=key,
        label=label,
        kind=ActionInputKind.BOOLEAN,
        required=required,
        description=description,
    )


def _choice(
    key: str,
    label: str,
    required: bool,
    description: str,
    options: tuple[str, ...],
) -> ActionInputField:
    return ActionInputField(
        key=key,
        label=label,
        kind=ActionInputKind.CHOICE,
        required=required,
        description=description,
        options=options,
    )


def _pairs(key: str, label: str, description: str) -> ActionInputField:
    return ActionInputField(
        key=key,
        label=label,
        kind=ActionInputKind.KEY_VALUE,
        required=False,
        description=description,
        placeholder="Name=Value, one per line",
    )


_FILE_SOURCE = (
    "Use file configured in Oracle",
    "Upload on governed screen",
    "Existing Oracle Inbox file",
)
_OPERATION_FIELDS: dict[str, tuple[ActionInputField, ...]] = {
    "report-generation": (
        _pairs("point_of_view", "Point of view", "Optional dimension and member overrides."),
    ),
    "cube-refresh": (),
    "substitution-variables": (
        _choice("action", "Variable action", False, "Update an existing definition or create a new one.", ("UPDATE", "CREATE")),
        _text("scope", "Variable scope", True, "ALL or an exact cube name.", "ALL"),
        _text("variable_name", "Variable name", False, "Exact Oracle substitution-variable name."),
        _text("new_value", "New value", True, "Value to review before an update or creation."),
        _text("expected_current_value", "Observed current value", False, "Concurrency check captured from the live Oracle catalog."),
        _boolean("create_if_missing", "Create if missing", False, "Legacy governed-screen compatibility flag."),
    ),
    "user-variables": (
        _text("user_name", "Oracle user", True, "Exact Oracle Planning user name."),
        _text("variable_name", "User variable", True, "Existing Planning user-variable definition."),
        _text("dimension", "Dimension", True, "Dimension owned by the selected definition."),
        _text("new_member", "New member", True, "Exact member assigned to this user."),
        _text("expected_current_member", "Observed current member", False, "Concurrency value captured from Oracle."),
    ),
    "data-import": (
        _choice("file_source", "File source", True, "Choose where the governed screen obtains the file.", _FILE_SOURCE),
        _text("inbox_file", "Oracle Inbox file", False, "Exact existing Inbox filename when that source is selected.", kind=ActionInputKind.FILE_REFERENCE),
        _text("upload_token", "Temporary upload token", False, "Session-owned reference for a reviewed local file."),
        _text("upload_name", "Uploaded filename", False, "Display name of the reviewed local file.", kind=ActionInputKind.FILE_REFERENCE),
        _text("error_file_name", "Error output filename", False, "Optional Oracle Inbox output for rejected rows.", kind=ActionInputKind.FILE_REFERENCE),
    ),
    "metadata-import": (
        _choice("file_source", "File source", True, "Choose where the governed screen obtains the file.", _FILE_SOURCE),
        _text("inbox_file", "Oracle Inbox file", False, "Exact existing Inbox filename when that source is selected.", kind=ActionInputKind.FILE_REFERENCE),
        _text("upload_token", "Temporary upload token", False, "Session-owned reference for a reviewed local file."),
        _text("upload_name", "Uploaded filename", False, "Display name of the reviewed local file.", kind=ActionInputKind.FILE_REFERENCE),
        _text("error_file_name", "Error output filename", False, "Optional Oracle Inbox output for rejected metadata records.", kind=ActionInputKind.FILE_REFERENCE),
        _boolean("refresh_after_import", "Refresh cube after import", True, "Whether to review a Cube Refresh after successful metadata import."),
        _text("refresh_job_name", "Cube Refresh job", False, "Exact saved Cube Refresh job selected for a successful import."),
    ),
    "pipelines": (
        _pairs("runtime_variables", "Pipeline variables", "Optional exact Oracle Pipeline variable names and values."),
        _pairs("inbox_files", "Oracle Inbox files", "Optional Pipeline input keys mapped to existing Inbox files."),
        _pairs("uploads", "Temporary uploads", "Session-owned upload tokens mapped to Pipeline input keys."),
        _pairs("upload_names", "Uploaded filenames", "Display names for reviewed local Pipeline uploads."),
        _pairs("configured_files", "Configured Oracle files", "Pipeline input keys using files configured in Oracle."),
    ),
    "data-integrations": (
        _text("start_period", "Start period", True, "Exact Data Integration start period.", "Jan-26"),
        _text("end_period", "End period", True, "Exact Data Integration end period.", "Jan-26"),
        _choice("import_mode", "Import mode", True, "Data Integration import mode.", ("Replace", "Append", "Map and Validate", "No Import")),
        _choice("export_mode", "Export mode", True, "Data Integration export mode.", ("Merge", "Replace", "Accumulate", "Subtract", "No Export", "Check")),
        _choice("file_source", "File source", True, "Choose where the governed screen obtains the file.", _FILE_SOURCE),
        _text("inbox_file", "Oracle Inbox file", False, "Exact existing Inbox filename when that source is selected.", kind=ActionInputKind.FILE_REFERENCE),
        _text("upload_token", "Temporary upload token", False, "Session-owned reference for a reviewed local file."),
        _text("upload_name", "Uploaded filename", False, "Display name of the reviewed local file.", kind=ActionInputKind.FILE_REFERENCE),
    ),
    "business-rules": (
        _pairs("runtime_prompts", "Runtime prompts", "Optional exact Calculation Manager RTP names and values."),
    ),
    "data-maps": (
        _boolean("clear_target", "Clear target", True, "Explicitly choose whether the target region is cleared first."),
        _pairs("member_overrides", "Member overrides", "Optional exact dimension and selection overrides."),
        _pairs("exclusion_overrides", "Exclusion overrides", "Optional dimension and exclusion selections."),
    ),
}
