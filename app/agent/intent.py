"""Deterministic intent routing for least-privilege agent tool exposure."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class AgentIntent(StrEnum):
    """Stable user-intent categories used before model invocation."""

    PLATFORM_DISCOVERY = "PLATFORM_DISCOVERY"
    HISTORY_REVIEW = "HISTORY_REVIEW"
    DATA_REVIEW = "DATA_REVIEW"
    OPERATION_PREPARATION = "OPERATION_PREPARATION"
    SCHEDULING = "SCHEDULING"
    GENERAL_GUIDANCE = "GENERAL_GUIDANCE"


@dataclass(frozen=True, slots=True)
class AgentIntentDecision:
    """Classified intent and the resulting permitted capability subset."""

    intent: AgentIntent
    tool_names: frozenset[str]


class AgentIntentRouter:
    """Constrain model tools using conservative, explainable text matching."""

    _BASE = frozenset(
        {"get_environment_summary", "list_platform_operations"}
    )
    _HISTORY_TERMS = (
        "history",
        "recent run",
        "recent execution",
        "last run",
        "failed run",
        "completed run",
        "status of",
        "job status",
        "execution status",
        "why did",
        "why has",
        "job error",
        "execution error",
        "failed step",
        "records read",
        "records processed",
        "records rejected",
        "load statistics",
    )
    _DATA_TERMS = (
        "cube",
        "slice",
        "data review",
        "data explorer",
        "saved view",
        "show data",
        "planning data",
        "data for",
        "validate data",
        "validation",
        "reconcile",
        "compare data",
        "source and target",
        "actual vs",
        "forecast vs",
    )
    _PREPARATION_TERMS = (
        "prepare",
        "run",
        "execute",
        "import",
        "load",
        "refresh",
        "calculate",
        "push",
        "generate",
        "update",
        "set",
        "change",
        "assign",
        "create",
    )
    _DATA_REFINEMENT_TERMS = (
        "change",
        "replace",
        "add",
        "remove",
        "instead",
        "swap",
        "put",
        "show",
        "compare",
        "actual",
        "forecast",
        "year",
        "period",
        "month",
        "pov",
        "row",
        "column",
        "member",
        "this",
        "previous",
        "same",
        "export",
    )
    _EXPLICIT_OPERATION_TERMS = (
        "business rule",
        "pipeline",
        "data integration",
        "data import",
        "metadata import",
        "data map",
        "smart push",
        "cube refresh",
        "substitution variable",
        "user variable",
        "planning job",
    )
    _SCHEDULE_TERMS = (
        "schedule",
        "scheduled",
        "recurrence",
        "recurring",
        "every day",
        "every week",
        "every month",
        "pause automation",
        "resume automation",
    )

    @classmethod
    def route(
        cls,
        prompt: str,
        permitted_tool_names: frozenset[str],
        *,
        has_data_review_context: bool = False,
        task_intent: str | None = None,
    ) -> AgentIntentDecision:
        """Return the smallest useful permitted tool set for this prompt."""
        normalized = " ".join(str(prompt).casefold().split())
        selected = set(cls._BASE)
        matches: list[AgentIntent] = []
        explicit_operation = any(
            term in normalized for term in cls._EXPLICIT_OPERATION_TERMS
        ) or str(task_intent or "").strip().upper() in {
            "RUN_BUSINESS_RULE",
            "FORECAST_SEEDING",
        }
        contextual_refinement = (
            has_data_review_context
            and not explicit_operation
            and any(
                term in normalized for term in cls._DATA_REFINEMENT_TERMS
            )
        )
        history_match = any(
            term in normalized for term in cls._HISTORY_TERMS
        )
        schedule_match = any(
            term in normalized for term in cls._SCHEDULE_TERMS
        )
        active_execution_task = str(task_intent or "").strip().upper() in {
            "MONTH_CLOSE",
            "METADATA_LOAD",
            "DATA_LOAD",
            "FORECAST_SEEDING",
            "VARIANCE_REPORTING",
            "RUN_BUSINESS_RULE",
            "RUN_DATA_INTEGRATION",
            "RUN_PIPELINE",
        }
        if schedule_match:
            selected.add("prepare_schedule_action")
            matches.append(AgentIntent.SCHEDULING)
        if history_match:
            selected.add("get_recent_execution_history")
            selected.add("get_execution_evidence")
            matches.append(AgentIntent.HISTORY_REVIEW)
        if contextual_refinement or any(
            term in normalized for term in cls._DATA_TERMS
        ):
            selected.update(
                {
                    "list_planning_cubes",
                    "list_cube_dimensions",
                    "search_dimension_members",
                    "list_data_explorer_views",
                    "list_variance_views",
                    "review_saved_data_view",
                    "review_saved_variance",
                    "review_data_slice",
                    "compare_data_slices",
                }
            )
            matches.append(AgentIntent.DATA_REVIEW)
        if (explicit_operation and not schedule_match) or (
            not contextual_refinement
            and not history_match
            and not schedule_match
            and (
                any(term in normalized for term in cls._PREPARATION_TERMS)
                or active_execution_task
            )
        ):
            selected.update(
                {
                    "list_operation_artifacts",
                    "plan_multi_step_request",
                    "prepare_operation_action",
                    "prepare_standalone_flow_action",
                }
            )
            matches.append(AgentIntent.OPERATION_PREPARATION)
        if not matches:
            return AgentIntentDecision(
                intent=AgentIntent.GENERAL_GUIDANCE,
                # General questions need only the two lightweight discovery
                # tools. Sending every operation schema wastes provider TPM
                # and exposes capabilities unrelated to the current request.
                tool_names=cls._BASE & permitted_tool_names,
            )
        intent = (
            matches[0]
            if len(matches) == 1
            else AgentIntent.PLATFORM_DISCOVERY
        )
        return AgentIntentDecision(
            intent=intent,
            tool_names=frozenset(selected) & permitted_tool_names,
        )
