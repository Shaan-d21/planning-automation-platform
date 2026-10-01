"""Allow-listed read-only capabilities available to the AI agent."""

from __future__ import annotations

import re
from dataclasses import asdict
from datetime import datetime
from pathlib import PurePath
from typing import Any
from urllib.parse import urlparse

from app.agent.models import AgentToolCall, AgentToolDefinition
from app.application.action_inputs import (
    action_input_schema,
    action_input_schema_payload,
    normalize_action_inputs,
)
from app.application.control_center import ControlCenterService
from app.application.automation_scheduling import AutomationScheduleApplicationService
from app.application.automation_schedule_targets import (
    AutomationScheduleCoordinator,
    PipelineScheduleTargetAdapter,
)
from app.application.data_review import (
    DataReviewAxisSelection,
    DataReviewSliceSelection,
    DataReviewWorkspaceService,
)
from app.application.reports import ReportCatalogItem, ReportWorkspaceService
from app.application.execution_evidence import agent_execution_evidence
from app.application.operations import OPERATION_DEFINITIONS, OperationCatalogService
from app.application.substitution_variables import (
    SubstitutionVariableAction,
    SubstitutionVariableApplicationService,
    SubstitutionVariableOperationInput,
)
from app.application.user_variables import (
    UserVariableApplicationService,
    UserVariableOperationInput,
)
from app.config.settings import Settings
from app.models.data_integration import (
    DataIntegrationFileReference,
    DataIntegrationPeriodRange,
)
from app.models.automation_schedule import (
    AutomationConcurrencyPolicy,
    AutomationInputPolicy,
    AutomationMisfirePolicy,
    AutomationScheduleFrequency,
    AutomationScheduleInput,
    AutomationTargetType,
)
from app.models.oracle_artifact import OracleArtifactType, OracleEnvironment
from app.services.business_rule_rtp_registry import BusinessRuleRTPRegistryService
from app.services.data_integration_service import DataIntegrationService
from app.services.data_map_service import DataMapService
from app.services.data_service import DataService
from app.services.metadata_service import MetadataService
from app.utils.exceptions import AgentCapabilityError, EPMError


CREATE_SUBSTITUTION_VARIABLE = "Create a new substitution variable"
PIPELINE_SCHEDULE_CREATE = "pipeline-schedule-create"
PIPELINE_SCHEDULE_PAUSE = "pipeline-schedule-pause"
PIPELINE_SCHEDULE_RESUME = "pipeline-schedule-resume"
PIPELINE_SCHEDULE_ACTIONS = frozenset(
    {PIPELINE_SCHEDULE_CREATE, PIPELINE_SCHEDULE_PAUSE, PIPELINE_SCHEDULE_RESUME}
)


class AgentCapabilityGateway:
    """Validate and execute a strict allow-list of read-only platform tools."""

    def __init__(
        self,
        settings: Settings,
        *,
        control_center: ControlCenterService,
        data_review: DataReviewWorkspaceService,
        report_workspace: ReportWorkspaceService | None = None,
        operation_catalog: OperationCatalogService | None = None,
        substitution_variables: SubstitutionVariableApplicationService | None = None,
        user_variables: UserVariableApplicationService | None = None,
        business_rule_rtps: BusinessRuleRTPRegistryService | None = None,
        schedule_service: AutomationScheduleApplicationService | None = None,
        schedule_coordinator: AutomationScheduleCoordinator | None = None,
    ) -> None:
        self._settings = settings
        self._control_center = control_center
        self._data_review = data_review
        self._report_workspace = report_workspace or ReportWorkspaceService(settings)
        self._operation_catalog = operation_catalog
        self._substitution_variables = substitution_variables or (
            SubstitutionVariableApplicationService(settings)
        )
        self._user_variables = user_variables or UserVariableApplicationService(settings)
        self._business_rule_rtps = business_rule_rtps or (
            BusinessRuleRTPRegistryService(settings)
        )
        self._schedule_service = schedule_service
        self._schedule_coordinator = schedule_coordinator
        self._schedule_environment_key = OracleEnvironment.from_settings(
            settings.epm_base_url,
            settings.application_name,
        ).key
        self._handlers = {
            "get_environment_summary": self._environment_summary,
            "list_platform_operations": self._platform_operations,
            "get_recent_execution_history": self._recent_history,
            "get_execution_evidence": self._execution_evidence,
            "list_planning_cubes": self._planning_cubes,
            "list_cube_dimensions": self._cube_dimensions,
            "search_dimension_members": self._dimension_members,
            "list_data_explorer_views": self._data_explorer_views,
            "list_variance_views": self._variance_views,
            "review_saved_data_view": self._review_saved_data_view,
            "review_saved_variance": self._review_saved_variance,
            "review_data_slice": self._review_data_slice,
            "compare_data_slices": self._compare_data_slices,
            "list_operation_artifacts": self._operation_artifacts,
            "plan_multi_step_request": self._plan_multi_step_request,
            "prepare_operation_action": self._prepare_operation_action,
            "prepare_standalone_flow_action": (
                self._prepare_standalone_flow_action
            ),
            "prepare_schedule_action": self._prepare_schedule_action,
        }

    _REQUIRED_INPUTS = {
        "report-generation": ("Saved Data Explorer view", "Point of view"),
        "cube-refresh": ("Saved Cube Refresh job",),
        "substitution-variables": ("Variable scope", "Variable name", "New value"),
        "user-variables": ("Oracle user", "User variable", "New member"),
        "data-import": ("Saved Import Data job", "Input file source"),
        "metadata-import": ("Saved Import Metadata job", "Metadata file source"),
        "pipelines": ("Oracle Pipeline", "Runtime variables", "Required stage files"),
        "data-integrations": (
            "Data Integration",
            "Start and end period",
            "Import and export modes",
            "Input file source",
        ),
        "business-rules": ("Deployed business rule", "Required runtime prompts"),
        "data-maps": ("Data Map", "Clear-target choice", "Optional member overrides"),
    }

    _STANDALONE_FLOW_OPERATIONS = frozenset(
        {
            "pipelines",
            "business-rules",
            "data-maps",
            "data-integrations",
            "data-import",
            "metadata-import",
            "cube-refresh",
            "substitution-variables",
            "user-variables",
        }
    )

    @staticmethod
    def definitions() -> tuple[AgentToolDefinition, ...]:
        empty = {"type": "object", "properties": {}, "additionalProperties": False}
        axis_item = {
            "type": "object",
            "properties": {
                "dimension": {"type": "string"},
                "members": {
                    "type": "array",
                    "items": {"type": "string"},
                    "minItems": 1,
                },
            },
            "required": ["dimension", "members"],
            "additionalProperties": False,
        }
        pov_item = {
            "type": "object",
            "properties": {
                "dimension": {"type": "string"},
                "member": {"type": "string"},
            },
            "required": ["dimension", "member"],
            "additionalProperties": False,
        }
        slice_schema = {
            "type": "object",
            "properties": {
                "cube": {"type": "string"},
                "pov": {"type": "array", "items": pov_item},
                "rows": {
                    "type": "array",
                    "items": axis_item,
                    "minItems": 1,
                },
                "columns": {
                    "type": "array",
                    "items": axis_item,
                    "minItems": 1,
                },
            },
            "required": ["cube", "pov", "rows", "columns"],
            "additionalProperties": False,
        }
        return (
            AgentToolDefinition(
                name="get_environment_summary",
                description="Return the configured Oracle Planning application and deployment type without credentials.",
                parameters_schema=empty,
            ),
            AgentToolDefinition(
                name="list_platform_operations",
                description="List automation capabilities available in this platform and whether they are read-only or can change Oracle.",
                parameters_schema=empty,
            ),
            AgentToolDefinition(
                name="get_recent_execution_history",
                description="Return sanitized status summaries for recent Planning process executions.",
                parameters_schema={
                    "type": "object",
                    "properties": {
                        "limit": {
                            "type": "integer",
                            "description": "Number of runs, from 1 to 10.",
                            "minimum": 1,
                            "maximum": 10,
                        }
                    },
                    "additionalProperties": False,
                },
            ),
            AgentToolDefinition(
                name="get_execution_evidence",
                description=(
                    "Inspect retained step-level evidence for one platform "
                    "execution, including failures and available Oracle load "
                    "statistics. Use an execution ID returned by recent "
                    "history, or select the latest or latest failed run."
                ),
                parameters_schema={
                    "type": "object",
                    "properties": {
                        "execution_id": {
                            "type": "string",
                            "description": "Exact retained platform execution ID.",
                        },
                        "selector": {
                            "type": "string",
                            "enum": ["latest", "latest_failed"],
                            "description": (
                                "Use only when the user refers to the latest "
                                "run rather than an execution ID."
                            ),
                        },
                    },
                    "additionalProperties": False,
                },
            ),
            AgentToolDefinition(
                name="list_planning_cubes",
                description="Discover Planning cubes visible to the configured Oracle identity. This is a live read-only Oracle request.",
                parameters_schema=empty,
            ),
            AgentToolDefinition(
                name="list_cube_dimensions",
                description=(
                    "List live dimensions for one exact Planning cube before "
                    "constructing a data slice. If Oracle reports that metadata "
                    "discovery is unavailable, ask the user for exact dimension "
                    "names instead of guessing."
                ),
                parameters_schema={
                    "type": "object",
                    "properties": {"cube": {"type": "string"}},
                    "required": ["cube"],
                    "additionalProperties": False,
                },
            ),
            AgentToolDefinition(
                name="search_dimension_members",
                description=(
                    "Search live Oracle members for one exact cube dimension. "
                    "Use a short query for large hierarchies and never invent a "
                    "member that Oracle did not return."
                ),
                parameters_schema={
                    "type": "object",
                    "properties": {
                        "cube": {"type": "string"},
                        "dimension": {"type": "string"},
                        "query": {"type": "string"},
                        "limit": {
                            "type": "integer",
                            "minimum": 1,
                            "maximum": 100,
                        },
                    },
                    "required": ["cube", "dimension"],
                    "additionalProperties": False,
                },
            ),
            AgentToolDefinition(
                name="list_data_explorer_views",
                description=(
                    "List reusable Data Explorer views saved in this platform. "
                    "A saved view contains an exact cube, POV, row, and column "
                    "layout, but no stored Oracle data values."
                ),
                parameters_schema=empty,
            ),
            AgentToolDefinition(
                name="review_saved_data_view",
                description=(
                    "Load current read-only Oracle data using one exact saved "
                    "Data Explorer view. Use the saved view name returned by "
                    "list_data_explorer_views; never reconstruct its layout."
                ),
                parameters_schema={
                    "type": "object",
                    "properties": {"name": {"type": "string"}},
                    "required": ["name"],
                    "additionalProperties": False,
                },
            ),
            AgentToolDefinition(
                name="list_variance_views",
                description=(
                    "List saved Data Explorer layouts that can be used as a "
                    "validated shape for a live variance comparison. The "
                    "layouts contain no stored financial values."
                ),
                parameters_schema={
                    "type": "object",
                    "properties": {
                        "comparison": {"type": "string"},
                        "period": {"type": "string"},
                        "year": {"type": "string"},
                        "threshold": {"type": "number", "minimum": 0},
                    },
                    "required": ["comparison", "period"],
                    "additionalProperties": False,
                },
            ),
            AgentToolDefinition(
                name="review_saved_variance",
                description=(
                    "Compare two live Oracle scenarios using one exact saved "
                    "Data Explorer layout. Scenario, Period, optional Year, "
                    "and explicitly supplied POV members may be replaced; the "
                    "saved row and column scope remains unchanged."
                ),
                parameters_schema={
                    "type": "object",
                    "properties": {
                        "name": {"type": "string"},
                        "comparison": {"type": "string"},
                        "period": {"type": "string"},
                        "year": {"type": "string"},
                        "threshold": {"type": "number", "minimum": 0},
                        "pov_overrides": {
                            "type": "object",
                            "description": (
                                "Optional exact dimension-to-member changes for "
                                "POV dimensions already present in the saved view."
                            ),
                            "additionalProperties": {"type": "string"},
                        },
                        "max_mismatches": {
                            "type": "integer",
                            "minimum": 1,
                            "maximum": 500,
                        },
                    },
                    "required": ["name", "comparison", "period"],
                    "additionalProperties": False,
                },
            ),
            AgentToolDefinition(
                name="review_data_slice",
                description=(
                    "Read a live ad-hoc Planning cube slice. Every cube "
                    "dimension must be placed once in POV, rows, or columns. "
                    "This capability never changes Oracle data."
                ),
                parameters_schema=slice_schema,
            ),
            AgentToolDefinition(
                name="compare_data_slices",
                description=(
                    "Read and reconcile two live Planning slices. Source and "
                    "target layouts must have the same row and column shape."
                ),
                parameters_schema={
                    "type": "object",
                    "properties": {
                        "source": slice_schema,
                        "target": slice_schema,
                        "tolerance": {
                            "type": "number",
                            "minimum": 0,
                        },
                        "max_mismatches": {
                            "type": "integer",
                            "minimum": 1,
                            "maximum": 500,
                        },
                    },
                    "required": ["source", "target"],
                    "additionalProperties": False,
                },
            ),
            AgentToolDefinition(
                name="list_operation_artifacts",
                description=(
                    "List current selectable Oracle artifacts for one standalone "
                    "operation. Use it before preparing an action when the user "
                    "did not provide an exact artifact name."
                ),
                parameters_schema={
                    "type": "object",
                    "properties": {
                        "operation_code": {
                            "type": "string",
                            "enum": [item.code for item in OPERATION_DEFINITIONS],
                        }
                    },
                    "required": ["operation_code"],
                    "additionalProperties": False,
                },
            ),
            AgentToolDefinition(
                name="plan_multi_step_request",
                description=(
                    "Resolve a request containing multiple Planning steps against "
                    "the registered Oracle Pipelines. This is read-only. It may "
                    "recommend one matching Pipeline, but never executes or chains "
                    "standalone operations."
                ),
                parameters_schema={
                    "type": "object",
                    "properties": {
                        "objective": {"type": "string"},
                        "requested_steps": {
                            "type": "array",
                            "items": {
                                "type": "string",
                                "enum": [item.code for item in OPERATION_DEFINITIONS],
                            },
                            "minItems": 2,
                        },
                        "prefer_standalone": {
                            "type": "boolean",
                            "description": (
                                "Prepare an ordered platform flow draft instead "
                                "of recommending an Oracle Pipeline."
                            ),
                        },
                    },
                    "required": ["objective", "requested_steps"],
                    "additionalProperties": False,
                },
            ),
            AgentToolDefinition(
                name="prepare_operation_action",
                description=(
                    "Prepare, but never execute, a governed standalone operation. "
                    "Use this when the user expresses an intent to perform one "
                    "available platform operation. Canonical risk and route data "
                    "are resolved by the platform."
                ),
                parameters_schema={
                    "type": "object",
                    "properties": {
                        "operation_code": {
                            "type": "string",
                            "enum": [item.code for item in OPERATION_DEFINITIONS],
                        },
                        "objective": {
                            "type": "string",
                            "description": "Concise description of what the user wants to accomplish.",
                        },
                        "artifact_name": {
                            "type": "string",
                            "description": "Exact Oracle artifact name when the user supplied one.",
                        },
                    },
                    "required": ["operation_code", "objective"],
                    "additionalProperties": False,
                },
            ),
            AgentToolDefinition(
                name="prepare_standalone_flow_action",
                description=(
                    "Prepare, but never execute without final approval, an "
                    "ordered sequence of supported standalone Oracle Planning "
                    "operations. Each step must resolve a live artifact and "
                    "its governed inputs before one flow approval is shown."
                ),
                parameters_schema={
                    "type": "object",
                    "properties": {
                        "objective": {
                            "type": "string",
                            "description": "Business outcome for the complete flow.",
                        },
                        "requested_steps": {
                            "type": "array",
                            "items": {
                                "type": "string",
                                "enum": sorted(
                                    AgentCapabilityGateway._STANDALONE_FLOW_OPERATIONS
                                ),
                            },
                            "minItems": 2,
                            "maxItems": 12,
                        },
                    },
                    "required": ["objective", "requested_steps"],
                    "additionalProperties": False,
                },
            ),
            AgentToolDefinition(
                name="prepare_schedule_action",
                description=(
                    "Prepare a governed Oracle Pipeline schedule change. Use "
                    "CREATE for a new unattended recurrence, PAUSE for an "
                    "enabled schedule, or RESUME for a paused schedule. The "
                    "platform performs live validation and requires explicit "
                    "approval before saving the change."
                ),
                parameters_schema={
                    "type": "object",
                    "properties": {
                        "action": {
                            "type": "string",
                            "enum": ["CREATE", "PAUSE", "RESUME"],
                        },
                        "objective": {
                            "type": "string",
                            "description": "Concise description of the requested scheduling change.",
                        },
                        "artifact_name": {
                            "type": "string",
                            "description": (
                                "Exact Pipeline code for CREATE, or exact "
                                "schedule identifier for PAUSE/RESUME when known."
                            ),
                        },
                    },
                    "required": ["action", "objective"],
                    "additionalProperties": False,
                },
            ),
        )

    def execute(self, call: AgentToolCall) -> dict[str, Any]:
        handler = self._handlers.get(call.name)
        if handler is None:
            raise AgentCapabilityError(
                f"Agent capability '{call.name}' is not allowed."
            )
        allowed_arguments = {
            "get_recent_execution_history": {"limit"},
            "get_execution_evidence": {"execution_id", "selector"},
            "list_cube_dimensions": {"cube"},
            "search_dimension_members": {
                "cube",
                "dimension",
                "query",
                "limit",
            },
            "review_saved_data_view": {"name"},
            "list_variance_views": {
                "comparison",
                "period",
                "year",
                "threshold",
            },
            "review_saved_variance": {
                "name",
                "comparison",
                "period",
                "year",
                "threshold",
                "pov_overrides",
                "max_mismatches",
            },
            "review_data_slice": {"cube", "pov", "rows", "columns"},
            "compare_data_slices": {
                "source",
                "target",
                "tolerance",
                "max_mismatches",
            },
            "list_operation_artifacts": {"operation_code"},
            "plan_multi_step_request": {
                "objective",
                "requested_steps",
                "prefer_standalone",
            },
            "prepare_operation_action": {
                "operation_code",
                "objective",
                "artifact_name",
                "input_values",
            },
            "prepare_standalone_flow_action": {
                "objective",
                "requested_steps",
                "configured_steps",
            },
            "prepare_schedule_action": {
                "action",
                "objective",
                "artifact_name",
                "input_values",
            },
        }
        unexpected = set(call.arguments) - allowed_arguments.get(call.name, set())
        if unexpected:
            raise AgentCapabilityError(
                f"Unexpected arguments for '{call.name}': {', '.join(sorted(unexpected))}."
            )
        return handler(call.arguments)

    def _prepare_standalone_flow_action(
        self,
        arguments: dict[str, Any],
    ) -> dict[str, Any]:
        """Validate one ordered platform-managed flow configuration."""
        objective = self._required_text(arguments, "objective", "Objective")
        raw_steps = arguments.get("requested_steps")
        if not isinstance(raw_steps, list):
            raise AgentCapabilityError(
                "Standalone flow steps must be supplied in execution order."
            )
        requested_steps = tuple(
            str(item).strip().casefold() for item in raw_steps if str(item).strip()
        )
        if not 2 <= len(requested_steps) <= 12:
            raise AgentCapabilityError(
                "A standalone flow requires between 2 and 12 steps."
            )
        unsupported = tuple(
            code
            for code in requested_steps
            if code not in self._STANDALONE_FLOW_OPERATIONS
        )
        if unsupported:
            raise AgentCapabilityError(
                "These operations cannot be executed inside a standalone flow: "
                + ", ".join(unsupported)
                + ". Use their governed screen or an Oracle Pipeline."
            )
        configured = arguments.get("configured_steps", [])
        if not isinstance(configured, list):
            raise AgentCapabilityError(
                "Configured standalone flow steps are invalid."
            )
        return {
            "standalone_flow": {
                "target_code": "standalone-flow",
                "display_name": "Standalone Planning Flow",
                "objective": objective,
                "requested_steps": list(requested_steps),
                "configured_steps": configured,
                "category": "Orchestration",
                "risk_level": "Elevated",
                "route": "/app/assistant",
                "status": (
                    "READY_FOR_APPROVAL"
                    if len(configured) == len(requested_steps)
                    else "REQUIRES_CONFIGURATION"
                ),
            }
        }

    def _plan_multi_step_request(
        self,
        arguments: dict[str, Any],
    ) -> dict[str, Any]:
        """Find one existing Oracle Pipeline for a multi-step objective.

        Pipeline inspection is deliberately read-only. If the platform cannot
        prove a clear match, the returned sequence is informational and cannot
        be executed as an improvised workflow.
        """
        objective = self._required_text(arguments, "objective", "Objective")
        raw_steps = arguments.get("requested_steps")
        if not isinstance(raw_steps, list):
            raise AgentCapabilityError("Requested steps must be a list.")
        definitions = {item.code: item for item in OPERATION_DEFINITIONS}
        requested_steps = tuple(
            str(item).strip().casefold()
            for item in raw_steps
            if str(item).strip()
        )
        if len(requested_steps) < 2 or any(
            item not in definitions for item in requested_steps
        ):
            raise AgentCapabilityError(
                "A multi-step plan requires at least two available operations."
            )
        steps = [
            {
                "sequence": index,
                "code": code,
                "display_name": definitions[code].display_name,
                "category": definitions[code].category,
            }
            for index, code in enumerate(requested_steps, start=1)
        ]
        if arguments.get("prefer_standalone") is True:
            return self._standalone_flow_draft(objective, steps)
        # A Pipeline explicitly requested alongside another operation is one
        # step in the desired sequence. It must not be mistaken for a single
        # Pipeline that implicitly covers every requested task.
        if "pipelines" in requested_steps:
            return self._standalone_flow_draft(objective, steps)
        if self._operation_catalog is None:
            return self._manual_multi_step_plan(objective, steps, ())

        catalog = self._operation_catalog.discover_registered()
        candidates: list[dict[str, Any]] = []
        inspection_errors: list[str] = []
        for pipeline in catalog.pipelines:
            try:
                preview = self._operation_catalog.preflight_pipeline(
                    pipeline.code
                )
            except Exception as exc:
                inspection_errors.append(
                    f"{pipeline.code}: {str(exc).strip()[:160]}"
                )
                continue
            score, coverage = self._pipeline_match_score(
                objective=objective,
                requested_steps=requested_steps,
                pipeline_code=preview.code,
                pipeline_name=preview.display_name,
                pipeline_description=str(
                    getattr(pipeline, "description", None) or ""
                ),
                stage_names=tuple(item.display_name for item in preview.stages),
            )
            candidates.append(
                {
                    "score": score,
                    "coverage": coverage,
                    "code": preview.code,
                    "display_name": preview.display_name,
                    "stages": [asdict(item) for item in preview.stages],
                    "variables": [asdict(item) for item in preview.variables],
                    "file_requirements": [
                        asdict(item) for item in preview.file_requirements
                    ],
                }
            )
        candidates.sort(key=lambda item: (-float(item["score"]), item["code"]))
        best = candidates[0] if candidates else None
        runner_up = float(candidates[1]["score"]) if len(candidates) > 1 else 0.0
        clear_match = bool(
            best
            and float(best["score"]) >= 0.48
            and (len(candidates) == 1 or float(best["score"]) - runner_up >= 0.08)
        )
        if not clear_match:
            return self._manual_multi_step_plan(
                objective,
                steps,
                tuple(inspection_errors),
                candidate_count=len(candidates),
            )
        assert best is not None
        return {
            "objective": objective,
            "requested_steps": steps,
            "resolution": "ORACLE_PIPELINE",
            "executable": True,
            "pipeline": {
                key: value
                for key, value in best.items()
                if key != "score"
            },
            "confidence": round(float(best["score"]), 3),
            "candidate_count": len(candidates),
            "message": (
                "A clear registered Oracle Pipeline match was found. Its live "
                "stages and inputs must be reviewed before the single governed "
                "execution approval."
            ),
        }

    @staticmethod
    def _standalone_flow_draft(
        objective: str,
        steps: list[dict[str, Any]],
    ) -> dict[str, Any]:
        return {
            "objective": objective,
            "requested_steps": steps,
            "resolution": "STANDALONE_FLOW_DRAFT",
            "executable": False,
            "pipeline": None,
            "confidence": 1.0,
            "candidate_count": 0,
            "message": (
                "A standalone platform flow draft has been prepared in the "
                "requested order. No Oracle operation has started. Each step "
                "must resolve its exact Oracle artifact and required inputs "
                "before this flow can become executable."
            ),
        }

    @staticmethod
    def _manual_multi_step_plan(
        objective: str,
        steps: list[dict[str, Any]],
        inspection_errors: tuple[str, ...],
        *,
        candidate_count: int = 0,
    ) -> dict[str, Any]:
        return {
            "objective": objective,
            "requested_steps": steps,
            "resolution": "NON_EXECUTABLE_PLAN",
            "executable": False,
            "pipeline": None,
            "confidence": 0.0,
            "candidate_count": candidate_count,
            "inspection_errors": list(inspection_errors[:5]),
            "message": (
                "No unambiguous registered Oracle Pipeline covers this request. "
                "The sequence is shown for design review only; the platform will "
                "not guess or chain standalone operations."
            ),
        }

    @classmethod
    def _pipeline_match_score(
        cls,
        *,
        objective: str,
        requested_steps: tuple[str, ...],
        pipeline_code: str,
        pipeline_name: str,
        pipeline_description: str,
        stage_names: tuple[str, ...],
    ) -> tuple[float, float]:
        searchable = " ".join(
            (pipeline_code, pipeline_name, pipeline_description, *stage_names)
        )
        searchable_terms = cls._planning_terms(searchable)
        objective_terms = cls._planning_terms(objective)
        overlap = (
            len(searchable_terms & objective_terms) / len(objective_terms)
            if objective_terms
            else 0.0
        )
        covered = sum(
            bool(searchable_terms & cls._STEP_MATCH_TERMS.get(code, {code}))
            for code in requested_steps
        )
        coverage = covered / len(requested_steps)
        normalized_objective = " ".join(objective.casefold().split())
        exact = float(
            pipeline_code.casefold() in normalized_objective
            or pipeline_name.casefold() in normalized_objective
        )
        return min(1.0, 0.55 * coverage + 0.35 * overlap + 0.10 * exact), coverage

    _STEP_MATCH_TERMS = {
        "metadata-import": {"metadata", "hierarchy", "dimension", "member"},
        "data-import": {"data", "load", "import"},
        "data-integrations": {"integration", "data", "load"},
        "business-rules": {"rule", "calculation", "calculate", "calc", "forecast"},
        "data-maps": {"map", "push", "publish", "reporting", "smartpush"},
        "cube-refresh": {"refresh", "database", "cube"},
        "report-generation": {"report", "workbook", "export"},
        "substitution-variables": {"substitution", "variable"},
        "user-variables": {"user", "variable"},
    }

    @staticmethod
    def _planning_terms(value: str) -> set[str]:
        expanded = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", " ", str(value or ""))
        terms = set(re.findall(r"[a-z0-9]+", expanded.casefold()))
        return terms - {
            "a", "an", "and", "for", "from", "in", "of", "on", "or",
            "the", "then", "this", "to", "with", "run", "execute", "process",
            "planning", "oracle", "epm",
        }

    def _environment_summary(self, _: dict[str, Any]) -> dict[str, Any]:
        host = urlparse(self._settings.epm_base_url).hostname or "Oracle EPM"
        return {
            "application": self._settings.application_name,
            "environment_host": host,
            "deployment_mode": self._settings.resolved_deployment_mode,
            "access": "read-only agent capabilities",
        }

    @staticmethod
    def _platform_operations(_: dict[str, Any]) -> dict[str, Any]:
        items = [
            {
                "code": item.code,
                "name": item.display_name,
                "category": item.category,
                "risk_level": item.risk_level,
                "description": item.description,
            }
            for item in OPERATION_DEFINITIONS
        ]
        return {"count": len(items), "operations": items}

    def _configured_processes(self, _: dict[str, Any]) -> dict[str, Any]:
        processes = self._control_center.snapshot(history_limit=1).processes
        items = [
            {
                "code": item.code,
                "name": item.name,
                "stages": list(item.steps),
                "context": item.context_label,
            }
            for item in processes
        ]
        return {"count": len(items), "processes": items}

    def _recent_history(self, arguments: dict[str, Any]) -> dict[str, Any]:
        try:
            limit = int(arguments.get("limit", 5))
        except (TypeError, ValueError) as exc:
            raise AgentCapabilityError("History limit must be an integer.") from exc
        limit = max(1, min(limit, 10))
        runs = self._control_center.snapshot(history_limit=limit).recent_runs
        items = [
            {
                "execution_id": item.execution_id,
                "workflow": item.workflow_name,
                "status": item.status,
                "started_at": item.started_at,
                "duration": item.duration,
                "completed_steps": item.completed_steps,
                "total_steps": item.total_steps,
                "trigger_source": item.trigger_source,
            }
            for item in runs
        ]
        return {"count": len(items), "runs": items}

    def _execution_evidence(
        self,
        arguments: dict[str, Any],
    ) -> dict[str, Any]:
        execution_id = str(arguments.get("execution_id") or "").strip()
        selector = str(arguments.get("selector") or "").strip().casefold()
        if execution_id and selector:
            raise AgentCapabilityError(
                "Provide an execution ID or a latest-run selector, not both."
            )
        if not execution_id and selector not in {"latest", "latest_failed"}:
            raise AgentCapabilityError(
                "Provide an execution ID or choose latest/latest_failed."
            )

        if execution_id:
            run = self._control_center.get_workflow_run(execution_id)
        else:
            runs = self._control_center.list_workflow_runs(limit=100)
            if selector == "latest_failed":
                run = next(
                    (item for item in runs if item.status.value == "FAILED"),
                    None,
                )
            else:
                run = runs[0] if runs else None
        if run is None:
            description = (
                f"Execution '{execution_id}'"
                if execution_id
                else "A matching retained execution"
            )
            raise AgentCapabilityError(f"{description} was not found.")
        return {"execution": agent_execution_evidence(run)}

    def _planning_cubes(self, _: dict[str, Any]) -> dict[str, Any]:
        cubes = [asdict(item) for item in self._data_review.list_cubes()]
        return {"count": len(cubes), "cubes": cubes}

    def _cube_dimensions(self, arguments: dict[str, Any]) -> dict[str, Any]:
        cube = self._required_text(arguments, "cube", "Planning cube")
        dimensions = [
            asdict(item) for item in self._data_review.list_dimensions(cube)
        ]
        return {
            "cube": cube,
            "count": len(dimensions),
            "dimensions": dimensions,
        }

    def _dimension_members(self, arguments: dict[str, Any]) -> dict[str, Any]:
        cube = self._required_text(arguments, "cube", "Planning cube")
        dimension = self._required_text(
            arguments,
            "dimension",
            "Planning dimension",
        )
        query = str(arguments.get("query") or "").strip()
        try:
            limit = int(arguments.get("limit", 40))
        except (TypeError, ValueError) as exc:
            raise AgentCapabilityError(
                "Member search limit must be an integer."
            ) from exc
        result = self._data_review.search_members(
            cube,
            dimension,
            query=query,
            limit=limit,
        )
        return asdict(result)

    def _data_explorer_views(self, _arguments: dict[str, Any]) -> dict[str, Any]:
        """List only saved layouts that the unified Data Explorer can reopen."""
        views = self._compatible_data_explorer_views()
        maximum_views = 50
        returned = views[:maximum_views]
        return {
            "count": len(returned),
            "total_count": len(views),
            "truncated": len(returned) < len(views),
            "views": [
                {
                    "name": item.name,
                    "title": item.title,
                    "cube": item.cube,
                    "pov": [
                        {"dimension": dimension, "member": member}
                        for dimension, member in item.default_pov
                    ],
                    "row_dimensions": [
                        dimension for dimension, _members in item.rows
                    ],
                    "column_dimensions": [
                        dimension for dimension, _members in item.columns
                    ],
                }
                for item in returned
            ],
        }

    def _variance_views(self, arguments: dict[str, Any]) -> dict[str, Any]:
        """List reusable layouts while retaining the requested comparison."""
        comparison = self._variance_comparison(
            self._required_text(arguments, "comparison", "Variance comparison")
        )
        period = self._required_text(arguments, "period", "Variance period")
        year = str(arguments.get("year") or "").strip()
        threshold = self._variance_threshold(arguments.get("threshold", 0))
        required_dimensions = {"scenario", "period"}
        if year:
            required_dimensions.add("year")
        compatible = [
            view
            for view in self._compatible_data_explorer_views()
            if required_dimensions.issubset(
                {
                    *(dimension.casefold() for dimension, _member in view.default_pov),
                    *(dimension.casefold() for dimension, _members in view.rows),
                    *(dimension.casefold() for dimension, _members in view.columns),
                }
            )
        ]
        maximum_views = 50
        returned = compatible[:maximum_views]
        return {
            "count": len(returned),
            "total_count": len(compatible),
            "truncated": len(returned) < len(compatible),
            "views": [
                {
                    "name": item.name,
                    "title": item.title,
                    "cube": item.cube,
                    "pov": [
                        {"dimension": dimension, "member": member}
                        for dimension, member in item.default_pov
                    ],
                    "row_dimensions": [
                        dimension for dimension, _members in item.rows
                    ],
                    "column_dimensions": [
                        dimension for dimension, _members in item.columns
                    ],
                }
                for item in returned
            ],
            "purpose": "variance",
            "comparison": f"{comparison[0]} vs {comparison[1]}",
            "period": period,
            "year": year,
            "threshold": threshold,
        }

    def _review_saved_data_view(
        self,
        arguments: dict[str, Any],
    ) -> dict[str, Any]:
        """Resolve a saved layout server-side, then retrieve its live values."""
        name = self._required_text(arguments, "name", "Saved Data Explorer view")
        view, selection = self._saved_data_view_selection(name)
        review = self._data_review.load_slice(selection)
        return {
            **self._agent_grid(review, selection),
            "saved_view": {"name": view.name, "title": view.title},
        }

    def _review_saved_variance(
        self,
        arguments: dict[str, Any],
    ) -> dict[str, Any]:
        """Run a live scenario comparison from a server-owned saved layout."""
        name = self._required_text(arguments, "name", "Saved Data Explorer view")
        source_scenario, target_scenario = self._variance_comparison(
            self._required_text(arguments, "comparison", "Variance comparison")
        )
        period = self._required_text(arguments, "period", "Variance period")
        year = str(arguments.get("year") or "").strip()
        tolerance = self._variance_threshold(arguments.get("threshold", 0))
        pov_overrides = self._variance_pov_overrides(
            arguments.get("pov_overrides")
        )
        try:
            max_mismatches = int(arguments.get("max_mismatches", 100))
        except (TypeError, ValueError) as exc:
            raise AgentCapabilityError(
                "Maximum variance rows must be an integer."
            ) from exc
        if not 1 <= max_mismatches <= 500:
            raise AgentCapabilityError(
                "Maximum variance rows must be between 1 and 500."
            )
        view, base = self._saved_data_view_selection(name)
        base = self._apply_variance_pov_overrides(base, pov_overrides)
        source = self._variance_selection(
            base,
            scenario=source_scenario,
            period=period,
            year=year,
        )
        target = self._variance_selection(
            base,
            scenario=target_scenario,
            period=period,
            year=year,
        )
        comparison = self._data_review.compare_slices(
            source,
            target,
            tolerance=tolerance,
            max_mismatches=max_mismatches,
            include_cells=False,
        )
        return {
            **asdict(comparison),
            "source_request": self._selection_payload(source),
            "target_request": self._selection_payload(target),
            "saved_view": {"name": view.name, "title": view.title},
            "variance_context": {
                "comparison": f"{source_scenario} vs {target_scenario}",
                "period": period,
                "year": year,
                "threshold": tolerance,
                "pov_overrides": pov_overrides,
            },
        }

    @staticmethod
    def _variance_pov_overrides(value: Any) -> dict[str, str]:
        if value is None:
            return {}
        if not isinstance(value, dict):
            raise AgentCapabilityError("Variance POV overrides must be an object.")
        if len(value) > 30:
            raise AgentCapabilityError(
                "At most 30 variance POV values can be changed."
            )
        protected = {"scenario", "period", "year"}
        normalized: dict[str, str] = {}
        for raw_dimension, raw_member in value.items():
            dimension = str(raw_dimension or "").strip()
            member = str(raw_member or "").strip()
            if not dimension or not member:
                raise AgentCapabilityError(
                    "Every variance POV override needs a dimension and member."
                )
            if dimension.casefold() in protected:
                raise AgentCapabilityError(
                    f"Change {dimension} using the comparison, period, or year "
                    "controls rather than a POV override."
                )
            normalized[dimension] = member
        return normalized

    @staticmethod
    def _apply_variance_pov_overrides(
        selection: DataReviewSliceSelection,
        overrides: dict[str, str],
    ) -> DataReviewSliceSelection:
        if not overrides:
            return selection
        pov = dict(selection.pov)
        indexed = {dimension.casefold(): dimension for dimension in pov}
        for requested_dimension, member in overrides.items():
            existing = indexed.get(requested_dimension.casefold())
            if existing is None:
                raise AgentCapabilityError(
                    "The selected saved view does not contain "
                    f"{requested_dimension} in its POV."
                )
            pov[existing] = member
        return DataReviewSliceSelection(
            cube=selection.cube,
            pov=pov,
            rows=selection.rows,
            columns=selection.columns,
        )

    @staticmethod
    def _variance_comparison(value: str) -> tuple[str, str]:
        match = re.fullmatch(
            r"\s*(Actual|Forecast|Budget)\s+(?:vs\.?|versus)\s+"
            r"(Actual|Forecast|Budget)\s*",
            value,
            re.IGNORECASE,
        )
        if match is None or match.group(1).casefold() == match.group(2).casefold():
            raise AgentCapabilityError(
                "Choose two different scenarios, such as Actual vs Budget "
                "or Actual vs Forecast."
            )
        return match.group(1).title(), match.group(2).title()

    @staticmethod
    def _variance_threshold(value: Any) -> float:
        try:
            threshold = float(value or 0)
        except (TypeError, ValueError) as exc:
            raise AgentCapabilityError("Variance threshold must be numeric.") from exc
        if threshold < 0:
            raise AgentCapabilityError("Variance threshold cannot be negative.")
        return threshold

    @classmethod
    def _variance_selection(
        cls,
        selection: DataReviewSliceSelection,
        *,
        scenario: str,
        period: str,
        year: str,
    ) -> DataReviewSliceSelection:
        updated = cls._replace_slice_dimension(
            selection,
            "Scenario",
            (scenario,),
            required=True,
        )
        updated = cls._replace_slice_dimension(
            updated,
            "Period",
            (period,),
            required=True,
        )
        if year:
            updated = cls._replace_slice_dimension(
                updated,
                "Year",
                (year,),
                required=False,
            )
        return updated

    @staticmethod
    def _replace_slice_dimension(
        selection: DataReviewSliceSelection,
        dimension: str,
        members: tuple[str, ...],
        *,
        required: bool,
    ) -> DataReviewSliceSelection:
        key = dimension.casefold()
        found = False
        pov = dict(selection.pov)
        for current in tuple(pov):
            if current.casefold() == key:
                pov[current] = members[0]
                found = True
        rows: list[DataReviewAxisSelection] = []
        for item in selection.rows:
            if item.dimension.casefold() == key:
                rows.append(DataReviewAxisSelection(item.dimension, members))
                found = True
            else:
                rows.append(item)
        columns: list[DataReviewAxisSelection] = []
        for item in selection.columns:
            if item.dimension.casefold() == key:
                columns.append(DataReviewAxisSelection(item.dimension, members))
                found = True
            else:
                columns.append(item)
        if required and not found:
            raise AgentCapabilityError(
                f"Saved view '{selection.cube}' does not contain the "
                f"{dimension} dimension. Choose a compatible saved view."
            )
        return DataReviewSliceSelection(
            cube=selection.cube,
            pov=pov,
            rows=tuple(rows),
            columns=tuple(columns),
        )

    def saved_data_view_selection_payload(self, name: str) -> dict[str, Any]:
        """Return a saved view's validated slice for safe conversation resume."""
        _view, selection = self._saved_data_view_selection(name)
        return self._selection_payload(selection)

    def _saved_data_view_selection(
        self,
        name: str,
    ) -> tuple[ReportCatalogItem, DataReviewSliceSelection]:
        normalized = str(name or "").strip()
        view = next(
            (
                item
                for item in self._compatible_data_explorer_views()
                if item.name.casefold() == normalized.casefold()
            ),
            None,
        )
        if view is None:
            raise AgentCapabilityError(
                f"Saved Data Explorer view '{normalized}' was not found. "
                "List the current saved views and choose its exact name."
            )
        return view, DataReviewSliceSelection(
            cube=view.cube,
            pov=dict(view.default_pov),
            rows=tuple(
                DataReviewAxisSelection(dimension, members)
                for dimension, members in view.rows
            ),
            columns=tuple(
                DataReviewAxisSelection(dimension, members)
                for dimension, members in view.columns
            ),
        )

    def _compatible_data_explorer_views(self) -> tuple[ReportCatalogItem, ...]:
        return tuple(
            item
            for item in self._report_workspace.catalog()
            if item.rows and item.columns
        )

    def _review_data_slice(self, arguments: dict[str, Any]) -> dict[str, Any]:
        selection = self._data_review_selection(arguments)
        review = self._data_review.load_slice(selection)
        return self._agent_grid(review, selection)

    def _compare_data_slices(self, arguments: dict[str, Any]) -> dict[str, Any]:
        source = arguments.get("source")
        target = arguments.get("target")
        if not isinstance(source, dict) or not isinstance(target, dict):
            raise AgentCapabilityError(
                "Source and target Planning slices are required."
            )
        try:
            tolerance = float(arguments.get("tolerance", 0))
            max_mismatches = int(arguments.get("max_mismatches", 100))
        except (TypeError, ValueError) as exc:
            raise AgentCapabilityError(
                "Tolerance and maximum mismatches must be numeric."
            ) from exc
        if tolerance < 0:
            raise AgentCapabilityError("Comparison tolerance cannot be negative.")
        if not 1 <= max_mismatches <= 500:
            raise AgentCapabilityError(
                "Maximum mismatches must be between 1 and 500."
            )
        source_selection = self._data_review_selection(source)
        target_selection = self._data_review_selection(target)
        comparison = self._data_review.compare_slices(
            source_selection,
            target_selection,
            tolerance=tolerance,
            max_mismatches=max_mismatches,
            include_cells=False,
        )
        return {
            **asdict(comparison),
            "source_request": self._selection_payload(source_selection),
            "target_request": self._selection_payload(target_selection),
        }

    @classmethod
    def _data_review_selection(
        cls,
        payload: dict[str, Any],
    ) -> DataReviewSliceSelection:
        cube = cls._required_text(payload, "cube", "Planning cube")
        raw_pov = payload.get("pov", [])
        raw_rows = payload.get("rows")
        raw_columns = payload.get("columns")
        if not isinstance(raw_pov, list):
            raise AgentCapabilityError("POV must be a list of dimensions.")
        return DataReviewSliceSelection(
            cube=cube,
            pov=cls._pov_selection(raw_pov),
            rows=cls._axis_selection(raw_rows, "row"),
            columns=cls._axis_selection(raw_columns, "column"),
        )

    @classmethod
    def _pov_selection(cls, values: list[Any]) -> dict[str, str]:
        result: dict[str, str] = {}
        seen: set[str] = set()
        for item in values:
            if not isinstance(item, dict):
                raise AgentCapabilityError(
                    "Every POV selection requires a dimension and member."
                )
            dimension = cls._required_text(item, "dimension", "POV dimension")
            member = cls._required_text(item, "member", "POV member")
            key = dimension.casefold()
            if key in seen:
                raise AgentCapabilityError(
                    f"POV dimension '{dimension}' was supplied more than once."
                )
            seen.add(key)
            result[dimension] = member
        return result

    @classmethod
    def _axis_selection(
        cls,
        values: Any,
        label: str,
    ) -> tuple[DataReviewAxisSelection, ...]:
        if not isinstance(values, list) or not values:
            raise AgentCapabilityError(
                f"Add at least one {label} dimension."
            )
        result: list[DataReviewAxisSelection] = []
        seen: set[str] = set()
        for item in values:
            if not isinstance(item, dict):
                raise AgentCapabilityError(
                    f"Every {label} selection requires a dimension and members."
                )
            dimension = cls._required_text(
                item,
                "dimension",
                f"{label.title()} dimension",
            )
            raw_members = item.get("members")
            if not isinstance(raw_members, list):
                raise AgentCapabilityError(
                    f"Members for '{dimension}' must be a list."
                )
            members = tuple(
                normalized
                for value in raw_members
                for normalized in (str(value).strip(),)
                if normalized
            )
            if not members:
                raise AgentCapabilityError(
                    f"Select at least one member for '{dimension}'."
                )
            key = dimension.casefold()
            if key in seen:
                raise AgentCapabilityError(
                    f"{label.title()} dimension '{dimension}' was supplied more than once."
                )
            seen.add(key)
            result.append(DataReviewAxisSelection(dimension, members))
        return tuple(result)

    @staticmethod
    def _agent_grid(
        review: Any,
        selection: DataReviewSliceSelection,
    ) -> dict[str, Any]:
        """Return a bounded live grid so provider token limits remain stable."""
        maximum_cells = 2_000
        column_count = max(review.column_count, 1)
        maximum_rows = max(1, maximum_cells // column_count)
        returned_rows = review.grid.rows[:maximum_rows]
        return {
            "cube": review.cube,
            "name": review.form_name,
            "row_count": review.row_count,
            "column_count": review.column_count,
            "cell_count": review.cell_count,
            "missing_cell_count": review.missing_cell_count,
            "returned_row_count": len(returned_rows),
            "truncated": len(returned_rows) < review.row_count,
            "request": AgentCapabilityGateway._selection_payload(selection),
            "grid": {
                "pov": list(review.grid.pov),
                "row_dimensions": list(review.grid.row_dimensions),
                "column_dimensions": list(review.grid.column_dimensions),
                "columns": [list(item) for item in review.grid.columns],
                "rows": [asdict(item) for item in returned_rows],
            },
        }

    @staticmethod
    def _selection_payload(
        selection: DataReviewSliceSelection,
    ) -> dict[str, Any]:
        """Return the exact validated slice contract used by the web API."""
        return {
            "cube": selection.cube,
            "pov": dict(selection.pov),
            "rows": [
                {
                    "dimension": item.dimension,
                    "members": list(item.members),
                }
                for item in selection.rows
            ],
            "columns": [
                {
                    "dimension": item.dimension,
                    "members": list(item.members),
                }
                for item in selection.columns
            ],
        }

    def _operation_artifacts(self, arguments: dict[str, Any]) -> dict[str, Any]:
        operation_code = self._required_text(
            arguments, "operation_code", "Operation code"
        )
        artifact_catalog = self.artifact_catalog(operation_code)
        items = tuple(item[0] for item in artifact_catalog)
        display_names = dict(artifact_catalog)
        return {
            "operation_code": operation_code,
            "count": len(items),
            "artifacts": list(items),
            "artifact_details": [
                {
                    "identifier": item,
                    "display_name": display_names.get(item, item),
                }
                for item in items
            ],
        }

    def artifact_choices(self, operation_code: str) -> tuple[str, ...]:
        """Return canonical selectable names without changing Oracle state."""
        return tuple(
            identifier
            for identifier, _ in self.artifact_catalog(operation_code)
        )

    def artifact_catalog(
        self,
        operation_code: str,
    ) -> tuple[tuple[str, str], ...]:
        """Return identifiers and labels from one consistent catalog read."""
        normalized = operation_code.strip().casefold()
        if normalized == PIPELINE_SCHEDULE_CREATE:
            return self.artifact_catalog("pipelines")
        if normalized in {PIPELINE_SCHEDULE_PAUSE, PIPELINE_SCHEDULE_RESUME}:
            if self._schedule_service is None:
                return ()
            desired_enabled = normalized == PIPELINE_SCHEDULE_PAUSE
            return tuple(
                (
                    f"schedule:{item.schedule_id}",
                    f"{item.name} · {item.target_key} · "
                    f"{'Active' if item.enabled else 'Paused'}",
                )
                for item in self._schedule_service.list_schedules(
                    environment_key=self._schedule_environment_key
                )
                if item.target_type is AutomationTargetType.ORACLE_PIPELINE
                and item.enabled is desired_enabled
            )
        if normalized == "substitution-variables":
            catalog = self._substitution_variable_catalog()
            name_counts: dict[str, int] = {}
            for variable in catalog.variables:
                key = variable.name.casefold()
                name_counts[key] = name_counts.get(key, 0) + 1
            items = [
                (
                    variable.name
                    if name_counts[variable.name.casefold()] == 1
                    else f"{variable.scope}.{variable.name}",
                    (
                        f"{variable.name} · {variable.scope} · Current: "
                        f"{variable.value or 'Empty'}"
                    ),
                )
                for variable in catalog.variables
            ]
            items.append(
                (CREATE_SUBSTITUTION_VARIABLE, CREATE_SUBSTITUTION_VARIABLE)
            )
            return tuple(items)
        if normalized == "user-variables":
            catalog = self._user_variable_catalog(self._settings.epm_username)
            return tuple(
                (item.name, f"{item.name} · {item.dimension}")
                for item in catalog.definitions
            )
        if self._operation_catalog is None:
            return ()
        job_types = {
            "business-rules": "RULES",
            "data-maps": "PLAN_TYPE_MAP",
            "metadata-import": "IMPORT_METADATA",
            "data-import": "IMPORT_DATA",
            "cube-refresh": "CUBE_REFRESH",
        }
        job_type = job_types.get(normalized)
        if job_type is not None:
            artifact_types = {
                "RULES": OracleArtifactType.BUSINESS_RULE,
                "PLAN_TYPE_MAP": OracleArtifactType.DATA_MAP,
                "IMPORT_METADATA": OracleArtifactType.METADATA_IMPORT_JOB,
                "IMPORT_DATA": OracleArtifactType.DATA_IMPORT_JOB,
                "CUBE_REFRESH": OracleArtifactType.CUBE_REFRESH_JOB,
            }
            registered = getattr(
                self._operation_catalog,
                "registered_artifacts",
                None,
            )
            if callable(registered):
                registered_items = registered(artifact_types[job_type])
                cached = (
                    tuple(
                        item
                        for item in registered_items
                        if item.is_verified
                    )
                    if isinstance(registered_items, (list, tuple))
                    else ()
                )
                if cached:
                    return tuple(
                        (item.oracle_identifier, item.display_name)
                        for item in cached
                    )
            return tuple(
                (name, name)
                for name in self._operation_catalog.discover_job_names(
                    job_type=job_type
                )
            )
        catalog = self._operation_catalog.discover_registered()
        if normalized == "pipelines":
            return tuple(
                (
                    item.code,
                    str(item.name or item.code).strip(),
                )
                for item in catalog.pipelines
            )
        if normalized == "data-integrations":
            return tuple(
                (
                    item.name,
                    str(
                        getattr(item, "display_label", None)
                        or (
                            f"{item.name} - {item.description}"
                            if getattr(item, "description", None)
                            else None
                        )
                        or item.name
                    ).strip(),
                )
                for item in catalog.data_integrations
            )
        return ()

    def artifact_display_names(
        self,
        operation_code: str,
        choices: tuple[str, ...],
    ) -> dict[str, str]:
        """Return stable identifiers mapped to business-facing names."""
        by_code = {
            identifier.casefold(): display_name
            for identifier, display_name in self.artifact_catalog(operation_code)
        }
        return {
            item: by_code.get(item.casefold(), item)
            for item in choices
        }

    @staticmethod
    def artifact_recovery_definition(
        operation_code: str,
    ) -> dict[str, Any]:
        """Describe safe catalog recovery for incompletely discoverable types."""
        normalized = operation_code.strip().casefold()
        if normalized == PIPELINE_SCHEDULE_CREATE:
            normalized = "pipelines"
        if normalized == "pipelines":
            return {
                "enabled": True,
                "identifier_label": "Exact Oracle Pipeline code",
                "identifier_placeholder": "For example: PIPE01",
                "registration_mode": "verified",
                "help": (
                    "Oracle does not provide a reliable list-all Pipeline API. "
                    "An exact code is checked with a read-only Oracle request "
                    "before it is registered."
                ),
            }
        if normalized == "data-integrations":
            return {
                "enabled": True,
                "identifier_label": "Exact Data Integration name",
                "identifier_placeholder": "For example: Revenue_Load",
                "registration_mode": "pending",
                "help": (
                    "Oracle does not provide a safe standalone list-all lookup. "
                    "The exact name is registered as pending and verified by "
                    "its first explicitly approved run."
                ),
            }
        return {}

    def synchronize_artifact_catalog(self):
        """Refresh every Oracle artifact that can be discovered safely."""
        if self._operation_catalog is None:
            raise AgentCapabilityError("The Oracle artifact catalog is unavailable.")
        return self._operation_catalog.synchronize_artifacts()

    def register_artifact(
        self,
        operation_code: str,
        identifier: str,
    ) -> str:
        """Register one exact Pipeline or Data Integration identifier."""
        if self._operation_catalog is None:
            raise AgentCapabilityError("The Oracle artifact catalog is unavailable.")
        normalized = operation_code.strip().casefold()
        if normalized == PIPELINE_SCHEDULE_CREATE:
            normalized = "pipelines"
        if normalized == "pipelines":
            return self._operation_catalog.register_pipeline(identifier).code
        if normalized == "data-integrations":
            return self._operation_catalog.register_data_integration(
                identifier
            ).oracle_identifier
        raise AgentCapabilityError(
            "Catalog recovery is available only for Pipelines and Data "
            "Integrations."
        )

    def resolve_artifact_choice(
        self,
        operation_code: str,
        identifier: str,
    ) -> str | None:
        """Resolve a current verified choice or an explicitly pending Integration."""
        requested = str(identifier or "").strip()
        if not requested:
            return None
        for available in self.artifact_choices(operation_code):
            if available.casefold() == requested.casefold():
                return available
        if operation_code.strip().casefold() == PIPELINE_SCHEDULE_CREATE:
            return self.resolve_artifact_choice("pipelines", requested)
        if (
            operation_code.strip().casefold() == "data-integrations"
            and self._operation_catalog is not None
        ):
            try:
                artifact = self._operation_catalog.require_data_integration(
                    requested
                )
            except EPMError:
                return None
            return artifact.oracle_identifier
        return None

    def guided_input_definition(
        self,
        operation_code: str,
        artifact_name: str,
    ) -> dict[str, Any] | None:
        """Return platform-owned guided fields for a supported operation."""
        normalized = operation_code.strip().casefold()
        if normalized == PIPELINE_SCHEDULE_CREATE:
            if self._operation_catalog is None:
                raise AgentCapabilityError(
                    "The live Oracle Pipeline catalog is unavailable."
                )
            try:
                preview = self._operation_catalog.preflight_pipeline(
                    artifact_name
                )
            except EPMError as exc:
                raise AgentCapabilityError(str(exc)) from exc
            return {
                "operation_code": PIPELINE_SCHEDULE_CREATE,
                "display_name": "Oracle Pipeline schedule",
                "artifact_name": preview.code,
                "title": "Choose when this Pipeline should run",
                "description": (
                    "Set the recurrence and unattended inputs. Oracle remains "
                    "the owner of Pipeline stages, and the complete schedule "
                    "is validated again before approval."
                ),
                "fields": (
                    {
                        "key": "schedule",
                        "label": "Schedule configuration",
                        "kind": "schedule",
                        "required": True,
                        "description": "Recurrence and unattended Pipeline inputs.",
                        "placeholder": "",
                        "options": [],
                    },
                ),
                "context": {
                    "code": preview.code,
                    "display_name": preview.display_name,
                    "variables": [asdict(item) for item in preview.variables],
                    "file_requirements": [
                        asdict(item) for item in preview.file_requirements
                    ],
                    "stages": [asdict(item) for item in preview.stages],
                },
            }
        if normalized == "substitution-variables":
            catalog = self._substitution_variable_catalog()
            if artifact_name.casefold() == CREATE_SUBSTITUTION_VARIABLE.casefold():
                return {
                    "operation_code": "substitution-variables",
                    "display_name": "Substitution Variables",
                    "artifact_name": CREATE_SUBSTITUTION_VARIABLE,
                    "title": "Define the new substitution variable",
                    "description": (
                        "Choose its application or cube scope, then enter the "
                        "exact name and initial value. Nothing changes until "
                        "you approve the final review."
                    ),
                    "fields": (
                        {
                            "key": "scope",
                            "label": "Scope",
                            "kind": "choice",
                            "required": True,
                            "description": "ALL is application-wide; a cube limits the variable to that plan type.",
                            "placeholder": "",
                            "options": list(catalog.scopes),
                        },
                        {
                            "key": "variable_name",
                            "label": "Variable name",
                            "kind": "text",
                            "required": True,
                            "description": "Exact name used by Planning forms, rules, jobs, or Pipelines.",
                            "placeholder": "CurYr",
                            "options": [],
                        },
                        {
                            "key": "new_value",
                            "label": "Initial value",
                            "kind": "text",
                            "required": True,
                            "description": "Exact Planning member or text value.",
                            "placeholder": "FY27",
                            "options": [],
                        },
                    ),
                    "context": {
                        "action": "CREATE",
                        "scopes": list(catalog.scopes),
                    },
                }
            variable = self._resolve_substitution_variable(
                artifact_name,
                catalog.variables,
            )
            if variable is None:
                raise AgentCapabilityError(
                    "The selected substitution variable is no longer available."
                )
            return {
                "operation_code": "substitution-variables",
                "display_name": "Substitution Variables",
                "artifact_name": artifact_name,
                "title": "Enter the new substitution variable value",
                "description": (
                    "Review the live scope and current Oracle value before "
                    "entering the replacement value."
                ),
                "fields": (
                    {
                        "key": "new_value",
                        "label": "New value",
                        "kind": "text",
                        "required": True,
                        "description": "Exact Planning member or text value.",
                        "placeholder": variable.value,
                        "options": [],
                    },
                ),
                "context": {
                    "action": "UPDATE",
                    "scope": variable.scope,
                    "variable_name": variable.name,
                    "current_value": variable.value,
                },
            }
        if normalized == "user-variables":
            catalog = self._user_variable_catalog(self._settings.epm_username)
            definition = next(
                (
                    item
                    for item in catalog.definitions
                    if item.name.casefold() == artifact_name.casefold()
                ),
                None,
            )
            if definition is None:
                raise AgentCapabilityError(
                    "The selected user variable is no longer available."
                )
            return {
                "operation_code": "user-variables",
                "display_name": "User Variables",
                "artifact_name": definition.name,
                "title": "Choose the user's Planning context",
                "description": (
                    "Confirm the Oracle user and enter the exact member for "
                    f"the {definition.dimension} dimension. Nothing changes "
                    "until final approval."
                ),
                "fields": (
                    {
                        "key": "user_name",
                        "label": "Oracle user",
                        "kind": "text",
                        "required": True,
                        "description": "Your own Oracle user name unless you administer other users.",
                        "placeholder": "",
                        "options": [],
                    },
                    {
                        "key": "new_member",
                        "label": f"New {definition.dimension} member",
                        "kind": "text",
                        "required": True,
                        "description": "Exact Planning member name.",
                        "placeholder": "",
                        "options": [],
                    },
                ),
                "context": {
                    "variable_name": definition.name,
                    "dimension": definition.dimension,
                },
            }
        if normalized == "business-rules":
            definition = self._business_rule_rtps.get_definition(artifact_name)
            rtp_context = None
            if definition is not None:
                rtp_context = {
                    "rule_name": definition.rule_name,
                    "cube_name": definition.cube_name,
                    "source_name": definition.source_name,
                    "synchronized_at": definition.synchronized_at.isoformat(),
                    "prompts": [
                        {
                            "name": prompt.name,
                            "label": prompt.label,
                            "value_type": prompt.value_type,
                            "dimension": prompt.dimension,
                            "default_value": prompt.default_value,
                            "has_default": prompt.has_default,
                            "required": prompt.required_at_launch,
                            "hidden": prompt.hidden,
                            "allow_multiple": prompt.allow_multiple,
                            "scope_type": prompt.scope_type,
                            "scope_name": prompt.scope_name,
                            "limit_type": prompt.limit_type,
                            "limit_value": prompt.limit_value,
                        }
                        for prompt in definition.prompts
                    ],
                }
            return {
                "operation_code": "business-rules",
                "display_name": "Business Rules",
                "artifact_name": artifact_name,
                "title": "Choose how to supply runtime prompts",
                "description": (
                    "Review the synchronized Calc Manager runtime prompts, "
                    "then use Oracle defaults or provide explicit values."
                    if definition is not None
                    else "No synchronized RTP definition is available. Use "
                    "Calculation Manager defaults when they are configured, "
                    "or provide exact RTP names and values."
                ),
                "fields": (
                    {
                        "key": "runtime_prompt_mode",
                        "label": "Runtime prompt source",
                        "kind": "choice",
                        "required": True,
                        "description": (
                            "Use configured defaults or explicitly override them."
                        ),
                        "placeholder": "",
                        "options": [
                            "Use Calculation Manager defaults",
                            "Provide runtime prompt values",
                        ],
                    },
                    {
                        "key": "runtime_prompts",
                        "label": "Runtime prompts",
                        "kind": "key_value",
                        "required": False,
                        "description": (
                            "Exact Calculation Manager RTP names and values."
                        ),
                        "placeholder": "Name=Value, one per line",
                        "options": [],
                    },
                ),
                "context": {"rtp_definition": rtp_context},
            }
        if normalized == "data-maps":
            return {
                "operation_code": "data-maps",
                "display_name": "Data Maps",
                "artifact_name": artifact_name,
                "title": "Choose how the Data Map should run",
                "description": (
                    "Use the Data Map definition configured in Oracle, or "
                    "optionally narrow its source region with exact member "
                    "and exclusion overrides."
                ),
                "fields": (
                    {
                        "key": "clear_target",
                        "label": "Clear target before push",
                        "kind": "boolean",
                        "required": True,
                        "description": (
                            "Choose Yes only when the target slice should be "
                            "cleared before Oracle publishes data."
                        ),
                        "placeholder": "",
                        "options": [],
                    },
                    {
                        "key": "member_overrides",
                        "label": "Member overrides",
                        "kind": "key_value",
                        "required": False,
                        "description": (
                            "Optional exact dimension and member selections."
                        ),
                        "placeholder": "Dimension=Member selection",
                        "options": [],
                    },
                    {
                        "key": "exclusion_overrides",
                        "label": "Exclusion overrides",
                        "kind": "key_value",
                        "required": False,
                        "description": (
                            "Optional exact dimension and members to exclude."
                        ),
                        "placeholder": "Dimension=Excluded member selection",
                        "options": [],
                    },
                ),
            }
        if normalized == "pipelines":
            if self._operation_catalog is None:
                raise AgentCapabilityError(
                    "Oracle Pipeline inspection is not configured."
                )
            try:
                preview = self._operation_catalog.preflight_pipeline(
                    artifact_name
                )
            except EPMError as exc:
                raise AgentCapabilityError(str(exc)) from exc
            fields: list[dict[str, Any]] = [
                {
                    "key": "pipeline_review",
                    "label": "Live Oracle Pipeline",
                    "kind": "pipeline_review",
                    "required": True,
                    "description": (
                        "Review the stages and provide only the live inputs "
                        "reported by Oracle."
                    ),
                    "placeholder": "",
                    "options": [],
                }
            ]
            fields.extend(
                {
                    "key": f"variable:{variable.name}",
                    "label": variable.display_name,
                    "kind": "pipeline_variable",
                    "required": variable.required,
                    "description": variable.name,
                    "placeholder": "",
                    "options": [],
                    "name": variable.name,
                    "default_value": variable.default_value,
                    "editable": variable.editable,
                }
                for variable in preview.variables
            )
            fields.extend(
                {
                    "key": f"file:{requirement.key}",
                    "label": requirement.display_name,
                    "kind": "pipeline_file",
                    "required": requirement.required,
                    "description": "Pipeline file input",
                    "placeholder": "",
                    "options": [],
                    "file_key": requirement.key,
                    "configured_reference": requirement.configured_reference,
                    "allowed_extensions": list(
                        requirement.allowed_extensions
                    ),
                    "consumers": list(requirement.consumers),
                }
                for requirement in preview.file_requirements
            )
            return {
                "operation_code": "pipelines",
                "display_name": "Pipelines",
                "artifact_name": preview.code,
                "title": "Review the live Pipeline inputs",
                "description": (
                    "The stages, variables, defaults, and file requirements "
                    "below were read from Oracle. Supply only the values "
                    "needed for this run."
                ),
                "fields": tuple(fields),
                "context": {
                    "code": preview.code,
                    "display_name": preview.display_name,
                    "variables": [
                        asdict(variable) for variable in preview.variables
                    ],
                    "file_requirements": [
                        asdict(requirement)
                        for requirement in preview.file_requirements
                    ],
                    "stages": [asdict(stage) for stage in preview.stages],
                },
            }
        if normalized == "data-integrations":
            return {
                "operation_code": "data-integrations",
                "display_name": "Data Integrations",
                "artifact_name": artifact_name,
                "title": "Choose the Data Integration run inputs",
                "description": (
                    "Select the mapped period range, Oracle import/export "
                    "modes, and exactly one source-file option. The Integration "
                    "configuration and mappings remain owned by Oracle."
                ),
                "fields": (
                    {
                        "key": "start_period",
                        "label": "Start period",
                        "kind": "text",
                        "required": True,
                        "description": "First Oracle Data Integration period.",
                        "placeholder": "Jan-27",
                        "options": [],
                    },
                    {
                        "key": "end_period",
                        "label": "End period",
                        "kind": "text",
                        "required": True,
                        "description": "Last Oracle Data Integration period.",
                        "placeholder": "Mar-27",
                        "options": [],
                    },
                    {
                        "key": "import_mode",
                        "label": "Import mode",
                        "kind": "choice",
                        "required": True,
                        "description": "How Oracle imports source rows.",
                        "placeholder": "",
                        "options": [
                            "Replace",
                            "Append",
                            "Map and Validate",
                            "No Import",
                        ],
                    },
                    {
                        "key": "export_mode",
                        "label": "Export mode",
                        "kind": "choice",
                        "required": True,
                        "description": "How Oracle writes mapped data.",
                        "placeholder": "",
                        "options": [
                            "Merge",
                            "Replace",
                            "Accumulate",
                            "Subtract",
                            "No Export",
                            "Check",
                        ],
                    },
                    {
                        "key": "source_file",
                        "label": "Source file",
                        "kind": "file_reference",
                        "required": True,
                        "description": "Configured, Inbox, or local upload.",
                        "placeholder": "",
                        "options": [],
                    },
                ),
                "context": {
                    "import_modes": [
                        "Replace",
                        "Append",
                        "Map and Validate",
                        "No Import",
                    ],
                    "export_modes": [
                        "Merge",
                        "Replace",
                        "Accumulate",
                        "Subtract",
                        "No Export",
                        "Check",
                    ],
                    "allowed_extensions": [".csv", ".txt", ".zip", ".dat"],
                },
            }
        if normalized == "data-import":
            return {
                "operation_code": "data-import",
                "display_name": "Planning Data Import",
                "artifact_name": artifact_name,
                "title": "Choose the Planning data source",
                "description": (
                    "The saved Oracle Import Data job owns the cube, data "
                    "layout, mappings, and load options. Choose the file for "
                    "this run and optionally name Oracle's rejected-records "
                    "output."
                ),
                "fields": (
                    {
                        "key": "source_file",
                        "label": "Data source file",
                        "kind": "file_reference",
                        "required": True,
                        "description": "Configured, Inbox, or local upload.",
                        "placeholder": "",
                        "options": [],
                    },
                    {
                        "key": "error_file_name",
                        "label": "Error output filename",
                        "kind": "text",
                        "required": False,
                        "description": (
                            "Optional Oracle Inbox output for rejected rows."
                        ),
                        "placeholder": "DataImportErrors.log",
                        "options": [],
                    },
                ),
                "context": {
                    "allowed_extensions": [".csv", ".txt", ".zip"],
                },
            }
        if normalized == "metadata-import":
            refresh_jobs = [
                identifier
                for identifier, _display_name in self.artifact_catalog(
                    "cube-refresh"
                )
            ]
            return {
                "operation_code": "metadata-import",
                "display_name": "Metadata Import",
                "artifact_name": artifact_name,
                "title": "Choose the metadata file and post-import action",
                "description": (
                    "The saved Oracle Import Metadata job owns its dimensions, "
                    "member properties, delimiters, and validation behavior. "
                    "Choose the source file and whether a successful import "
                    "should run a saved Cube Refresh job."
                ),
                "fields": (
                    {
                        "key": "source_file",
                        "label": "Metadata source file",
                        "kind": "file_reference",
                        "required": True,
                        "description": "Configured, Inbox, or local upload.",
                        "placeholder": "",
                        "options": [],
                    },
                    {
                        "key": "error_file_name",
                        "label": "Error output filename",
                        "kind": "text",
                        "required": False,
                        "description": (
                            "Optional Oracle Inbox output for rejected records."
                        ),
                        "placeholder": "Metadata_Errors.csv",
                        "options": [],
                    },
                    {
                        "key": "refresh_after_import",
                        "label": "Refresh cube after import",
                        "kind": "boolean",
                        "required": True,
                        "description": (
                            "Run a saved Cube Refresh only after a successful import."
                        ),
                        "placeholder": "",
                        "options": [],
                    },
                ),
                "context": {
                    "allowed_extensions": [".csv", ".zip"],
                    "refresh_jobs": refresh_jobs,
                },
            }
        return None

    def normalize_business_rule_runtime_prompts(
        self,
        rule_name: str,
        values: dict[str, Any] | None,
    ) -> dict[str, str]:
        """Expose the shared registry guard to final agent execution."""
        supplied = values if isinstance(values, dict) else {}
        return self._business_rule_rtps.normalize_for_execution(
            rule_name,
            supplied,
        )

    def normalize_guided_inputs(
        self,
        operation_code: str,
        artifact_name: str,
        values: dict[str, Any] | None,
    ) -> dict[str, Any]:
        """Convert one guided response into governed draft input values."""
        normalized = operation_code.strip().casefold()
        if normalized not in {
            "substitution-variables",
            "user-variables",
            "business-rules",
            "data-maps",
            "pipelines",
            "data-integrations",
            "data-import",
            "metadata-import",
            PIPELINE_SCHEDULE_CREATE,
        }:
            return {}
        supplied = values if isinstance(values, dict) else {}
        if normalized == PIPELINE_SCHEDULE_CREATE:
            return self._normalize_pipeline_schedule_inputs(
                artifact_name,
                supplied,
            )
        if normalized == "substitution-variables":
            return self._normalize_substitution_variable_inputs(
                artifact_name,
                supplied,
            )
        if normalized == "user-variables":
            return self._normalize_user_variable_inputs(artifact_name, supplied)
        if normalized == "pipelines":
            return self._normalize_pipeline_inputs(artifact_name, supplied)
        if normalized == "data-integrations":
            return self._normalize_data_integration_inputs(supplied)
        if normalized == "data-import":
            return self._normalize_data_import_inputs(supplied)
        if normalized == "metadata-import":
            return self._normalize_metadata_import_inputs(supplied)
        if normalized == "data-maps":
            unexpected = set(supplied) - {
                "clear_target",
                "member_overrides",
                "exclusion_overrides",
            }
            if unexpected:
                raise AgentCapabilityError(
                    "Unsupported Data Map inputs: "
                    + ", ".join(sorted(unexpected))
                    + "."
                )
            if not isinstance(supplied.get("clear_target"), bool):
                raise AgentCapabilityError(
                    "Choose whether the Data Map should clear its target."
                )
            member_overrides = supplied.get("member_overrides") or {}
            exclusion_overrides = supplied.get("exclusion_overrides") or {}
            if not isinstance(member_overrides, dict) or not isinstance(
                exclusion_overrides, dict
            ):
                raise AgentCapabilityError(
                    "Data Map overrides must contain dimension and member "
                    "selection pairs."
                )
            try:
                request = DataMapService.build_request(
                    "guided-data-map",
                    clear_target=supplied["clear_target"],
                    member_overrides=member_overrides,
                    exclusion_overrides=exclusion_overrides,
                )
            except EPMError as exc:
                raise AgentCapabilityError(str(exc)) from exc
            return {
                "clear_target": request.clear_target,
                "member_overrides": dict(request.member_overrides),
                "exclusion_overrides": dict(request.exclusion_overrides),
            }
        unexpected = set(supplied) - {
            "runtime_prompt_mode",
            "runtime_prompts",
        }
        if unexpected:
            raise AgentCapabilityError(
                "Unsupported Business Rule inputs: "
                + ", ".join(sorted(unexpected))
                + "."
            )
        mode = str(supplied.get("runtime_prompt_mode") or "").strip()
        if mode == "Use Calculation Manager defaults":
            prompts = self._business_rule_rtps.normalize_for_execution(
                artifact_name,
                {},
            )
            return {"runtime_prompts": prompts}
        if mode != "Provide runtime prompt values":
            raise AgentCapabilityError("Choose a runtime prompt source.")
        raw_prompts = supplied.get("runtime_prompts")
        if not isinstance(raw_prompts, dict) or not raw_prompts:
            raise AgentCapabilityError(
                "Add at least one exact runtime prompt name and value."
            )
        prompts = self._business_rule_rtps.normalize_for_execution(
            artifact_name,
            raw_prompts,
        )
        return {"runtime_prompts": prompts}

    def _normalize_substitution_variable_inputs(
        self,
        artifact_name: str,
        supplied: dict[str, Any],
    ) -> dict[str, Any]:
        """Validate one live update or explicit creation request."""
        catalog = self._substitution_variable_catalog()
        creating = (
            artifact_name.casefold()
            == CREATE_SUBSTITUTION_VARIABLE.casefold()
        )
        allowed = (
            {"scope", "variable_name", "new_value"}
            if creating
            else {"new_value"}
        )
        unexpected = set(supplied) - allowed
        if unexpected:
            raise AgentCapabilityError(
                "Unsupported substitution-variable inputs: "
                + ", ".join(sorted(unexpected))
                + "."
            )
        if creating:
            operation_input = SubstitutionVariableOperationInput(
                action=SubstitutionVariableAction.CREATE,
                scope=str(supplied.get("scope") or "").strip(),
                name=str(supplied.get("variable_name") or ""),
                value=str(supplied.get("new_value") or ""),
            )
            # ALL is Oracle Planning's application-wide scope and remains
            # valid even when an older Planning release cannot expose plan
            # types. Cube-scoped creation still requires a live scope.
            valid_scopes = {
                "all",
                *(scope.strip().casefold() for scope in catalog.scopes),
            }
            if operation_input.scope.casefold() not in valid_scopes:
                raise AgentCapabilityError(
                    "Choose an application-wide or live cube scope."
                )
        else:
            variable = self._resolve_substitution_variable(
                artifact_name,
                catalog.variables,
            )
            if variable is None:
                raise AgentCapabilityError(
                    "The selected substitution variable is no longer available."
                )
            operation_input = SubstitutionVariableOperationInput(
                action=SubstitutionVariableAction.UPDATE,
                scope=variable.scope,
                name=variable.name,
                value=str(supplied.get("new_value") or ""),
                expected_current_value=variable.value,
            )
        try:
            normalized = SubstitutionVariableApplicationService.normalize_input(
                operation_input
            )
            self._substitution_variables.validate_value_compatibility(
                variable_name=normalized.name,
                current_value=(
                    None
                    if normalized.action is SubstitutionVariableAction.CREATE
                    else normalized.expected_current_value
                ),
                proposed_value=normalized.value,
                scope=normalized.scope,
            )
        except EPMError as exc:
            raise AgentCapabilityError(str(exc)) from exc
        return {
            "action": normalized.action.value,
            "scope": normalized.scope,
            "variable_name": normalized.name,
            "new_value": normalized.value,
            "expected_current_value": normalized.expected_current_value or "",
            "create_if_missing": normalized.action is SubstitutionVariableAction.CREATE,
        }

    def _substitution_variable_catalog(self):
        """Read the current Oracle variable definitions and valid scopes."""
        try:
            return self._substitution_variables.discover()
        except EPMError as exc:
            raise AgentCapabilityError(str(exc)) from exc

    def _user_variable_catalog(self, user_name: str):
        try:
            return self._user_variables.discover(user_name)
        except EPMError as exc:
            raise AgentCapabilityError(str(exc)) from exc

    def _normalize_user_variable_inputs(
        self,
        artifact_name: str,
        supplied: dict[str, Any],
    ) -> dict[str, Any]:
        unexpected = set(supplied) - {"user_name", "new_member"}
        if unexpected:
            raise AgentCapabilityError(
                "Unsupported user-variable inputs: "
                + ", ".join(sorted(unexpected))
                + "."
            )
        user_name = str(supplied.get("user_name") or "").strip()
        new_member = str(supplied.get("new_member") or "").strip()
        catalog = self._user_variable_catalog(user_name)
        definition = next(
            (
                item
                for item in catalog.definitions
                if item.name.casefold() == artifact_name.casefold()
            ),
            None,
        )
        if definition is None:
            raise AgentCapabilityError(
                "The selected user variable is no longer available."
            )
        current = next(
            (
                item
                for item in catalog.values
                if item.name.casefold() == definition.name.casefold()
            ),
            None,
        )
        from app.services.user_variable_service import UserVariableService

        try:
            UserVariableService.validate_value(
                user_name,
                definition.name,
                definition.dimension,
                new_member,
            )
            self._user_variables.validate_value_compatibility(
                variable_name=definition.name,
                dimension=definition.dimension,
                member=new_member,
            )
        except EPMError as exc:
            raise AgentCapabilityError(str(exc)) from exc
        return {
            "user_name": user_name,
            "variable_name": definition.name,
            "dimension": definition.dimension,
            "new_member": new_member,
            "expected_current_member": current.member if current is not None else None,
        }

    @staticmethod
    def _resolve_substitution_variable(identifier: str, variables):
        """Resolve the same stable identifier exposed by artifact_catalog."""
        requested = str(identifier or "").strip().casefold()
        if not requested:
            return None
        name_counts: dict[str, int] = {}
        for variable in variables:
            key = variable.name.casefold()
            name_counts[key] = name_counts.get(key, 0) + 1
        for variable in variables:
            canonical = (
                variable.name
                if name_counts[variable.name.casefold()] == 1
                else f"{variable.scope}.{variable.name}"
            )
            if canonical.casefold() == requested:
                return variable
        return None

    def _normalize_data_integration_inputs(
        self,
        supplied: dict[str, Any],
    ) -> dict[str, Any]:
        """Validate one mouse-first Data Integration run configuration."""
        unexpected = set(supplied) - {
            "start_period",
            "end_period",
            "import_mode",
            "export_mode",
            "file_choice",
        }
        if unexpected:
            raise AgentCapabilityError(
                "Unsupported Data Integration inputs: "
                + ", ".join(sorted(unexpected))
                + "."
            )
        try:
            period_range = DataIntegrationPeriodRange.from_period_names(
                str(supplied.get("start_period") or ""),
                str(supplied.get("end_period") or ""),
            )
            import_mode = DataIntegrationService.normalize_import_mode(
                str(supplied.get("import_mode") or "")
            )
            export_mode = DataIntegrationService.normalize_export_mode(
                str(supplied.get("export_mode") or "")
            )
        except EPMError as exc:
            raise AgentCapabilityError(str(exc)) from exc
        raw_choice = supplied.get("file_choice")
        if not isinstance(raw_choice, dict):
            raise AgentCapabilityError(
                "Choose one Data Integration source-file option."
            )
        source = str(raw_choice.get("source") or "").strip().casefold()
        result: dict[str, Any] = {
            "start_period": period_range.start_period,
            "end_period": period_range.end_period,
            "import_mode": import_mode,
            "export_mode": export_mode,
            "inbox_file": "",
            "upload_token": "",
            "upload_name": "",
        }
        if source == "configured":
            result["file_source"] = "Use file configured in Oracle"
            return result
        if source == "inbox":
            try:
                reference = DataIntegrationFileReference.from_existing(
                    str(raw_choice.get("inbox_reference") or "")
                )
            except EPMError as exc:
                raise AgentCapabilityError(str(exc)) from exc
            result["file_source"] = "Existing Oracle Inbox file"
            result["inbox_file"] = str(reference)
            return result
        if source == "upload":
            token = self._safe_pipeline_value(
                raw_choice.get("upload_token"),
                "Data Integration upload",
                maximum=200,
            )
            filename = self._safe_pipeline_value(
                raw_choice.get("filename"),
                "Data Integration filename",
                maximum=500,
            )
            if not token or not filename:
                raise AgentCapabilityError(
                    "Choose a local Data Integration source file."
                )
            if not filename.casefold().endswith(
                (".csv", ".txt", ".zip", ".dat")
            ):
                raise AgentCapabilityError(
                    "Data Integration uploads support .csv, .txt, .zip, or .dat."
                )
            result["file_source"] = "Upload on governed screen"
            result["upload_token"] = token
            result["upload_name"] = filename
            return result
        raise AgentCapabilityError(
            "Choose the configured file, an Oracle Inbox file, or a local upload."
        )

    def _normalize_data_import_inputs(
        self,
        supplied: dict[str, Any],
    ) -> dict[str, Any]:
        """Validate one mouse-first native Planning Data Import setup."""
        unexpected = set(supplied) - {"file_choice", "error_file_name"}
        if unexpected:
            raise AgentCapabilityError(
                "Unsupported Planning Data Import inputs: "
                + ", ".join(sorted(unexpected))
                + "."
            )
        raw_choice = supplied.get("file_choice")
        if not isinstance(raw_choice, dict):
            raise AgentCapabilityError(
                "Choose one Planning Data Import source-file option."
            )
        raw_error_file = str(supplied.get("error_file_name") or "").strip()
        if raw_error_file and (
            PurePath(raw_error_file).name != raw_error_file
            or "/" in raw_error_file
            or "\\" in raw_error_file
        ):
            raise AgentCapabilityError(
                "The error output must be a filename, not a folder path."
            )
        result: dict[str, Any] = {
            "inbox_file": "",
            "upload_token": "",
            "upload_name": "",
            "error_file_name": raw_error_file,
        }
        source = str(raw_choice.get("source") or "").strip().casefold()
        if source == "configured":
            result["file_source"] = "Use file configured in Oracle"
            return result
        if source == "inbox":
            raw_reference = str(
                raw_choice.get("inbox_reference") or ""
            ).strip()
            filename = PurePath(raw_reference.replace("\\", "/")).name
            try:
                DataService.validate_inputs(filename, "guided-data-import")
            except EPMError as exc:
                raise AgentCapabilityError(str(exc)) from exc
            result["file_source"] = "Existing Oracle Inbox file"
            result["inbox_file"] = filename
            return result
        if source == "upload":
            token = self._safe_pipeline_value(
                raw_choice.get("upload_token"),
                "Planning Data Import upload",
                maximum=200,
            )
            filename = self._safe_pipeline_value(
                raw_choice.get("filename"),
                "Planning Data Import filename",
                maximum=500,
            )
            if not token or not filename:
                raise AgentCapabilityError(
                    "Choose a local Planning data file."
                )
            try:
                DataService.validate_inputs(filename, "guided-data-import")
            except EPMError as exc:
                raise AgentCapabilityError(str(exc)) from exc
            result["file_source"] = "Upload on governed screen"
            result["upload_token"] = token
            result["upload_name"] = PurePath(filename).name
            return result
        raise AgentCapabilityError(
            "Choose the configured file, an Oracle Inbox file, or a local upload."
        )

    def _normalize_metadata_import_inputs(
        self,
        supplied: dict[str, Any],
    ) -> dict[str, Any]:
        """Validate one governed Planning Metadata Import setup."""
        unexpected = set(supplied) - {
            "file_choice",
            "error_file_name",
            "refresh_after_import",
            "refresh_job_name",
        }
        if unexpected:
            raise AgentCapabilityError(
                "Unsupported Metadata Import inputs: "
                + ", ".join(sorted(unexpected))
                + "."
            )
        if not isinstance(supplied.get("refresh_after_import"), bool):
            raise AgentCapabilityError(
                "Choose whether to refresh the cube after Metadata Import."
            )
        refresh_after_import = supplied["refresh_after_import"]
        refresh_job_name = str(
            supplied.get("refresh_job_name") or ""
        ).strip()
        if refresh_after_import:
            available_refresh_jobs = self.artifact_choices("cube-refresh")
            canonical_refresh = next(
                (
                    job
                    for job in available_refresh_jobs
                    if job.casefold() == refresh_job_name.casefold()
                ),
                None,
            )
            if canonical_refresh is not None:
                refresh_job_name = canonical_refresh
            elif available_refresh_jobs:
                raise AgentCapabilityError(
                    "Select a current saved Cube Refresh job."
                )
            else:
                refresh_job_name = self._safe_pipeline_value(
                    refresh_job_name,
                    "Cube Refresh job name",
                    maximum=128,
                )
                if not refresh_job_name:
                    raise AgentCapabilityError(
                        "Enter the exact saved Cube Refresh job name."
                    )
        else:
            refresh_job_name = ""
        raw_error_file = str(supplied.get("error_file_name") or "").strip()
        if raw_error_file and (
            PurePath(raw_error_file).name != raw_error_file
            or "/" in raw_error_file
            or "\\" in raw_error_file
        ):
            raise AgentCapabilityError(
                "The metadata error output must be a filename, not a folder path."
            )
        raw_choice = supplied.get("file_choice")
        if not isinstance(raw_choice, dict):
            raise AgentCapabilityError(
                "Choose one Metadata Import source-file option."
            )
        result: dict[str, Any] = {
            "inbox_file": "",
            "upload_token": "",
            "upload_name": "",
            "error_file_name": raw_error_file,
            "refresh_after_import": refresh_after_import,
            "refresh_job_name": refresh_job_name,
        }
        source = str(raw_choice.get("source") or "").strip().casefold()
        if source == "configured":
            result["file_source"] = "Use file configured in Oracle"
            return result
        if source == "inbox":
            raw_reference = str(
                raw_choice.get("inbox_reference") or ""
            ).strip()
            filename = PurePath(raw_reference.replace("\\", "/")).name
            try:
                MetadataService.validate_inputs(
                    filename,
                    "guided-metadata-import",
                )
            except EPMError as exc:
                raise AgentCapabilityError(str(exc)) from exc
            result["file_source"] = "Existing Oracle Inbox file"
            result["inbox_file"] = filename
            return result
        if source == "upload":
            token = self._safe_pipeline_value(
                raw_choice.get("upload_token"),
                "Metadata Import upload",
                maximum=200,
            )
            filename = self._safe_pipeline_value(
                raw_choice.get("filename"),
                "Metadata Import filename",
                maximum=500,
            )
            if not token or not filename:
                raise AgentCapabilityError("Choose a local metadata file.")
            try:
                MetadataService.validate_inputs(
                    filename,
                    "guided-metadata-import",
                )
            except EPMError as exc:
                raise AgentCapabilityError(str(exc)) from exc
            result["file_source"] = "Upload on governed screen"
            result["upload_token"] = token
            result["upload_name"] = PurePath(filename).name
            return result
        raise AgentCapabilityError(
            "Choose the configured metadata files, an Oracle Inbox file, "
            "or a local upload."
        )

    def _normalize_pipeline_inputs(
        self,
        artifact_name: str,
        supplied: dict[str, Any],
    ) -> dict[str, Any]:
        """Validate Pipeline values against a fresh Oracle preflight."""
        unexpected = set(supplied) - {"runtime_variables", "file_choices"}
        if unexpected:
            raise AgentCapabilityError(
                "Unsupported Pipeline inputs: "
                + ", ".join(sorted(unexpected))
                + "."
            )
        if self._operation_catalog is None:
            raise AgentCapabilityError(
                "Oracle Pipeline inspection is not configured."
            )
        try:
            preview = self._operation_catalog.preflight_pipeline(
                artifact_name
            )
        except EPMError as exc:
            raise AgentCapabilityError(str(exc)) from exc
        raw_variables = supplied.get("runtime_variables") or {}
        raw_choices = supplied.get("file_choices") or {}
        if not isinstance(raw_variables, dict) or not isinstance(
            raw_choices, dict
        ):
            raise AgentCapabilityError(
                "Pipeline variables and file choices must be structured values."
            )
        variables_by_name = {
            variable.name.casefold(): variable for variable in preview.variables
        }
        unknown_variables = {
            str(name)
            for name in raw_variables
            if str(name).casefold() not in variables_by_name
        }
        if unknown_variables:
            raise AgentCapabilityError(
                "Oracle no longer reports these Pipeline variables: "
                + ", ".join(sorted(unknown_variables))
                + "."
            )
        variables: dict[str, str] = {}
        for variable in preview.variables:
            raw_value = next(
                (
                    value
                    for name, value in raw_variables.items()
                    if str(name).casefold() == variable.name.casefold()
                ),
                variable.default_value,
            )
            value = self._safe_pipeline_value(
                raw_value,
                f"Pipeline variable '{variable.display_name}'",
            )
            if variable.required and not value:
                raise AgentCapabilityError(
                    f"Choose a value for {variable.display_name}."
                )
            if value:
                variables[variable.name] = value

        requirements = {
            requirement.key.casefold(): requirement
            for requirement in preview.file_requirements
        }
        unknown_files = {
            str(key)
            for key in raw_choices
            if str(key).casefold() not in requirements
        }
        if unknown_files:
            raise AgentCapabilityError(
                "Oracle no longer reports these Pipeline file inputs: "
                + ", ".join(sorted(unknown_files))
                + "."
            )
        uploads: dict[str, str] = {}
        upload_names: dict[str, str] = {}
        inbox_files: dict[str, str] = {}
        configured_files: dict[str, str] = {}
        for requirement in preview.file_requirements:
            raw_choice = next(
                (
                    choice
                    for key, choice in raw_choices.items()
                    if str(key).casefold() == requirement.key.casefold()
                ),
                None,
            )
            choice = raw_choice if isinstance(raw_choice, dict) else {}
            source = str(choice.get("source") or "").strip().casefold()
            if not source:
                source = (
                    "configured"
                    if requirement.configured_reference
                    else "none"
                )
            if source == "configured":
                if not requirement.configured_reference:
                    raise AgentCapabilityError(
                        f"{requirement.display_name} has no configured Oracle file."
                    )
                configured_files[requirement.key] = (
                    requirement.configured_reference
                )
            elif source == "upload":
                token = self._safe_pipeline_value(
                    choice.get("upload_token"),
                    f"{requirement.display_name} upload",
                    maximum=200,
                )
                filename = self._safe_pipeline_value(
                    choice.get("filename"),
                    f"{requirement.display_name} filename",
                    maximum=500,
                )
                if not token or not filename:
                    raise AgentCapabilityError(
                        f"Choose a local file for {requirement.display_name}."
                    )
                if requirement.allowed_extensions and not any(
                    filename.casefold().endswith(extension.casefold())
                    for extension in requirement.allowed_extensions
                ):
                    raise AgentCapabilityError(
                        f"{requirement.display_name} supports: "
                        + ", ".join(requirement.allowed_extensions)
                        + "."
                    )
                uploads[requirement.key] = token
                upload_names[requirement.key] = filename
            elif source == "inbox":
                reference = self._safe_pipeline_value(
                    choice.get("inbox_reference"),
                    f"{requirement.display_name} Inbox reference",
                    maximum=500,
                )
                if not reference:
                    raise AgentCapabilityError(
                        f"Choose an Oracle Inbox file for {requirement.display_name}."
                    )
                inbox_files[requirement.key] = reference
            elif source == "none":
                if requirement.required:
                    raise AgentCapabilityError(
                        f"{requirement.display_name} is required."
                    )
            else:
                raise AgentCapabilityError(
                    f"Choose a valid file source for {requirement.display_name}."
                )
        return {
            "runtime_variables": variables,
            "uploads": uploads,
            "upload_names": upload_names,
            "inbox_files": inbox_files,
            "configured_files": configured_files,
        }

    @staticmethod
    def _safe_pipeline_value(
        raw_value: Any,
        label: str,
        *,
        maximum: int = 1_000,
    ) -> str:
        value = str(raw_value or "").strip()
        if len(value) > maximum or any(
            ord(character) < 32 for character in value
        ):
            raise AgentCapabilityError(f"{label} contains an invalid value.")
        return value

    def _normalize_pipeline_schedule_inputs(
        self,
        pipeline_code: str,
        supplied: dict[str, Any],
    ) -> dict[str, Any]:
        """Validate a complete unattended Pipeline recurrence live."""
        if self._schedule_coordinator is None:
            raise AgentCapabilityError(
                "Agent scheduling is not configured for this environment."
            )
        allowed = {
            "name",
            # These three values are produced by the first live preview and may
            # be replayed by LangGraph when it builds the approval proposal.
            # They are never trusted: target_key comes from the selected live
            # artifact and the next-run values are recalculated below.
            "target_key",
            "next_run_at",
            "next_run_local",
            "frequency",
            "timezone",
            "first_run_local",
            "input_policy",
            "variables",
            "inbox_files",
            "misfire_policy",
            "enabled",
        }
        unexpected = set(supplied) - allowed
        if unexpected:
            raise AgentCapabilityError(
                "Unsupported schedule inputs: "
                + ", ".join(sorted(unexpected))
                + "."
            )
        name = str(supplied.get("name") or "").strip()
        timezone = str(supplied.get("timezone") or "").strip()
        if not name:
            raise AgentCapabilityError("Enter a business-friendly schedule name.")
        if len(name) > 160:
            raise AgentCapabilityError("Schedule name is too long.")
        if not timezone:
            raise AgentCapabilityError("Choose a schedule timezone.")
        try:
            first_run_local = datetime.fromisoformat(
                str(supplied.get("first_run_local") or "").strip()
            )
        except ValueError as exc:
            raise AgentCapabilityError(
                "Choose a valid first-run date and time."
            ) from exc
        if first_run_local.tzinfo is not None:
            raise AgentCapabilityError(
                "First run must be a local date and time without an offset."
            )
        try:
            frequency = AutomationScheduleFrequency(
                str(supplied.get("frequency") or "")
            )
            input_policy = AutomationInputPolicy(
                str(supplied.get("input_policy") or "")
            )
            misfire_policy = AutomationMisfirePolicy(
                str(supplied.get("misfire_policy") or "RUN_ONCE")
            )
        except ValueError as exc:
            raise AgentCapabilityError(
                "Choose valid recurrence, input, and missed-run policies."
            ) from exc
        if input_policy is AutomationInputPolicy.DYNAMIC:
            raise AgentCapabilityError(
                "Dynamic schedule inputs are not supported. Use Oracle "
                "defaults or fixed unattended values."
            )
        variables = PipelineScheduleTargetAdapter._string_mapping(
            supplied.get("variables"),
            label="Pipeline variables",
        )
        inbox_files = PipelineScheduleTargetAdapter._string_mapping(
            supplied.get("inbox_files"),
            label="Pipeline Inbox files",
        )
        if input_policy is AutomationInputPolicy.ORACLE_DEFAULTS:
            variables = {}
            inbox_files = {}
        schedule_input = AutomationScheduleInput(
            environment_key=self._schedule_environment_key,
            name=name,
            target_type=AutomationTargetType.ORACLE_PIPELINE,
            target_key=pipeline_code,
            frequency=frequency,
            timezone=timezone,
            first_run_local=first_run_local,
            input_policy=input_policy,
            configuration=(
                {"variables": variables, "inbox_files": inbox_files}
                if input_policy is AutomationInputPolicy.FIXED
                else {}
            ),
            concurrency_policy=AutomationConcurrencyPolicy.SKIP_IF_ACTIVE,
            misfire_policy=misfire_policy,
            enabled=bool(supplied.get("enabled", True)),
        )
        try:
            preview = self._schedule_coordinator.preview(schedule_input)
        except EPMError as exc:
            raise AgentCapabilityError(str(exc)) from exc
        return {
            "name": schedule_input.name,
            "target_key": schedule_input.target_key,
            "frequency": schedule_input.frequency.value,
            "timezone": schedule_input.timezone,
            "first_run_local": schedule_input.first_run_local.isoformat(
                timespec="minutes"
            ),
            "input_policy": schedule_input.input_policy.value,
            "variables": variables,
            "inbox_files": inbox_files,
            "misfire_policy": schedule_input.misfire_policy.value,
            "enabled": schedule_input.enabled,
            "next_run_at": preview.next_run_at.isoformat(),
            "next_run_local": preview.next_run_local.isoformat(),
        }

    def _schedule_from_identifier(self, identifier: str):
        if self._schedule_service is None:
            raise AgentCapabilityError(
                "Agent scheduling is not configured for this environment."
            )
        match = re.fullmatch(r"schedule:(\d+)", identifier.strip(), re.IGNORECASE)
        if match is None:
            raise AgentCapabilityError("Choose a current automation schedule.")
        try:
            schedule = self._schedule_service.get(int(match.group(1)))
        except EPMError as exc:
            raise AgentCapabilityError(str(exc)) from exc
        if schedule.environment_key != self._schedule_environment_key:
            raise AgentCapabilityError("The selected schedule is no longer available.")
        return schedule

    def _prepare_schedule_action(
        self,
        arguments: dict[str, Any],
    ) -> dict[str, Any]:
        action = self._required_text(arguments, "action", "Schedule action").upper()
        if action not in {"CREATE", "PAUSE", "RESUME"}:
            raise AgentCapabilityError(
                "Schedule action must be CREATE, PAUSE, or RESUME."
            )
        objective = self._required_text(arguments, "objective", "Objective")
        artifact_name = self._optional_text(arguments.get("artifact_name"))
        target_code = {
            "CREATE": PIPELINE_SCHEDULE_CREATE,
            "PAUSE": PIPELINE_SCHEDULE_PAUSE,
            "RESUME": PIPELINE_SCHEDULE_RESUME,
        }[action]
        input_values: dict[str, Any] = {}
        display_name = {
            "CREATE": "Oracle Pipeline schedule",
            "PAUSE": "Pause schedule",
            "RESUME": "Resume schedule",
        }[action]
        if action in {"PAUSE", "RESUME"} and artifact_name:
            schedule = self._schedule_from_identifier(artifact_name)
            expected_enabled = action == "PAUSE"
            if schedule.enabled is not expected_enabled:
                state = "active" if schedule.enabled else "paused"
                raise AgentCapabilityError(
                    f"Schedule '{schedule.name}' is already {state}."
                )
            input_values = {
                "schedule_id": schedule.schedule_id,
                "schedule_name": schedule.name,
                "target_key": schedule.target_key,
                "current_enabled": schedule.enabled,
                "next_run_at": (
                    schedule.next_run_at.isoformat()
                    if schedule.next_run_at is not None
                    else None
                ),
            }
        elif action == "CREATE":
            raw_values = arguments.get("input_values")
            if isinstance(raw_values, dict) and raw_values:
                if not artifact_name:
                    raise AgentCapabilityError(
                        "Choose a live Oracle Pipeline before configuring its schedule."
                    )
                input_values = self._normalize_pipeline_schedule_inputs(
                    artifact_name,
                    raw_values,
                )
        return {
            "action_draft": {
                "action_type": "schedule",
                "target_code": target_code,
                "display_name": display_name,
                "category": "Automation scheduling",
                "risk_level": "Controlled",
                "route": "/app/schedules",
                "objective": objective,
                "artifact_name": artifact_name,
                "required_inputs": (
                    ["Pipeline", "Recurrence", "Unattended inputs"]
                    if action == "CREATE"
                    else ["Saved schedule"]
                ),
                "stages": [],
                "approval_required": True,
                "status": "PREPARED_NOT_EXECUTED",
                "input_schema": [],
                "input_values": input_values,
            }
        }

    def _prepare_operation_action(
        self, arguments: dict[str, Any]
    ) -> dict[str, Any]:
        operation_code = self._required_text(
            arguments, "operation_code", "Operation code"
        )
        objective = self._required_text(arguments, "objective", "Objective")
        artifact_name = self._optional_text(arguments.get("artifact_name"))
        definition = next(
            (
                item
                for item in OPERATION_DEFINITIONS
                if item.code.casefold() == operation_code.casefold()
            ),
            None,
        )
        if definition is None:
            raise AgentCapabilityError(
                f"Operation '{operation_code}' is not available."
            )
        input_schema = action_input_schema("operation", definition.code)
        raw_input_values = arguments.get("input_values", {})
        input_values = normalize_action_inputs(input_schema, raw_input_values)
        # Artifact selection happens before the guided RTP form is shown. The
        # action schema supplies an empty runtime_prompts mapping by default,
        # so validating that default here would reject every registered rule
        # with a required RTP before the user has a chance to enter it. Enforce
        # the registry contract only after the guided form explicitly submits
        # runtime_prompts; final execution validates the contract again.
        runtime_prompts_submitted = (
            isinstance(raw_input_values, dict)
            and "runtime_prompts" in raw_input_values
        )
        if (
            definition.code == "business-rules"
            and artifact_name
            and runtime_prompts_submitted
        ):
            raw_prompts = input_values.get("runtime_prompts", {})
            if not isinstance(raw_prompts, dict):
                raise AgentCapabilityError(
                    "Business Rule runtime prompts must contain name and value pairs."
                )
            input_values["runtime_prompts"] = (
                self.normalize_business_rule_runtime_prompts(
                    artifact_name,
                    raw_prompts,
                )
            )
        return {
            "action_draft": {
                "action_type": "operation",
                "target_code": definition.code,
                "display_name": definition.display_name,
                "category": definition.category,
                "risk_level": definition.risk_level,
                "route": definition.route,
                "objective": objective,
                "artifact_name": artifact_name,
                "required_inputs": list(
                    self._REQUIRED_INPUTS.get(definition.code, ())
                ),
                "stages": [],
                "approval_required": definition.risk_level != "Read only",
                "status": "PREPARED_NOT_EXECUTED",
                "input_schema": action_input_schema_payload(input_schema),
                "input_values": input_values,
            }
        }

    @staticmethod
    def _required_text(
        arguments: dict[str, Any], key: str, label: str
    ) -> str:
        value = str(arguments.get(key) or "").strip()
        if not value:
            raise AgentCapabilityError(f"{label} is required.")
        if len(value) > 500:
            raise AgentCapabilityError(f"{label} is too long.")
        return value

    @staticmethod
    def _optional_text(value: Any) -> str | None:
        normalized = str(value or "").strip()
        if len(normalized) > 250:
            raise AgentCapabilityError("Artifact name is too long.")
        return normalized or None
